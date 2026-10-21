"""Unit tests verifying strict read-only enforcement across all Google Sheets operations."""

import argparse
from datetime import date
import os
from unittest.mock import MagicMock, patch
import pytest

from app.cli import cmd_closing_sync, cmd_daily_pipeline, cmd_digi_sync, cmd_run
from app.core.exceptions import GoogleSheetsError
from app.google_sheets.client import DEFAULT_SCOPES, GoogleApiSheetsService
from app.google_sheets.writer import SheetWriter
from app.google_sheets.reconciliation import SheetReconciliationResult


def test_google_api_sheets_service_read_only_blocks_append():
    """Verify append_rows raises GoogleSheetsError when read_only=True."""
    with patch.object(GoogleApiSheetsService, "_init_service", return_value=MagicMock()):
        service = GoogleApiSheetsService(read_only=True)
        assert service.read_only is True
        assert service.scopes == DEFAULT_SCOPES

        with pytest.raises(GoogleSheetsError, match="Cannot append rows: service is initialized in READ-ONLY mode"):
            service.append_rows("SPREADSHEET_ID", "Sheet1!A1:G1", [["val1", "val2"]])


def test_google_api_sheets_service_read_only_blocks_update():
    """Verify update_range raises GoogleSheetsError when read_only=True."""
    with patch.object(GoogleApiSheetsService, "_init_service", return_value=MagicMock()):
        service = GoogleApiSheetsService(read_only=True)
        assert service.read_only is True

        with pytest.raises(GoogleSheetsError, match="Cannot update cells: service is initialized in READ-ONLY mode"):
            service.update_range("SPREADSHEET_ID", "Sheet1!A1:B2", [["a", "b"]])


def test_google_api_sheets_service_read_only_blocks_clear():
    """Verify clear_range raises GoogleSheetsError when read_only=True."""
    with patch.object(GoogleApiSheetsService, "_init_service", return_value=MagicMock()):
        service = GoogleApiSheetsService(read_only=True)
        assert service.read_only is True

        with pytest.raises(GoogleSheetsError, match="Cannot clear cells: service is initialized in READ-ONLY mode"):
            service.clear_range("SPREADSHEET_ID", "Sheet1!A1:B2")


def test_strict_read_only_env_forces_read_only_mode(monkeypatch):
    """Verify STRICT_READ_ONLY=true environment variable forces read_only=True even if read_only=False passed."""
    monkeypatch.setenv("STRICT_READ_ONLY", "true")
    with patch.object(GoogleApiSheetsService, "_init_service", return_value=MagicMock()):
        service = GoogleApiSheetsService(read_only=False)
        assert service.read_only is True
        assert service.scopes == DEFAULT_SCOPES

        with pytest.raises(GoogleSheetsError, match="READ-ONLY mode"):
            service.append_rows("SID", "A1:G1", [["data"]])


def test_cli_run_daily_blocked_under_strict_read_only(capsys, monkeypatch):
    """Verify run-daily without --dry-run or --mock is blocked when STRICT_READ_ONLY=true."""
    monkeypatch.setenv("STRICT_READ_ONLY", "true")
    args = argparse.Namespace(
        report_date="2026-10-20",
        dry_run=False,
        mock=False,
        force=False,
    )
    exit_code = cmd_daily_pipeline(args)
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "PIPELINE BLOCKED: STRICT READ-ONLY MODE IS ACTIVE" in out


def test_cli_run_blocked_under_strict_read_only(capsys, monkeypatch):
    """Verify enrollment cmd_run without --dry-run is blocked when STRICT_READ_ONLY=true."""
    monkeypatch.setenv("STRICT_READ_ONLY", "true")
    args = argparse.Namespace(
        report_date="2026-10-20",
        report_id="subhiksham",
        dry_run=False,
        force=False,
    )
    exit_code = cmd_run(args)
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "STRICT READ-ONLY MODE IS ACTIVE" in out


def test_cli_digi_sync_blocked_under_strict_read_only(capsys, monkeypatch):
    """Verify cmd_digi_sync without --dry-run or --mock is blocked when STRICT_READ_ONLY=true."""
    monkeypatch.setenv("STRICT_READ_ONLY", "true")
    args = argparse.Namespace(
        report_date="2026-10-20",
        digi_spreadsheet_id="SID1",
        spreadsheet_id="SID2",
        service_account=None,
        dry_run=False,
        mock=False,
    )
    exit_code = cmd_digi_sync(args)
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "STRICT READ-ONLY MODE IS ACTIVE" in out


