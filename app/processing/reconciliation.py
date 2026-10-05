"""Reconciliation utilities for validating source data integrity against processed output."""

from typing import Any, Dict, List, NamedTuple
from app.core.models import CleanedRecord, RawReportPayload


class ReconciliationResult(NamedTuple):
    """Outcome of reconciling raw source data with cleaned/processed data."""
    is_matched: bool
    source_row_count: int
    processed_row_count: int
    source_amount_total: float
    processed_amount_total: float
    amount_discrepancy: float
    message: str


class DataReconciler:
    """Performs mathematical and count reconciliations across pipeline stages."""

    @staticmethod
    def reconcile_raw_vs_cleaned(
        raw_payload: RawReportPayload, cleaned_records: List[CleanedRecord]
    ) -> ReconciliationResult:
        """
        Verify that record counts and total RECAMOUNT in raw data perfectly match cleaned records.
        """
        source_count = raw_payload.row_count
        processed_count = len(cleaned_records)

        # Sum amounts from raw data
        raw_total = 0.0
        for row in raw_payload.data:
            val = row.get("RECAMOUNT", 0.0)
            try:
                raw_total += float(str(val).replace(",", "").strip() or 0.0)
            except (ValueError, TypeError):
                pass

        processed_total = sum(float(r.RECAMOUNT or 0.0) for r in cleaned_records)
        discrepancy = round(abs(raw_total - processed_total), 2)

        count_matches = source_count == processed_count
        amount_matches = discrepancy < 0.01

        is_matched = count_matches and amount_matches
        if is_matched:
            msg = (
                f"Reconciliation successful: {processed_count} rows, "
                f"total amount {processed_total:,.2f}"
            )
        else:
            msg = (
                f"Reconciliation mismatch: Count (raw={source_count}, processed={processed_count}), "
                f"Amount (raw={raw_total:,.2f}, processed={processed_total:,.2f}, diff={discrepancy})"
            )

        return ReconciliationResult(
            is_matched=is_matched,
            source_row_count=source_count,
            processed_row_count=processed_count,
            source_amount_total=raw_total,
            processed_amount_total=processed_total,
            amount_discrepancy=discrepancy,
            message=msg,
        )
