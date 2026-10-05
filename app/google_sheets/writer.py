"""Idempotent row appending and batch update operations for Google Sheets."""

import logging
from typing import Any, List, NamedTuple, Set
from app.google_sheets.client import GoogleSheetsService

logger = logging.getLogger("pothys_reporting")


class AppendResult(NamedTuple):
    """Result of an idempotent sheet append operation."""
    total_candidates: int
    appended_count: int
    skipped_duplicates_count: int


class SheetWriter:
    """Manages appending and updating Google Sheet tabs idempotently."""

    def __init__(self, service: GoogleSheetsService):
        self.service = service

    def append_rows_idempotently(
        self,
        spreadsheet_id: str,
        tab_name: str,
        rows: List[List[Any]],
        key_column_index: int = 3,  # Column D (0-indexed 3) is MSNO
    ) -> AppendResult:
        """
        Append rows only if their deduplication key does not already exist in the target sheet.

        Args:
            spreadsheet_id: Target Google Sheet workbook ID.
            tab_name: Month tab name (e.g. 'SS - Oct').
            rows: List of row value lists (Columns A-N).
            key_column_index: Index of the unique business key (default 3 for MSNO).

        Returns:
            AppendResult with appended count and skipped count.
        """
        if not rows:
            return AppendResult(total_candidates=0, appended_count=0, skipped_duplicates_count=0)

        # 1. Fetch existing keys from sheet
        col_letter = chr(ord("A") + key_column_index)
        existing_keys = self.service.get_existing_column_values(
            spreadsheet_id, tab_name, col_letter
        )

        # 2. Filter candidate rows
        to_append: List[List[Any]] = []
        skipped = 0

        for row in rows:
            if len(row) > key_column_index:
                key = str(row[key_column_index]).strip()
                if key and key in existing_keys:
                    skipped += 1
                    continue
            to_append.append(row)

        # 3. Append unique rows
        appended = 0
        if to_append:
            appended = self.service.append_rows(spreadsheet_id, f"{tab_name}!A:N", to_append)
            logger.info(
                f"Appended {appended} rows to '{tab_name}' (skipped {skipped} existing duplicate keys)."
            )
        else:
            logger.info(
                f"No new rows to append to '{tab_name}'. All {len(rows)} rows already exist."
            )

        return AppendResult(
            total_candidates=len(rows),
            appended_count=appended,
            skipped_duplicates_count=skipped,
        )
