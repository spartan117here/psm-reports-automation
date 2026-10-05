"""Google Sheets service interface and production API client."""

from abc import ABC, abstractmethod
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from app.core.exceptions import GoogleAuthenticationError, GoogleSheetsError

logger = logging.getLogger("pothys_reporting")

# Default Google Sheets API Scopes
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]
READ_WRITE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]


class GoogleSheetsService(ABC):
    """
    Abstract interface for Google Sheets operations.

    Decouples business logic from specific third-party client libraries
    and enables straightforward testing and offline verification.
    """

    @abstractmethod
    def get_spreadsheet_metadata(self, spreadsheet_id: str) -> Dict[str, Any]:
        """Fetch workbook metadata including sheets, titles, and dimensions."""
        pass

    @abstractmethod
    def read_range(
        self, spreadsheet_id: str, range_name: str, value_render_option: str = "FORMATTED_VALUE"
    ) -> List[List[Any]]:
        """Read a 2D range of cell values."""
        pass

    @abstractmethod
    def read_formulas(self, spreadsheet_id: str, range_name: str) -> List[List[Any]]:
        """Read a range with formula expressions instead of calculated values."""
        pass

    @abstractmethod
    def append_rows(self, spreadsheet_id: str, range_name: str, values: List[List[Any]]) -> int:
        """Append rows to a target sheet/range."""
        pass

    @abstractmethod
    def update_range(self, spreadsheet_id: str, range_name: str, values: List[List[Any]]) -> bool:
        """Update a specific range of cells."""
        pass

    @abstractmethod
    def get_existing_column_values(
        self, spreadsheet_id: str, tab_name: str, column_letter: str
    ) -> Set[str]:
        """Fetch all existing non-empty values from a column for idempotency deduplication."""
        pass


class MockGoogleSheetsService(GoogleSheetsService):
    """In-memory mock service for testing and offline execution."""

    def __init__(self, metadata: Optional[Dict[str, Any]] = None):
        self.sheets: Dict[str, List[List[Any]]] = {}
        self.formulas: Dict[str, List[List[Any]]] = {}
        self.metadata = metadata or {
            "properties": {"title": "Mock Spreadsheet"},
            "sheets": [],
        }

    def set_metadata(self, metadata: Dict[str, Any]) -> None:
        self.metadata = metadata

    def get_spreadsheet_metadata(self, spreadsheet_id: str) -> Dict[str, Any]:
        return self.metadata

    def read_range(
        self, spreadsheet_id: str, range_name: str, value_render_option: str = "FORMATTED_VALUE"
    ) -> List[List[Any]]:
        if value_render_option == "FORMULA":
            key = f"{spreadsheet_id}:{range_name}"
            if key in self.formulas:
                return self.formulas[key]
        return self.sheets.get(f"{spreadsheet_id}:{range_name}", [])

    def read_formulas(self, spreadsheet_id: str, range_name: str) -> List[List[Any]]:
        return self.read_range(spreadsheet_id, range_name, value_render_option="FORMULA")

    def append_rows(self, spreadsheet_id: str, range_name: str, values: List[List[Any]]) -> int:
        key = f"{spreadsheet_id}:{range_name}"
        if key not in self.sheets:
            self.sheets[key] = []
        self.sheets[key].extend(values)
        return len(values)

    def update_range(self, spreadsheet_id: str, range_name: str, values: List[List[Any]]) -> bool:
        key = f"{spreadsheet_id}:{range_name}"
        self.sheets[key] = values
        return True

    def get_existing_column_values(
        self, spreadsheet_id: str, tab_name: str, column_letter: str
    ) -> Set[str]:
        target_range = f"{tab_name}!{column_letter}2:{column_letter}"
        rows = self.read_range("MOCK_SPREADSHEET", target_range)
        values = set()
        for r in rows:
            if r and str(r[0]).strip():
                values.add(str(r[0]).strip())
        return values