def test_cli_closing_sync_blocked_under_strict_read_only(capsys, monkeypatch):
    """Verify cmd_closing_sync without --dry-run or --mock is blocked when STRICT_READ_ONLY=true."""
    monkeypatch.setenv("STRICT_READ_ONLY", "true")
    args = argparse.Namespace(
        report_date="2026-10-20",
        spreadsheet_id="SID_CLOSING",
        service_account=None,
        dry_run=False,
        mock=False,
        force=False,
    )
    exit_code = cmd_closing_sync(args)
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "STRICT READ-ONLY MODE IS ACTIVE" in out


def test_sheet_writer_dry_run_zero_write_calls():
    """Verify SheetWriter.append_new_records in dry_run mode performs zero API mutation calls."""
    mock_service = MagicMock()
    writer = SheetWriter(mock_service)

    rec_res = SheetReconciliationResult(
        report_date=date(2026, 10, 20),
        target_tab="SS - Oct",
        incoming_record_count=5,
        existing_sheet_msno_count=0,
        already_present_count=0,
        new_record_count=5,
        conflict_count=0,
        live_writes=0,
        status="SAFE / NO ACTION",
    )

    dummy_records = [MagicMock() for _ in range(5)]
    for r in dummy_records:
        r.MSNO = "M1"
        r.REGDATE = "20/10/2026"
        r.CLIENTNAME = "NAME"
        r.MOBILENO = "9840112233"
        r.SCHEMENAME = "SS"
        r.AMOUNT = 5000.0
        r.LOCATION = "CHROMEPET"
        r.CODE = "CPT001"
        r.CODE_NAME = "CPT001 - NAME"
        r.LOCATION_2 = "CHROMEPET"
        r.TOTAL_RECVD = 5000.0

    res = writer.append_new_records(
        spreadsheet_id="SID",
        tab_name="SS - Oct",
        reconciliation_result=rec_res,
        records=dummy_records,
        dry_run=True,
    )

    assert res.appended_count == 0
    assert res.skipped_duplicates_count == 0
    # ZERO write calls on the service
    mock_service.append_rows.assert_not_called()
    mock_service.update_range.assert_not_called()
    mock_service.clear_range.assert_not_called()


def test_google_api_sheets_service_read_only_blocks_batch_update():
    """Verify batch_update raises GoogleSheetsError when read_only=True."""
    with patch.object(GoogleApiSheetsService, "_init_service", return_value=MagicMock()):
        service = GoogleApiSheetsService(read_only=True)
        assert service.read_only is True

        with pytest.raises(GoogleSheetsError, match="Cannot execute batchUpdate: service is initialized in READ-ONLY mode"):
            service.batch_update("SPREADSHEET_ID", {"requests": []})


def test_google_api_sheets_service_ensure_grid_capacity_skips_in_read_only():
    """Verify ensure_grid_capacity does not dispatch mutations when read_only=True."""
    with patch.object(GoogleApiSheetsService, "_init_service", return_value=MagicMock()):
        service = GoogleApiSheetsService(read_only=True)
        # Calling ensure_grid_capacity must return immediately without calling batch_update or API
        service.ensure_grid_capacity("SPREADSHEET_ID", "SS - Oct", 5000)


def test_sheet_writer_append_rows_idempotently_blocks_when_read_only():
    """Verify SheetWriter.append_rows_idempotently raises GoogleSheetsError when service is read-only."""
    mock_service = MagicMock()
    mock_service.read_only = True
    mock_service.get_existing_column_values.return_value = set()

    writer = SheetWriter(mock_service)
    with pytest.raises(GoogleSheetsError, match="READ-ONLY mode"):
        writer.append_rows_idempotently(
            spreadsheet_id="SID",
            tab_name="SS - Oct",
            rows=[["A", "B", "C", "NEW_KEY"]],
        )


