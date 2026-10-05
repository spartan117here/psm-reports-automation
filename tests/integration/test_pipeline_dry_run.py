"""Integration test validating the full offline pipeline and idempotency."""

from datetime import date
from pathlib import Path
import pytest

from app.core.config import AppConfigBundle
from app.core.models import RawReportPayload, RunMetadata, RunStatus
from app.google_sheets.client import MockGoogleSheetsService
from app.google_sheets.writer import SheetWriter
from app.processing.aggregators import LocationAggregator
from app.processing.cleaners import SchemeReportCleaner
from app.processing.location import LocationResolver
from app.processing.reconciliation import DataReconciler
from app.processing.transformers import RecordTransformer
from app.repositories.filesystem import (
    FileSystemReportRepository,
    FileSystemRunRepository,
)
from app.validation.engine import ValidationEngine
from app.validation.rules import ValidationRules


def test_full_pipeline_dry_run(
    config_bundle: AppConfigBundle,
    sample_raw_payload: RawReportPayload,
    tmp_path: Path,
):
    """
    End-to-end integration test validating:
    - Raw report validation
    - 11-column cleaning
    - Location attribution & helper column generation
    - Mock Google Sheet idempotent appending
    - Location aggregations
    - Source vs processed reconciliation
    - File persistence (raw, processed, run metadata)
    - Re-run idempotency
    """
    report_cfg = config_bundle.get_report("subhiksham")
    report_date = date(2026, 10, 4)

    # 1. Validate raw data
    v_empty = ValidationRules.check_non_empty_response(sample_raw_payload)
    assert v_empty.status.value == "PASSED"

    # 2. Clean records (21 -> 11 columns)
    cleaner = SchemeReportCleaner(report_cfg)
    cleaned = cleaner.clean_records(sample_raw_payload.data)
    assert len(cleaned) == 4

    # 3. Location attribution & enrichment
    resolver = LocationResolver(config_bundle.mappings)
    transformer = RecordTransformer(resolver)
    enriched = transformer.enrich_records(cleaned, scheme_type="ss")
    assert len(enriched) == 4
    assert enriched[0].LOCATION == "CHROMEPET"
    assert enriched[0].LOCATION_2 == "CHROMEPET"
    assert enriched[0].CODE_NAME == "CPT001 - K. SURESH"

    # 4. Reconciliation
    rec_result = DataReconciler.reconcile_raw_vs_cleaned(sample_raw_payload, cleaned)
    assert rec_result.is_matched is True
    assert rec_result.source_amount_total == 37500.0
    assert rec_result.processed_amount_total == 37500.0

    # 5. Idempotent Google Sheets Append (Mock)
    mock_service = MockGoogleSheetsService()
    writer = SheetWriter(mock_service)
    sheet_rows = [RecordTransformer.to_sheet_row_values(r) for r in enriched]

    # First append: all 4 rows appended
    append_1 = writer.append_rows_idempotently(
        spreadsheet_id="MOCK_SPREADSHEET",
        tab_name="SS - Oct",
        rows=sheet_rows,
        key_column_index=3,  # Column D (MSNO)
    )
    assert append_1.appended_count == 4
    assert append_1.skipped_duplicates_count == 0

    # Populate mock service existing keys to simulate persistence
    existing_msnos = {r[3] for r in sheet_rows}
    mock_service.get_existing_column_values = lambda sid, tab, col: existing_msnos

    # Second append (simulating re-run): all 4 rows skipped
    append_2 = writer.append_rows_idempotently(
        spreadsheet_id="MOCK_SPREADSHEET",
        tab_name="SS - Oct",
        rows=sheet_rows,
        key_column_index=3,
    )
    assert append_2.appended_count == 0
    assert append_2.skipped_duplicates_count == 4

    # 6. Aggregation
    aggregator = LocationAggregator(config_bundle.targets)
    targets_map = {"CHROMEPET": 216000000.0}
    metrics = aggregator.aggregate_by_location(
        records=enriched, target_branch_map=targets_map, day_of_month=4
    )
    assert "CHROMEPET" in metrics
    assert metrics["CHROMEPET"].new_enrollment_count == 1
    assert metrics["CHROMEPET"].total_amount == 5000.0

    # 7. File persistence in temporary repository
    report_repo = FileSystemReportRepository(tmp_path)
    run_repo = FileSystemRunRepository(tmp_path)

    raw_path = report_repo.save_raw_report(sample_raw_payload)
    assert raw_path.is_file()

    proc_path = report_repo.save_cleaned_records("subhiksham", report_date, cleaned)
    assert proc_path.is_file()

    run_meta = RunMetadata(
        run_id="RUN-20261004-TEST",
        run_date=date(2026, 10, 5),
        report_date=report_date,
        status=RunStatus.SUCCESS,
        source="Innervex:subhiksham",
        row_count=4,
        processed_count=4,
    )
    run_repo.save_run(run_meta)

    # 8. Verify idempotency lookup
    assert run_repo.is_already_processed(report_date, "subhiksham") is True
    assert run_repo.is_already_processed(date(2026, 10, 1), "subhiksham") is False
