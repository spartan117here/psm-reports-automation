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
    "https://www.googleapis.com/auth/drive.readonly",
]
READ_WRITE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
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
    def clear_range(self, spreadsheet_id: str, range_name: str) -> bool:
        """Clear cell contents in a specific range."""
        pass

    @abstractmethod
    def get_existing_column_values(
        self, spreadsheet_id: str, tab_name: str, column_letter: str
    ) -> Set[str]:
        """Fetch all existing non-empty values from a column for idempotency deduplication."""
        pass

    @abstractmethod
    def ensure_grid_capacity(self, spreadsheet_id: str, tab_name: str, required_rows: int) -> None:
        """Ensure worksheet grid dimension has at least required_rows capacity."""
        pass

    @abstractmethod
    def export_pdf(
        self, spreadsheet_id: str, gid: Any, range_name: Optional[str] = None
    ) -> bytes:
        """Export a sheet or cell range to PDF bytes."""
        pass


class MockGoogleSheetsService(GoogleSheetsService):
    """In-memory mock service for testing and offline execution."""

    def __init__(self, metadata: Optional[Dict[str, Any]] = None):
        self.sheets: Dict[str, List[List[Any]]] = {}
        self.formulas: Dict[str, List[List[Any]]] = {}
        self.mock_pdfs: Dict[str, bytes] = {}
        self.metadata_by_id: Dict[str, Dict[str, Any]] = {}
        self.metadata = metadata or {
            "properties": {"title": "Mock Spreadsheet"},
            "sheets": [],
        }

    def set_metadata(self, metadata: Dict[str, Any], spreadsheet_id: Optional[str] = None) -> None:
        self.metadata = metadata
        if spreadsheet_id:
            self.metadata_by_id[spreadsheet_id] = metadata

    def get_spreadsheet_metadata(self, spreadsheet_id: str) -> Dict[str, Any]:
        if spreadsheet_id in self.metadata_by_id:
            return self.metadata_by_id[spreadsheet_id]
        return self.metadata

    def set_mock_pdf(self, key: str, pdf_bytes: bytes) -> None:
        """Set custom mock PDF bytes for a given key or range."""
        self.mock_pdfs[key] = pdf_bytes

    def export_pdf(
        self, spreadsheet_id: str, gid: Any, range_name: Optional[str] = None
    ) -> bytes:
        """Export mock PDF bytes or generate synthetic PDF."""
        key = f"{spreadsheet_id}:{gid}:{range_name}"
        if key in self.mock_pdfs:
            return self.mock_pdfs[key]
        sheet_key = f"{spreadsheet_id}:{gid}"
        if sheet_key in self.mock_pdfs:
            return self.mock_pdfs[sheet_key]
        if range_name and range_name in self.mock_pdfs:
            return self.mock_pdfs[range_name]

        # Generate synthetic valid PDF using PyMuPDF for offline tests
        try:
            import pymupdf
            doc = pymupdf.open()
            page = doc.new_page(width=612, height=792)
            content = f"Mock Sheet (gid: {gid})\nRange: {range_name or 'Full Sheet'}\n"
            # Add any mocked rows
            for k, rows in self.sheets.items():
                if range_name and (range_name in k or str(gid) in k or "October" in k or "Consolidate" in k):
                    for r in rows:
                        content += " ".join(str(cell) for cell in r) + "\n"
            page.insert_text((72, 72), content)
            return doc.tobytes()
        except Exception as e:
            logger.warning(f"Failed to generate synthetic PDF: {e}")
            return b"%PDF-1.4 mock pdf content"

    def read_range(
        self, spreadsheet_id: str, range_name: str, value_render_option: str = "FORMATTED_VALUE"
    ) -> List[List[Any]]:
        if value_render_option == "FORMULA":
            key = f"{spreadsheet_id}:{range_name}"
            if key in self.formulas:
                return self.formulas[key]
        direct = self.sheets.get(f"{spreadsheet_id}:{range_name}")
        if direct is not None:
            return direct
        # Fallback 1: check with/without single quotes
        alt_range = range_name.replace("'", "") if "'" in range_name else f"'{range_name}'"
        alt_key = f"{spreadsheet_id}:{alt_range}"
        if alt_key in self.sheets:
            return self.sheets[alt_key]
        # Fallback 2: match worksheet prefix if only one range is stored for that worksheet
        clean_tab = range_name.split("!")[0].replace("'", "")
        for k, v in self.sheets.items():
            if k.startswith(f"{spreadsheet_id}:") and clean_tab in k.split("!")[0]:
                return v
        return []

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
        self.formulas[key] = values
        return True

    def clear_range(self, spreadsheet_id: str, range_name: str) -> bool:
        key = f"{spreadsheet_id}:{range_name}"
        self.sheets[key] = []
        self.formulas[key] = []
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

    def ensure_grid_capacity(self, spreadsheet_id: str, tab_name: str, required_rows: int) -> None:
        pass


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

        self.credentials = credentials
        try:
            return build("sheets", "v4", credentials=credentials, cache_discovery=False)
        except Exception as e:
            raise GoogleSheetsError(f"Failed to initialize Google Sheets API client: {e}") from e

    def export_pdf(
        self, spreadsheet_id: str, gid: Any, range_name: Optional[str] = None
    ) -> bytes:
        """Export a Google Sheets range or worksheet to PDF bytes."""
        if not self.credentials:
            raise GoogleSheetsError("No credentials available for PDF export.")

        from google.auth.transport.requests import AuthorizedSession
        import urllib.parse

        session = AuthorizedSession(self.credentials)
        url = (
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export"
            f"?format=pdf&gid={gid}"
            f"&size=letter&portrait=true&fitw=true&gridlines=true"
            f"&printtitle=false&sheetnames=false&fzr=false"
        )
        if range_name:
            url += f"&range={urllib.parse.quote(range_name)}"

        try:
            resp = session.get(url, timeout=60)
            if resp.status_code != 200:
                raise GoogleSheetsError(
                    f"Google Sheets PDF export failed with HTTP {resp.status_code}: {resp.text[:200]}"
                )
            if "application/pdf" not in resp.headers.get("content-type", ""):
                raise GoogleSheetsError(
                    f"Unexpected content type from PDF export: {resp.headers.get('content-type')}"
                )
            return resp.content
        except GoogleSheetsError:
            raise
        except Exception as e:
            raise GoogleSheetsError(f"Failed to export Google Sheet to PDF: {e}") from e

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

    def clear_range(self, spreadsheet_id: str, range_name: str) -> bool:
        """Clear cell contents from a specific range."""
        if self.read_only:
            raise GoogleSheetsError("Cannot clear cells: service is initialized in READ-ONLY mode.")
        try:
            self.service.spreadsheets().values().clear(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                body={},
            ).execute()
            return True
        except Exception as e:
            raise GoogleSheetsError(
                f"Failed to clear range '{range_name}' in spreadsheet '{spreadsheet_id}': {e}"
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

    def ensure_grid_capacity(self, spreadsheet_id: str, tab_name: str, required_rows: int) -> None:
        """Ensure worksheet grid dimension has at least required_rows capacity."""
        if self.read_only:
            return
        meta = self.get_spreadsheet_metadata(spreadsheet_id)
        sheet_meta = None
        for s in meta.get("sheets", []):
            if s.get("properties", {}).get("title") == tab_name:
                sheet_meta = s.get("properties", {})
                break
        if not sheet_meta:
            return
        current_rows = sheet_meta.get("gridProperties", {}).get("rowCount", 0)
        if current_rows < required_rows:
            rows_to_add = required_rows - current_rows
            sheet_id = sheet_meta.get("sheetId")
            body = {
                "requests": [
                    {
                        "appendDimension": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "length": rows_to_add,
                        }
                    }
                ]
            }
            try:
                self.service.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id, body=body
                ).execute()
                logger.info(
                    f"Expanded grid capacity for '{tab_name}' from {current_rows} to {required_rows} rows."
                )
            except Exception as e:
                raise GoogleSheetsError(
                    f"Failed to expand grid rows for '{tab_name}' in spreadsheet '{spreadsheet_id}': {e}"
                ) from e