def test_sheet_writer_append_new_records_blocks_when_read_only_and_not_dry_run():
    """Verify SheetWriter.append_new_records raises GoogleSheetsError if dry_run=False and read_only=True."""
    mock_service = MagicMock()
    mock_service.read_only = True
    writer = SheetWriter(mock_service)

    from app.google_sheets.reconciliation import RecordReconciliationCategory, RecordReconciliationDetail
    rec_res = SheetReconciliationResult(
        report_date=date(2026, 10, 20),
        target_tab="SS - Oct",
        incoming_record_count=1,
        existing_sheet_msno_count=0,
        already_present_count=0,
        new_record_count=1,
        conflict_count=0,
        live_writes=0,
        status="READY TO APPEND",
        details=[RecordReconciliationDetail(msno="M100", category=RecordReconciliationCategory.NEW)],
    )
    dummy_record = MagicMock()
    dummy_record.MSNO = "M100"
    dummy_record.RECAMOUNT = 1000.0
    dummy_record.COSTNAME = "CN"
    dummy_record.CLIENTID = "CID"
    dummy_record.GROUPCODE = "GC"
    dummy_record.NAME = "N"
    dummy_record.SCHDATE = "20/10/2026"
    dummy_record.LOCATION_2 = "LOC"
    dummy_record.MOBILENO = "9840112233"
    dummy_record.SCHEME = "SS"
    dummy_record.COMMNAME = "COMM"
    dummy_record.COMMCODE = "CC"

    with pytest.raises(GoogleSheetsError, match="READ-ONLY mode"):
        writer.append_new_records(
            spreadsheet_id="SID",
            tab_name="SS - Oct",
            reconciliation_result=rec_res,
            records=[dummy_record],
            dry_run=False,
        )


def test_cmd_screenshot_isolates_mock_to_mock_directory(monkeypatch):
    """Verify cmd_screenshot defaults mock screenshots to data/screenshots/mock."""
    from app.cli import build_parser, cmd_screenshot
    from pathlib import Path

    parser = build_parser()
    args = parser.parse_args([
        "screenshot",
        "--report-date", "2026-10-20",
        "--target", "all",
        "--mock",
    ])

    captured_dirs = []
    def mock_engine_init(self, sheets_service, config_bundle, output_dir, dpi):
        captured_dirs.append(output_dir)
        self.sheets_service = sheets_service
        self.output_dir = output_dir

    with patch("app.reporting.screenshots.GoogleSheetsScreenshotEngine.__init__", mock_engine_init), \
         patch("app.reporting.screenshots.GoogleSheetsScreenshotEngine.capture_enrollment") as mock_cap_e, \
         patch("app.reporting.screenshots.GoogleSheetsScreenshotEngine.capture_closing") as mock_cap_c:

        mock_res_e = MagicMock(report_date=date(2026, 10, 20), output_path=Path("mock_e.png"), report_title="T", range_used="R", gid="1", dimensions=(100, 100), file_size_bytes=1000)
        mock_res_c = MagicMock(report_date=date(2026, 10, 20), output_path=Path("mock_c.png"), report_title="T", range_used="R", gid="2", dimensions=(100, 100), file_size_bytes=1000)
        mock_cap_e.return_value = mock_res_e
        mock_cap_c.return_value = mock_res_c

        exit_code = cmd_screenshot(args)

    assert exit_code == 0
    assert len(captured_dirs) == 1
    # Must be in mock subfolder
    assert captured_dirs[0].name == "mock"
    assert "screenshots" in str(captured_dirs[0])


def test_digi_report_processor_blocks_in_read_only_mode():
    """Verify ConsolidateReportUpdater.update_consolidate_report raises GoogleSheetsError in read-only mode."""
    from app.reporting.digi import ConsolidateReportUpdater, DigiExtractionResult

    mock_dest = MagicMock()
    mock_dest.read_only = True

    updater = ConsolidateReportUpdater(
        sheets_service=mock_dest,
        spreadsheet_id="DEST_ID",
    )

    dummy_branches = [
        "NELLAI", "CHROMEPET", "TRIVANDRUM", "TRICHY", "COIMBATORE",
        "SALEM", "POONAMALLEE", "PADI", "KANCHEEPURAM", "ECOMMERCE"
    ]
    extraction = DigiExtractionResult(
        report_date=date(2026, 10, 20),
        gold_values={b: 10 for b in dummy_branches},
        silver_values={b: 5 for b in dummy_branches},
    )

    with pytest.raises(GoogleSheetsError, match="READ-ONLY mode"):
        updater.update_consolidate_report(
            extraction=extraction,
            dry_run=False,
        )


