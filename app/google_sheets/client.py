"""Abstract Google Sheets service interface."""

from abc import ABC, abstractmethod
from typing import Any, List, Optional, Set
import logging

logger = logging.getLogger("pothys_reporting")


class GoogleSheetsService(ABC):
    """
    Abstract interface for Google Sheets operations.

    Decouples business logic from specific third-party client libraries
    (gspread, google-api-python-client) and enables straightforward testing.
    """

    @abstractmethod
    def read_range(self, spreadsheet_id: str, range_name: str) -> List[List[Any]]:
        """Read a 2D range of cell values."""
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
    """In-memory mock service for Phase 0 testing and local offline execution."""

    def __init__(self):
        self.sheets: dict = {}

    def read_range(self, spreadsheet_id: str, range_name: str) -> List[List[Any]]:
        return self.sheets.get(f"{spreadsheet_id}:{range_name}", [])

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
        # Mock returns empty set initially
        return set()
