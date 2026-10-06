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

    def append_new_records(
        self,
        spreadsheet_id: str,
        tab_name: str,
        reconciliation_result: Any,  # SheetReconciliationResult
        records: List[Any],          # List[CleanedRecord]
        dry_run: bool = False,
        verify_after_write: bool = True,
    ) -> AppendResult:
        """
        Safely append ONLY genuinely NEW records to target monthly sheet.

        Guarantees:
        - Fails closed if reconciliation had any conflicts or API errors.
        - Appends strictly records classified as NEW.
        - Skips all records classified as ALREADY_PRESENT.
        - NEVER overwrites existing data rows (appends strictly below last row).
        - NEVER writes to formula columns H (LOCATION) or N (CODE - NAME):
          writes Columns A:G (7 cols) and Columns I:M (5 cols), leaving Col H & N
          completely untouched so that array formulas from H2 and N2 expand naturally.
        - In dry_run mode: performs zero Google Sheet writes and returns candidate metrics.
        - Performs post-write read-back verification when verify_after_write is True.

        Args:
            spreadsheet_id: Target Google Sheet workbook ID.
            tab_name: Month tab name (e.g. 'SS - Oct' or 'SV - Oct').
            reconciliation_result: Result from LiveSheetReconciler.
            records: List of cleaned records.
            dry_run: If True, simulate and do not execute Google Sheet writes.
            verify_after_write: If True, read back newly written rows to verify data integrity.

        Returns:
            AppendResult with total_candidates, appended_count, skipped_duplicates_count.
        """
        from app.core.exceptions import GoogleSheetsError
        from app.google_sheets.reconciliation import (
            RecordReconciliationCategory,
            normalize_text,
        )

        # 1. Fail closed if reconciliation failed
        if reconciliation_result.status.startswith("FAILED"):
            raise GoogleSheetsError(
                f"Cannot append to '{tab_name}': Reconciliation failed with status '{reconciliation_result.status}'. "
                f"Error: {reconciliation_result.error_message}"
            )

        # 2. Fail closed if any conflicts detected
        if reconciliation_result.conflict_count > 0:
            raise GoogleSheetsError(
                f"Cannot append to '{tab_name}': {reconciliation_result.conflict_count} conflict(s) detected. "
                "Resolve data conflicts before writing."
            )

        # 3. Check if any new records exist
        if reconciliation_result.new_record_count == 0:
            logger.info(
                f"No new records to append to '{tab_name}'. All {reconciliation_result.incoming_record_count} "
                "records are already present."
            )
            return AppendResult(
                total_candidates=reconciliation_result.incoming_record_count,
                appended_count=0,
                skipped_duplicates_count=reconciliation_result.already_present_count,
            )

        # 4. Extract genuinely NEW records
        new_msnos = {
            d.msno for d in reconciliation_result.details
            if d.category == RecordReconciliationCategory.NEW
        }
        records_to_append = [
            r for r in records
            if normalize_text(r.MSNO).upper() in new_msnos
        ]

        if not records_to_append:
            return AppendResult(
                total_candidates=reconciliation_result.incoming_record_count,
                appended_count=0,
                skipped_duplicates_count=reconciliation_result.already_present_count,
            )

        # 5. Format payload blocks excluding formula columns H and N:
        # Part 1: Columns A through G (COSTNAME, CLIENTID, GROUPCODE, MSNO, NAME, SCHDATE, LOCATION_2)
        # Part 2: Columns I through M (RECAMOUNT, MOBILENO, SCHEME, COMMNAME, COMMCODE)
        rows_ag: List[List[Any]] = []
        rows_im: List[List[Any]] = []

        for r in records_to_append:
            rows_ag.append([
                r.COSTNAME or "",
                r.CLIENTID or "",
                r.GROUPCODE or "",
                r.MSNO or "",
                r.NAME or "",
                r.SCHDATE or "",
                r.LOCATION_2 or "",
            ])
            rows_im.append([
                r.RECAMOUNT if r.RECAMOUNT is not None else 0.0,
                r.MOBILENO or "",
                r.SCHEME or "",
                r.COMMNAME or "",
                r.COMMCODE or "",
            ])

        # 6. Dry run check: zero writes guaranteed
        if dry_run:
            logger.info(
                f"[DRY_RUN] Would append {len(records_to_append)} rows to '{tab_name}'. "
                "Formula columns H and N are excluded. Zero writes performed."
            )
            return AppendResult(
                total_candidates=reconciliation_result.incoming_record_count,
                appended_count=len(records_to_append),
                skipped_duplicates_count=reconciliation_result.already_present_count,
            )

        # 7. Live execution: Determine append row boundary
        existing_rows = self.service.read_range(spreadsheet_id, f"{tab_name}!D:D")
        start_row = len(existing_rows) + 1
        end_row = start_row + len(records_to_append) - 1

        # Ensure sheet grid dimension has capacity to hold appended records
        self.service.ensure_grid_capacity(spreadsheet_id, tab_name, end_row)

        range_ag = f"{tab_name}!A{start_row}:G{end_row}"
        range_im = f"{tab_name}!I{start_row}:M{end_row}"

        self.service.update_range(spreadsheet_id, range_ag, rows_ag)
        self.service.update_range(spreadsheet_id, range_im, rows_im)

        logger.info(
            f"Successfully appended {len(records_to_append)} new records to '{tab_name}' "
            f"at rows {start_row}:{end_row} (columns A:G and I:M; formula columns H and N untouched)."
        )

        # 8. Post-write verification
        if verify_after_write:
            verification_rows = self.service.read_range(spreadsheet_id, f"{tab_name}!A{start_row}:M{end_row}")
            if len(verification_rows) != len(records_to_append):
                raise GoogleSheetsError(
                    f"Post-write verification failed: expected {len(records_to_append)} appended rows, "
                    f"but read back {len(verification_rows)} rows from '{tab_name}'."
                )
            written_msnos = [normalize_text(r[3]).upper() for r in verification_rows if len(r) > 3]
            expected_msnos = [normalize_text(r.MSNO).upper() for r in records_to_append]
            if written_msnos != expected_msnos:
                raise GoogleSheetsError(
                    f"Post-write verification failed: appended MSNOs mismatch in '{tab_name}'. "
                    f"Expected {expected_msnos[:3]}, got {written_msnos[:3]}"
                )

            # Confirm formula columns H2 and N2 are intact
            h2_formulas = self.service.read_formulas(spreadsheet_id, f"{tab_name}!H2:H2")
            n2_formulas = self.service.read_formulas(spreadsheet_id, f"{tab_name}!N2:N2")
            if not h2_formulas or not h2_formulas[0] or not str(h2_formulas[0][0]).startswith("="):
                raise GoogleSheetsError(
                    f"Post-write verification failed: H2 array formula missing or altered in '{tab_name}'."
                )
            if not n2_formulas or not n2_formulas[0] or not str(n2_formulas[0][0]).startswith("="):
                raise GoogleSheetsError(
                    f"Post-write verification failed: N2 array formula missing or altered in '{tab_name}'."
                )

            logger.info(
                f"Post-write verification PASSED: {len(written_msnos)} MSNOs verified, formula columns H and N intact."
            )

        return AppendResult(
            total_candidates=reconciliation_result.incoming_record_count,
            appended_count=len(records_to_append),
            skipped_duplicates_count=reconciliation_result.already_present_count,
        )