def test_closing_report_processor_blocks_in_read_only_mode():
    """Verify ClosingReportProcessor.run raises GoogleSheetsError in read-only mode."""
    from pathlib import Path
    from app.reporting.closing import ClosingReportProcessor

    mock_innervex = MagicMock()
    mock_service = MagicMock()
    mock_service.read_only = True

    processor = ClosingReportProcessor(
        innervex_client=mock_innervex,
        sheets_service=mock_service,
        spreadsheet_id="CLOSING_ID",
    )

    processor.fetcher.get_filter_configuration = MagicMock(return_value=(["SS"], ["CPT"], ["CLOSED"]))
    processor.fetcher.fetch_closing_report = MagicMock(return_value=[MagicMock()])
    processor._save_raw_and_processed = MagicMock(return_value=(Path("dummy_raw.csv"), Path("dummy_proc.csv")))

    dummy_clean = MagicMock()
    dummy_clean.cleaned_row_count = 1
    dummy_clean.records = [MagicMock(to_row_values=lambda: ["A", "B", "C", "D", "E", "F", "G"])]
    processor.cleaner.clean_records = MagicMock(return_value=dummy_clean)
    processor.sheets_sync.get_last_data_row = MagicMock(return_value=10)

    with pytest.raises(GoogleSheetsError, match="READ-ONLY mode"):
        processor.run(
            report_date=date(2026, 10, 20),
            dry_run=False,
        )


def test_mock_mode_digi_sync_labels_and_counts(capsys):
    """Verify cmd_digi_sync in mock mode displays MOCK labels and 0 actual writes."""
    args = argparse.Namespace(
        report_date="2026-10-20",
        digi_spreadsheet_id=None,
        spreadsheet_id=None,
        service_account=None,
        dry_run=False,
        mock=True,
    )
    rc = cmd_digi_sync(args)
    assert rc == 0

    out = capsys.readouterr().out
    assert "MOCK / OFFLINE (Simulated Data & Zero Writes)" in out
    assert "LIVE UPDATE" not in out
    assert "ACTUAL GOOGLE SHEETS WRITES: 0 (MOCK MODE)" in out
    assert "SIMULATED ROWS:              10" in out
    assert "[MOCK / SYNTHETIC DATA]" in out
    assert "PASS (MOCK SIMULATION COMPLETE - ZERO WRITES)" in out


def test_mock_mode_closing_sync_labels_and_counts(capsys):
    """Verify cmd_closing_sync in mock mode displays MOCK labels, simulated counts, and 0 actual writes."""
    args = argparse.Namespace(
        report_date="2026-10-20",
        spreadsheet_id="MOCK_CLOSING_ID",
        service_account=None,
        dry_run=False,
        mock=True,
        force=False,
    )
    rc = cmd_closing_sync(args)
    assert rc == 0

    out = capsys.readouterr().out
    assert "MOCK / OFFLINE (Simulated Data & Zero Writes)" in out
    assert "LIVE UPDATE" not in out
    assert "Actual Google Sheets Writes: 0 (MOCK MODE)" in out
    assert "Simulated Rows:            103" in out
    assert "Writes Performed:          0 (MOCK SIMULATION - 103 rows simulated)" in out
    assert "Writes Performed:          103" not in out
    assert "[MOCK / SYNTHETIC DATA]" in out
    assert "SUCCESS (MOCK SIMULATION COMPLETE - ZERO WRITES)" in out


def test_mock_mode_closing_sync_idempotency_consistency(capsys):
    """Verify closing sync idempotency in mock mode reports 0 simulated rows and 0 writes."""

    args = argparse.Namespace(
        report_date="2026-10-20",
        spreadsheet_id="MOCK_CLOSING_ID",
        service_account=None,
        dry_run=False,
        mock=True,
        force=False,
        seed_include_target_date=True,
    )
    rc = cmd_closing_sync(args)
    assert rc == 0

    out = capsys.readouterr().out
    assert "Actual Google Sheets Writes: 0 (MOCK MODE)" in out
    assert "Simulated Rows:            0" in out
    assert "Writes Performed:          0 (Idempotent Skip)" in out
    assert "ALREADY_PROCESSED (Idempotent skip)" in out
    assert "Writes Performed:          103" not in out


