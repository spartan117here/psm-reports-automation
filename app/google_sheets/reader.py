"""Google Sheet reader operations and schema validators."""

from typing import Any, List, Set
from app.google_sheets.client import GoogleSheetsService


class SheetReader:
    """Reads structured tables and deduplication indices from Google Sheets."""

    def __init__(self, service: GoogleSheetsService):
        self.service = service

    def get_existing_keys(
        self, spreadsheet_id: str, tab_name: str, key_col_letter: str = "D"
    ) -> Set[str]:
        """Retrieve existing keys (e.g., MSNO in Column D) to prevent duplicate appends."""
        return self.service.get_existing_column_values(spreadsheet_id, tab_name, key_col_letter)