class GoogleApiSheetsService(GoogleSheetsService):
    """
    Production Google Sheets service client using Google APIs client library.

    Supports authentication via:
    1. Service Account JSON file path (GOOGLE_SERVICE_ACCOUNT_PATH or GOOGLE_APPLICATION_CREDENTIALS)
    2. Service Account JSON string in environment variable (GOOGLE_SERVICE_ACCOUNT_JSON)
    3. Application Default Credentials (ADC)
    """

    def __init__(
        self,
        service_account_path: Optional[str] = None,
        service_account_json: Optional[str] = None,
        read_only: bool = True,
    ):
        self.read_only = read_only
        self.scopes = DEFAULT_SCOPES if read_only else READ_WRITE_SCOPES
        self.service = self._init_service(service_account_path, service_account_json)

    def _init_service(
        self,
        service_account_path: Optional[str],
        service_account_json: Optional[str],
    ) -> Any:
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as e:
            raise GoogleSheetsError(
                f"Missing Google client dependencies: {e}. Please ensure google-api-python-client is installed."
            ) from e

        credentials = None

        # 1. Direct path or environment variable for path
        sa_path = (
            service_account_path
            or os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH")
            or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        )

        if sa_path and Path(sa_path).is_file():
            logger.info(f"Authenticating to Google APIs via service account file: {sa_path}")
            try:
                credentials = service_account.Credentials.from_service_account_file(
                    sa_path, scopes=self.scopes
                )
            except Exception as e:
                raise GoogleAuthenticationError(f"Failed to load service account file from '{sa_path}': {e}") from e

        # 2. Service account JSON string in environment variable
        elif service_account_json or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"):
            raw_json = service_account_json or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
            try:
                info = json.loads(raw_json)  # type: ignore[arg-type]
                credentials = service_account.Credentials.from_service_account_info(
                    info, scopes=self.scopes
                )
            except Exception as e:
                raise GoogleAuthenticationError(f"Failed to parse GOOGLE_SERVICE_ACCOUNT_JSON: {e}") from e

        # 3. Application Default Credentials fallback
        else:
            try:
                import google.auth
                credentials, _ = google.auth.default(scopes=self.scopes)
                logger.info("Authenticating to Google APIs via Application Default Credentials (ADC).")
            except Exception as e:
                raise GoogleAuthenticationError(
                    "Google Sheets credentials not configured. "
                    "Please provide a Service Account JSON via GOOGLE_APPLICATION_CREDENTIALS, "
                    "GOOGLE_SERVICE_ACCOUNT_PATH, or GOOGLE_SERVICE_ACCOUNT_JSON."
                ) from e

        try:
            return build("sheets", "v4", credentials=credentials, cache_discovery=False)
        except Exception as e:
            raise GoogleSheetsError(f"Failed to initialize Google Sheets API client: {e}") from e

    def get_spreadsheet_metadata(self, spreadsheet_id: str) -> Dict[str, Any]:
        """Fetch workbook metadata including sheets, titles, and dimensions."""
        try:
            result = (
                self.service.spreadsheets()
                .get(spreadsheetId=spreadsheet_id, includeGridData=False)
                .execute()
            )
            return result
        except Exception as e:
            raise GoogleSheetsError(f"Failed to fetch metadata for spreadsheet '{spreadsheet_id}': {e}") from e

    def read_range(
        self, spreadsheet_id: str, range_name: str, value_render_option: str = "FORMATTED_VALUE"
    ) -> List[List[Any]]:
        """Read a 2D range of cell values."""
        try:
            result = (
                self.service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                    valueRenderOption=value_render_option,
                )
                .execute()
            )
            return result.get("values", [])
        except Exception as e:
            raise GoogleSheetsError(
                f"Failed to read range '{range_name}' from spreadsheet '{spreadsheet_id}': {e}"
            ) from e

    def read_formulas(self, spreadsheet_id: str, range_name: str) -> List[List[Any]]:
        """Read range with un-evaluated formula expressions."""
        return self.read_range(spreadsheet_id, range_name, value_render_option="FORMULA")

    def append_rows(self, spreadsheet_id: str, range_name: str, values: List[List[Any]]) -> int:
        """Append rows to a target sheet/range."""
        if self.read_only:
            raise GoogleSheetsError("Cannot append rows: service is initialized in READ-ONLY mode.")
        try:
            body = {"values": values}
            result = (
                self.service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body=body,
                )
                .execute()
            )
            updates = result.get("updates", {})
            return updates.get("updatedRows", len(values))
        except Exception as e:
            raise GoogleSheetsError(
                f"Failed to append rows to '{range_name}' in spreadsheet '{spreadsheet_id}': {e}"
            ) from e

    def update_range(self, spreadsheet_id: str, range_name: str, values: List[List[Any]]) -> bool:
        """Update a specific range of cells."""
        if self.read_only:
            raise GoogleSheetsError("Cannot update cells: service is initialized in READ-ONLY mode.")
        try:
            body = {"values": values}
            self.service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body=body,
            ).execute()
            return True
        except Exception as e:
            raise GoogleSheetsError(
                f"Failed to update range '{range_name}' in spreadsheet '{spreadsheet_id}': {e}"
            ) from e

    def get_existing_column_values(
        self, spreadsheet_id: str, tab_name: str, column_letter: str
    ) -> Set[str]:
        """Fetch all existing non-empty values from a column."""
        target_range = f"{tab_name}!{column_letter}2:{column_letter}"
        rows = self.read_range(spreadsheet_id, target_range)
        values = set()
        for r in rows:
            if r and str(r[0]).strip():
                values.add(str(r[0]).strip())
        return values