def test_mock_mode_validation_table_synthetic_flag():
    """Verify ClosingReportProcessor in mock mode marks validation results as synthetic."""
    from app.google_sheets.client import MockGoogleSheetsService
    from app.innervex.client import InnervexClient
    from app.reporting.closing import ClosingReportProcessor, seed_mock_closing_sheet

    svc = MockGoogleSheetsService()
    seed_mock_closing_sheet(svc, "MOCK_CLOSING_ID", target_date=date(2026, 10, 20), include_target_date=False)

    processor = ClosingReportProcessor(
        innervex_client=MagicMock(spec=InnervexClient),
        sheets_service=svc,
        spreadsheet_id="MOCK_CLOSING_ID",
    )

    result = processor.run(report_date=date(2026, 10, 20), dry_run=False, mock=True)
    assert result.mock is True
    assert result.validation.is_synthetic is True
    assert result.validation.status == "PASS"
    assert len(result.validation.comparisons) > 0


def test_real_mode_empty_showroom_table_fails_validation():
    """Verify live mode without showroom reconciliation data does not report PASS."""
    from app.google_sheets.client import MockGoogleSheetsService
    from app.innervex.client import InnervexClient
    from app.reporting.closing import ClosingReportProcessor

    # Real service without summary or pivot table
    from app.google_sheets.client import GoogleApiSheetsService
    svc = MagicMock(spec=GoogleApiSheetsService)
    processor = ClosingReportProcessor(
        innervex_client=MagicMock(spec=InnervexClient),
        sheets_service=svc,
        spreadsheet_id="EMPTY_CLOSING_ID",
    )

    processor.fetcher.get_filter_configuration = MagicMock(return_value=(["SS"], ["CPT"], ["CLOSED"]))
    processor.fetcher.fetch_closing_report = MagicMock(return_value=[{
        "DOCDATE": "20/10/2026", "NAME": "M", "MOBILE": "98401", "SCHEMENAME": "SS",
        "WEIGHT": "5", "PURCHASEWEIGHT": "5", "GROSSAMOUNT": "5000", "GIFTAMOUNT": "0",
        "AMOUNT": "5000", "PAIDINS": "1", "STATUS": "CLOSE", "SHOWROOM": "CPT",
        "DOCNO": "1", "PASSBOOK": "P1", "SNO": "1"
    }])
    processor._save_raw_and_processed = MagicMock(return_value=(None, None))
    processor.sheets_sync.read_summary_and_pivot_values = MagicMock(return_value=({}, {}))
    processor.sheets_sync.find_last_data_row = MagicMock(return_value=10)
    processor.sheets_sync.check_idempotency = MagicMock(return_value=(False, 0))

    result = processor.run(report_date=date(2026, 10, 20), dry_run=True, mock=False)
    assert result.validation.is_synthetic is False
    assert result.validation.status == "FAIL"
    assert len(result.validation.comparisons) == 0


def test_mock_mode_daily_pipeline_labels_and_zero_writes(capsys, tmp_path):
    """Verify cmd_daily_pipeline in mock mode labels all stages as MOCK and performs 0 writes."""
    args = argparse.Namespace(
        report_date="2026-10-20",
        dry_run=False,
        mock=True,
        force=False,
        output_dir=str(tmp_path),
        dpi=100,
        service_account=None,
        spreadsheet_id=None,
        digi_spreadsheet_id=None,
        closing_spreadsheet_id=None,
    )

    rc = cmd_daily_pipeline(args)
    assert rc == 0

    out = capsys.readouterr().out
    assert "Pipeline Mode:      MOCK / OFFLINE" in out
    assert "LIVE PRODUCTION" not in out
    assert "LIVE UPDATE" not in out
    assert "Actual Google Sheets Writes: 0 (MOCK MODE)" in out
    assert "SUCCESS (MOCK SIMULATION - ZERO WRITES)" in out
    assert "SUCCESS (MOCK SCREENSHOTS - ZERO WRITES)" in out

    # Verify screenshots generated with mock_ prefix
    pngs = list(tmp_path.glob("*.png"))
    assert len(pngs) == 2
    for p in pngs:
        assert p.name.startswith("mock_")



