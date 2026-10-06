"""
Unit tests for Phase 1E: Read-Only Live Google Sheets Reconciliation Service.

Validates:
- No incoming duplicates
- All incoming MSNOs already present
- All incoming MSNOs new
- Mixed existing and new records
- Conflict detection (amounts, client IDs, duplicate incoming MSNOs)
- Normalization of whitespace and non-breaking spaces (NBSP / \xa0)
- Missing target tab handling (fail closed)
- Header mismatch handling (fail closed)
- API/read failure handling (fail closed)
- Zero writes guaranteed across all operations
- Month tab derivation convention (e.g. 'Sept' and 'Oct')
- CLI reconcile command execution
"""

import argparse
from datetime import date
from unittest.mock import MagicMock, patch
import pytest

from app.cli import cmd_reconcile
from app.core.exceptions import GoogleSheetsError
from app.core.models import CleanedRecord
from app.google_sheets.client import MockGoogleSheetsService
from app.google_sheets.reconciliation import (
    LiveSheetReconciler,
    RecordReconciliationCategory,
    derive_monthly_tab_name,
    format_reconciliation_report,
    normalize_amount,
    normalize_date_str,
    normalize_text,
)


def _make_cleaned_record(
    msno: str = "APP27SCTM/1798658",
    recamount: float = 5000.0,
    clientid: str = "CL-1001",
    name: str = "RAMESH KUMAR",
    schdate: str = "2026-10-04",
) -> CleanedRecord:
    return CleanedRecord(
        COSTNAME="CPT",
        CLIENTID=clientid,
        GROUPCODE="GRP01",
        MSNO=msno,
        NAME=name,
        SCHDATE=schdate,
        RECAMOUNT=recamount,
        MOBILENO="9876543210",
        SCHEME="NEW SWARNA SUBHIKSHAM",
        COMMNAME="K. SURESH",
        COMMCODE="CPT001",
    )


# Standard header row (Columns A:M)
SHEET_HEADERS = [
    "COSTNAME",
    "CLIENTID",
    "GROUPCODE",
    "MSNO",
    "NAME",
    "SCHDATE",
    "LOCATION 2",
    "LOCATION",
    "RECAMOUNT",
    "MOBILENO",
    "SCHEME",
    "COMMNAME",
    "COMMCODE",
]


def _build_mock_service(tab_name: str, sheet_data_rows: list) -> MockGoogleSheetsService:
    metadata = {
        "properties": {"title": "New Enrollment from April 2026"},
        "sheets": [{"properties": {"title": tab_name}}],
    }
    mock_service = MockGoogleSheetsService(metadata)
    mock_service.sheets[f"SPREADSHEET_ID:{tab_name}!A1:M"] = [SHEET_HEADERS] + sheet_data_rows
    return mock_service


# 1. Month Tab Name Derivation Tests
def test_derive_monthly_tab_name():
    """Verify month tab name dynamically adheres to workbook naming convention."""
    assert derive_monthly_tab_name("SS", date(2026, 9, 30)) == "SS - Sept"
    assert derive_monthly_tab_name("SS", date(2026, 10, 4)) == "SS - Oct"
    assert derive_monthly_tab_name("SV", date(2026, 10, 4)) == "SV - Oct"
    assert derive_monthly_tab_name("SS", date(2026, 8, 15)) == "SS - Aug"
    assert derive_monthly_tab_name("SS", date(2026, 1, 10)) == "SS - Jan"


# 2. Text, Amount, and Date Normalization Tests
def test_normalization_whitespace_and_nbsp():
    """Verify whitespace and non-breaking space (\\xa0) are normalized cleanly."""
    assert normalize_text("RAMESH\xa0KUMAR") == "RAMESH KUMAR"
    assert normalize_text("  CPT001 \xa0 ") == "CPT001"
    assert normalize_text(None) == ""

    # Amount normalization
    assert normalize_amount("1,955,500.00") == 1955500.0
    assert normalize_amount("5,000\xa0") == 5000.0
    assert normalize_amount(None) == 0.0

    # Date normalization
    assert normalize_date_str("04/10/26") == "2026-10-04"
    assert normalize_date_str("2026-10-04") == "2026-10-04"
    assert normalize_date_str("04-10-2026") == "2026-10-04"


# 3. No incoming duplicates test
def test_reconcile_no_incoming_duplicates():
    """Verify reconciler accepts distinct incoming records without duplicate errors."""
    mock_service = _build_mock_service("SS - Oct", [])
    reconciler = LiveSheetReconciler(mock_service)

    records = [
        _make_cleaned_record(msno="MSNO-001"),
        _make_cleaned_record(msno="MSNO-002"),
        _make_cleaned_record(msno="MSNO-003"),
    ]

    result = reconciler.reconcile_records(
        "SPREADSHEET_ID", "SS - Oct", date(2026, 10, 4), records
    )

    assert result.incoming_record_count == 3
    assert result.conflict_count == 0
    assert result.new_record_count == 3
    assert result.already_present_count == 0


# 4. All incoming MSNOs already present
def test_reconcile_all_incoming_already_present():
    """Verify matching records are categorized as ALREADY_PRESENT with 0 new, 0 conflicts."""
    sheet_rows = [
        ["CPT", "CL-1001", "GRP01", "MSNO-001", "RAMESH KUMAR", "04/10/26", "CPT", "CPT", 5000.0, "9876543210", "NEW SWARNA SUBHIKSHAM", "K. SURESH", "CPT001"],
        ["CPT", "CL-1002", "GRP01", "MSNO-002", "PRIYA S", "04/10/26", "CPT", "CPT", 10000.0, "9876543210", "NEW SWARNA SUBHIKSHAM", "K. SURESH", "CPT001"],
    ]
    mock_service = _build_mock_service("SS - Oct", sheet_rows)
    reconciler = LiveSheetReconciler(mock_service)

    records = [
        _make_cleaned_record(msno="MSNO-001", recamount=5000.0, clientid="CL-1001", name="RAMESH KUMAR"),
        _make_cleaned_record(msno="MSNO-002", recamount=10000.0, clientid="CL-1002", name="PRIYA S"),
    ]

    result = reconciler.reconcile_records(
        "SPREADSHEET_ID", "SS - Oct", date(2026, 10, 4), records
    )

    assert result.incoming_record_count == 2
    assert result.existing_sheet_msno_count == 2
    assert result.already_present_count == 2
    assert result.new_record_count == 0
    assert result.conflict_count == 0
    assert result.live_writes == 0
    assert result.status == "SAFE / NO ACTION"
    assert result.matching_sheet_amount_total == 15000.0


# 5. All incoming MSNOs new
def test_reconcile_all_incoming_new():
    """Verify records not on the sheet are categorized as NEW."""
    sheet_rows = [
        ["CPT", "CL-0999", "GRP01", "MSNO-999", "EXISTING USER", "01/10/26", "CPT", "CPT", 2000.0, "9876543299", "NEW SWARNA SUBHIKSHAM", "K. SURESH", "CPT001"]
    ]
    mock_service = _build_mock_service("SS - Oct", sheet_rows)
    reconciler = LiveSheetReconciler(mock_service)

    records = [
        _make_cleaned_record(msno="MSNO-001"),
        _make_cleaned_record(msno="MSNO-002"),
    ]

    result = reconciler.reconcile_records(
        "SPREADSHEET_ID", "SS - Oct", date(2026, 10, 4), records
    )

    assert result.incoming_record_count == 2
    assert result.existing_sheet_msno_count == 1
    assert result.already_present_count == 0
    assert result.new_record_count == 2
    assert result.conflict_count == 0
    assert result.live_writes == 0
    assert result.status == "READY TO APPEND (Simulation / No writes performed)"


# 6. Mixed existing and new records
def test_reconcile_mixed_existing_and_new():
    """Verify mixed batch correctly partitions into already_present and new."""
    sheet_rows = [
        ["CPT", "CL-1001", "GRP01", "MSNO-001", "RAMESH KUMAR", "04/10/26", "CPT", "CPT", 5000.0, "9876543210", "NEW SWARNA SUBHIKSHAM", "K. SURESH", "CPT001"]
    ]
    mock_service = _build_mock_service("SS - Oct", sheet_rows)
    reconciler = LiveSheetReconciler(mock_service)

    records = [
        _make_cleaned_record(msno="MSNO-001", recamount=5000.0, clientid="CL-1001", name="RAMESH KUMAR"),
        _make_cleaned_record(msno="MSNO-NEW-002", recamount=7000.0, clientid="CL-1002", name="NEW MEMBER"),
    ]

    result = reconciler.reconcile_records(
        "SPREADSHEET_ID", "SS - Oct", date(2026, 10, 4), records
    )

    assert result.incoming_record_count == 2
    assert result.existing_sheet_msno_count == 1
    assert result.already_present_count == 1
    assert result.new_record_count == 1
    assert result.conflict_count == 0
    assert result.live_writes == 0
    assert result.status == "READY TO APPEND (Simulation / No writes performed)"


# 7. Conflict detection tests
def test_reconcile_conflict_detection_amount_mismatch():
    """Verify amount mismatch between incoming record and sheet triggers CONFLICT."""
    sheet_rows = [
        ["CPT", "CL-1001", "GRP01", "MSNO-001", "RAMESH KUMAR", "04/10/26", "CPT", "CPT", 10000.0, "9876543210", "SS", "SURESH", "CPT001"]
    ]
    mock_service = _build_mock_service("SS - Oct", sheet_rows)
    reconciler = LiveSheetReconciler(mock_service)

    records = [
        _make_cleaned_record(msno="MSNO-001", recamount=5000.0, clientid="CL-1001", name="RAMESH KUMAR")
    ]

    result = reconciler.reconcile_records(
        "SPREADSHEET_ID", "SS - Oct", date(2026, 10, 4), records
    )

    assert result.conflict_count == 1
    assert result.already_present_count == 0
    assert result.status == "CONFLICT DETECTED"
    assert any("RECAMOUNT mismatch" in r for r in result.details[0].conflict_reasons)


def test_reconcile_conflict_detection_clientid_mismatch():
    """Verify client ID mismatch on existing MSNO triggers CONFLICT."""
    sheet_rows = [
        ["CPT", "CL-OTHER", "GRP01", "MSNO-001", "RAMESH KUMAR", "04/10/26", "CPT", "CPT", 5000.0, "9876543210", "SS", "SURESH", "CPT001"]
    ]
    mock_service = _build_mock_service("SS - Oct", sheet_rows)
    reconciler = LiveSheetReconciler(mock_service)

    records = [
        _make_cleaned_record(msno="MSNO-001", recamount=5000.0, clientid="CL-1001", name="RAMESH KUMAR")
    ]

    result = reconciler.reconcile_records(
        "SPREADSHEET_ID", "SS - Oct", date(2026, 10, 4), records
    )

    assert result.conflict_count == 1
    assert result.status == "CONFLICT DETECTED"
    assert any("CLIENTID mismatch" in r for r in result.details[0].conflict_reasons)


def test_reconcile_conflict_detection_mobile_and_scheme_mismatch():
    """Verify differences in any of the 11 business fields (e.g. MOBILENO or SCHEME) trigger CONFLICT."""
    sheet_rows = [
        ["CPT", "CL-1001", "GRP01", "MSNO-001", "RAMESH KUMAR", "04/10/26", "CPT", "CPT", 5000.0, "9999999999", "DIFFERENT SCHEME", "SURESH", "CPT001"]
    ]
    mock_service = _build_mock_service("SS - Oct", sheet_rows)
    reconciler = LiveSheetReconciler(mock_service)

    records = [
        _make_cleaned_record(msno="MSNO-001", recamount=5000.0, clientid="CL-1001", name="RAMESH KUMAR")
    ]

    result = reconciler.reconcile_records(
        "SPREADSHEET_ID", "SS - Oct", date(2026, 10, 4), records
    )

    assert result.conflict_count == 1
    assert result.status == "CONFLICT DETECTED"
    assert any("MOBILENO mismatch" in r for r in result.details[0].conflict_reasons)
    assert any("SCHEME mismatch" in r for r in result.details[0].conflict_reasons)


def test_reconcile_conflict_detection_incoming_duplicate_msno():
    """Verify duplicate MSNO in incoming records batch triggers CONFLICT."""
    mock_service = _build_mock_service("SS - Oct", [])
    reconciler = LiveSheetReconciler(mock_service)

    records = [
        _make_cleaned_record(msno="MSNO-DUP"),
        _make_cleaned_record(msno="MSNO-DUP"),
    ]

    result = reconciler.reconcile_records(
        "SPREADSHEET_ID", "SS - Oct", date(2026, 10, 4), records
    )

    assert result.conflict_count == 1
    assert result.new_record_count == 1
    assert result.status == "CONFLICT DETECTED"
    assert any("Duplicate MSNO" in r for r in result.details[1].conflict_reasons)


# 8. Normalization prevents false conflicts
def test_reconcile_normalization_prevents_false_conflicts():
    """Verify trailing spaces, NBSP, and date formatting differences are not flagged as conflicts."""
    sheet_rows = [
        ["CPT", "CL-1001 ", "GRP01", " MSNO-001 ", "RAMESH\xa0 KUMAR ", "04/10/26", "CPT", "CPT", "5,000.00\xa0", "9876543210", "NEW SWARNA SUBHIKSHAM\xa0", "K. SURESH\xc2", "CPT001"]
    ]
    mock_service = _build_mock_service("SS - Oct", sheet_rows)
    reconciler = LiveSheetReconciler(mock_service)

    records = [
        _make_cleaned_record(msno="MSNO-001", recamount=5000.0, clientid="CL-1001", name="RAMESH KUMAR", schdate="2026-10-04")
    ]

    result = reconciler.reconcile_records(
        "SPREADSHEET_ID", "SS - Oct", date(2026, 10, 4), records
    )

    assert result.conflict_count == 0
    assert result.already_present_count == 1
    assert result.status == "SAFE / NO ACTION"


# 9. Missing target tab handling
def test_reconcile_missing_target_tab():
    """Verify reconciler fails closed if the target tab does not exist in workbook."""
    metadata = {
        "properties": {"title": "Workbook"},
        "sheets": [{"properties": {"title": "SS - Sept"}}],  # SS - Oct is missing
    }
    mock_service = MockGoogleSheetsService(metadata)
    reconciler = LiveSheetReconciler(mock_service)

    records = [_make_cleaned_record()]
    result = reconciler.reconcile_records(
        "SPREADSHEET_ID", "SS - Oct", date(2026, 10, 4), records
    )

    assert "FAILED: TAB_NOT_FOUND" in result.status
    assert result.live_writes == 0
    assert result.error_message is not None


# 10. Header mismatch handling
def test_reconcile_header_mismatch():
    """Verify reconciler fails closed if MSNO column is missing from headers."""
    bad_headers = ["COL_A", "COL_B", "COL_C"]
    metadata = {
        "properties": {"title": "Workbook"},
        "sheets": [{"properties": {"title": "SS - Oct"}}],
    }
    mock_service = MockGoogleSheetsService(metadata)
    mock_service.sheets["SPREADSHEET_ID:SS - Oct!A1:M"] = [bad_headers]

    reconciler = LiveSheetReconciler(mock_service)
    records = [_make_cleaned_record()]

    result = reconciler.reconcile_records(
        "SPREADSHEET_ID", "SS - Oct", date(2026, 10, 4), records
    )

    assert result.status == "FAILED: HEADER_MISMATCH"
    assert result.live_writes == 0
    assert "MSNO" in result.error_message


# 11. API / read failure handling
def test_reconcile_api_read_failure():
    """Verify reconciler fails closed on unexpected API or network exceptions."""
    mock_service = MagicMock()
    mock_service.get_spreadsheet_metadata.side_effect = GoogleSheetsError(
        "Network connection timeout connecting to sheets.googleapis.com"
    )

    reconciler = LiveSheetReconciler(mock_service)
    records = [_make_cleaned_record()]

    result = reconciler.reconcile_records(
        "SPREADSHEET_ID", "SS - Oct", date(2026, 10, 4), records
    )

    assert result.status == "FAILED: API_READ_ERROR"
    assert result.live_writes == 0
    assert "Network connection timeout" in result.error_message


# 12. Zero writes guaranteed
def test_reconcile_zero_writes_guaranteed():
    """Verify across all execution paths that zero write operations are executed."""
    sheet_rows = [
        ["CPT", "CL-1001", "GRP01", "MSNO-001", "RAMESH KUMAR", "04/10/26", "CPT", "CPT", 5000.0, "9876543210", "SS", "SURESH", "CPT001"]
    ]
    mock_service = _build_mock_service("SS - Oct", sheet_rows)

    # Spy on write methods
    mock_service.append_rows = MagicMock()
    mock_service.update_range = MagicMock()

    reconciler = LiveSheetReconciler(mock_service)
    records = [
        _make_cleaned_record(msno="MSNO-001"),
        _make_cleaned_record(msno="MSNO-002"),
    ]

    result = reconciler.reconcile_records(
        "SPREADSHEET_ID", "SS - Oct", date(2026, 10, 4), records
    )

    # Assert 0 writes in report model
    assert result.live_writes == 0

    # Assert 0 mutation calls were made on service
    mock_service.append_rows.assert_not_called()
    mock_service.update_range.assert_not_called()


# 13. Format report test
def test_format_reconciliation_report_output():
    """Verify deterministic formatting matching user specification."""
    sheet_rows = [
        ["CPT", "CL-1001", "GRP01", "MSNO-001", "RAMESH KUMAR", "04/10/26", "CPT", "CPT", 5000.0, "9876543210", "NEW SWARNA SUBHIKSHAM", "K. SURESH", "CPT001"]
    ]
    mock_service = _build_mock_service("SS - Oct", sheet_rows)
    reconciler = LiveSheetReconciler(mock_service)
    records = [_make_cleaned_record(msno="MSNO-001")]

    result = reconciler.reconcile_records(
        "SPREADSHEET_ID", "SS - Oct", date(2026, 10, 4), records
    )
    formatted = format_reconciliation_report(result)

    assert "PSM LIVE GOOGLE SHEETS RECONCILIATION" in formatted
    assert "READ ONLY" in formatted
    assert "Report date:            2026-10-04" in formatted
    assert "Target tab:             SS - Oct" in formatted
    assert "Incoming records:              1" in formatted
    assert "Existing records:              1" in formatted
    assert "Already present:               1" in formatted
    assert "New:                           0" in formatted
    assert "Conflicts:                     0" in formatted
    assert "Live writes:                   0" in formatted
    assert "Status:                 SAFE / NO ACTION" in formatted


# 14. CLI reconcile command with --mock
def test_cli_cmd_reconcile_mock(capsys):
    """Verify CLI reconcile command executes cleanly with --mock flag."""
    args = argparse.Namespace(
        command="reconcile",
        report_date="2026-10-04",
        report_id="subhiksham",
        spreadsheet_id="MOCK_SHEET_ID",
        service_account=None,
        mock=True,
    )

    with patch("app.repositories.filesystem.FileSystemReportRepository.get_cleaned_records") as mock_get:
        mock_get.return_value = [_make_cleaned_record(msno="MSNO-001")]
        exit_code = cmd_reconcile(args)

    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "PSM LIVE GOOGLE SHEETS RECONCILIATION" in captured
    assert "Target tab:             SS - Oct" in captured
    assert "Google Sheets writes performed: 0" in captured


# 15. SV Destination Schema Tolerance Test
def test_sv_destination_schema_tolerance():
    """Verify reconciler correctly handles SV - Oct headers with blank Col A and leading-space Col G."""
    sv_headers = [
        " ",              # A: Blank/space header for COSTNAME
        "CLIENTID",       # B
        "GROUPCODE",      # C
        "MSNO",           # D
        "NAME",           # E
        "SCHDATE",        # F
        " LOCATION 2",    # G: Leading space
        "LOCATION",       # H: Formula
        "RECAMOUNT",      # I
        "MOBILENO",       # J
        "SCHEME",         # K
        "COMMNAME",       # L
        "COMMCODE",       # M
    ]
    sv_row = [
        "APP", "APP27SVP1/500", "NEW", "APP27SCTM/1781480", "V.SARAVANAN",
        "03/10/26", "COIMBATORE SV ECOMM", "ONLINE SV", 35000.0,
        "9566722712", "SWARNA VIRUKSHAM", "KUMAR S", "3070"
    ]
    mock_meta = {
        "properties": {"title": "New Enrollment from April 2026"},
        "sheets": [{"properties": {"title": "SV - Oct"}}],
    }
    mock_service = MockGoogleSheetsService(mock_meta)
    mock_service.sheets["MOCK_ID:SV - Oct!A1:M"] = [sv_headers, sv_row]

    reconciler = LiveSheetReconciler(mock_service)
    matching_record = CleanedRecord(
        COSTNAME="APP",
        CLIENTID="APP27SVP1/500",
        GROUPCODE="NEW",
        MSNO="APP27SCTM/1781480",
        NAME="V.SARAVANAN",
        SCHDATE="2026-10-03",
        RECAMOUNT=35000.0,
        MOBILENO="9566722712",
        SCHEME="SWARNA VIRUKSHAM",
        COMMNAME="KUMAR S",
        COMMCODE="3070",
    )

    result = reconciler.reconcile_records("MOCK_ID", "SV - Oct", date(2026, 10, 4), [matching_record])
    assert result.already_present_count == 1
    assert result.new_record_count == 0
    assert result.conflict_count == 0
    assert result.status == "SAFE / NO ACTION"


# 16. SheetWriter Safe Append Excludes Formula Columns H and N
def test_sheet_writer_append_new_records_formula_columns_excluded():
    """Verify append_new_records writes Columns A:G and I:M, strictly leaving H and N untouched."""
    from app.google_sheets.writer import SheetWriter

    mock_service = MagicMock()
    # Mock existing rows in Column D (1 header row + 10 data rows = 11 rows total)
    mock_service.read_range.return_value = [["MSNO"]] + [["MSNO-OLD"]] * 10

    writer = SheetWriter(mock_service)

    reconcile_res = MagicMock()
    reconcile_res.status = "READY TO APPEND (Simulation / No writes performed)"
    reconcile_res.conflict_count = 0
    reconcile_res.new_record_count = 2
    reconcile_res.incoming_record_count = 2
    reconcile_res.already_present_count = 0
    reconcile_res.details = [
        MagicMock(msno="MSNO-NEW-01", category=RecordReconciliationCategory.NEW),
        MagicMock(msno="MSNO-NEW-02", category=RecordReconciliationCategory.NEW),
    ]

    records = [
        _make_cleaned_record(msno="MSNO-NEW-01", recamount=5000.0),
        _make_cleaned_record(msno="MSNO-NEW-02", recamount=10000.0),
    ]

    result = writer.append_new_records(
        spreadsheet_id="TEST_SHEET_ID",
        tab_name="SS - Oct",
        reconciliation_result=reconcile_res,
        records=records,
        dry_run=False,
        verify_after_write=False,
    )

    assert result.appended_count == 2
    assert result.skipped_duplicates_count == 0

    # Verify update_range calls: exactly 2 calls (one for A:G, one for I:M)
    assert mock_service.update_range.call_count == 2
    call_args_list = mock_service.update_range.call_args_list

    # First call must be A12:G13 (7 columns)
    call_1_range = call_args_list[0][0][1]
    call_1_values = call_args_list[0][0][2]
    assert call_1_range == "SS - Oct!A12:G13"
    assert len(call_1_values) == 2
    assert len(call_1_values[0]) == 7  # Columns A-G

    # Second call must be I12:M13 (5 columns)
    call_2_range = call_args_list[1][0][1]
    call_2_values = call_args_list[1][0][2]
    assert call_2_range == "SS - Oct!I12:M13"
    assert len(call_2_values) == 2
    assert len(call_2_values[0]) == 5  # Columns I-M

    # Verify H and N are NEVER written
    assert "H" not in call_1_range and "H" not in call_2_range
    assert "N" not in call_1_range and "N" not in call_2_range


# 17. Safe Append Fails Closed on Conflict
def test_sheet_writer_append_new_records_fails_on_conflict():
    """Verify append_new_records refuses to write and raises GoogleSheetsError if conflict exists."""
    from app.google_sheets.writer import SheetWriter

    mock_service = MagicMock()
    writer = SheetWriter(mock_service)

    reconcile_res = MagicMock()
    reconcile_res.status = "CONFLICT DETECTED"
    reconcile_res.conflict_count = 1
    reconcile_res.new_record_count = 1

    records = [_make_cleaned_record(msno="MSNO-001")]

    with pytest.raises(GoogleSheetsError, match="conflict.*detected"):
        writer.append_new_records(
            spreadsheet_id="TEST_SHEET_ID",
            tab_name="SS - Oct",
            reconciliation_result=reconcile_res,
            records=records,
            dry_run=False,
        )

    # 0 write calls made
    mock_service.update_range.assert_not_called()
    mock_service.append_rows.assert_not_called()


# 18. Safe Append Skips Already Present Records
def test_sheet_writer_append_new_records_skips_already_present():
    """Verify append_new_records writes zero rows when all records are already present."""
    from app.google_sheets.writer import SheetWriter

    mock_service = MagicMock()
    writer = SheetWriter(mock_service)

    reconcile_res = MagicMock()
    reconcile_res.status = "SAFE / NO ACTION"
    reconcile_res.conflict_count = 0
    reconcile_res.new_record_count = 0
    reconcile_res.already_present_count = 2
    reconcile_res.incoming_record_count = 2

    records = [
        _make_cleaned_record(msno="MSNO-001"),
        _make_cleaned_record(msno="MSNO-002"),
    ]

    result = writer.append_new_records(
        spreadsheet_id="TEST_SHEET_ID",
        tab_name="SS - Oct",
        reconciliation_result=reconcile_res,
        records=records,
        dry_run=False,
    )

    assert result.appended_count == 0
    assert result.skipped_duplicates_count == 2
    mock_service.update_range.assert_not_called()


# 19. Dry Run Append Guarantees Zero Writes
def test_sheet_writer_append_new_records_dry_run_zero_writes():
    """Verify dry_run=True returns candidate metrics without making any write API calls."""
    from app.google_sheets.writer import SheetWriter

    mock_service = MagicMock()
    writer = SheetWriter(mock_service)

    reconcile_res = MagicMock()
    reconcile_res.status = "READY TO APPEND (Simulation / No writes performed)"
    reconcile_res.conflict_count = 0
    reconcile_res.new_record_count = 2
    reconcile_res.incoming_record_count = 2
    reconcile_res.already_present_count = 0
    reconcile_res.details = [
        MagicMock(msno="MSNO-NEW-01", category=RecordReconciliationCategory.NEW),
        MagicMock(msno="MSNO-NEW-02", category=RecordReconciliationCategory.NEW),
    ]

    records = [
        _make_cleaned_record(msno="MSNO-NEW-01"),
        _make_cleaned_record(msno="MSNO-NEW-02"),
    ]

    result = writer.append_new_records(
        spreadsheet_id="TEST_SHEET_ID",
        tab_name="SS - Oct",
        reconciliation_result=reconcile_res,
        records=records,
        dry_run=True,
    )

    assert result.appended_count == 2
    mock_service.update_range.assert_not_called()
    mock_service.append_rows.assert_not_called()


# 20. Post-Write Verification Success
def test_sheet_writer_post_write_verification_success():
    """Verify post-write verification checks row count, MSNOs, and formula integrity."""
    from app.google_sheets.writer import SheetWriter

    mock_service = MagicMock()
    # First read_range: Column D existing rows (1 header + 5 rows = 6 rows total)
    # Second read_range: Verification rows A7:M7
    mock_service.read_range.side_effect = [
        [["MSNO"], ["OLD1"], ["OLD2"], ["OLD3"], ["OLD4"], ["OLD5"]],
        [["CPT", "CL-1", "GRP", "MSNO-NEW-01", "NAME", "2026-10-04", "CPT", "CPT", 5000.0, "9876543210", "SS", "SURESH", "101"]],
    ]
    # read_formulas: H2 and N2
    mock_service.read_formulas.side_effect = [
        [["=ARRAYFORMULA(IF(A2:A=\"\",\"\",...))"]],
        [["=ARRAYFORMULA(IF((M2:M=\"\")*(L2:L=\"\"),\"-\",...))"]],
    ]

    writer = SheetWriter(mock_service)
    reconcile_res = MagicMock()
    reconcile_res.status = "READY TO APPEND"
    reconcile_res.conflict_count = 0
    reconcile_res.new_record_count = 1
    reconcile_res.incoming_record_count = 1
    reconcile_res.already_present_count = 0
    reconcile_res.details = [
        MagicMock(msno="MSNO-NEW-01", category=RecordReconciliationCategory.NEW)
    ]

    records = [_make_cleaned_record(msno="MSNO-NEW-01")]
    result = writer.append_new_records(
        spreadsheet_id="TEST_SHEET_ID",
        tab_name="SS - Oct",
        reconciliation_result=reconcile_res,
        records=records,
        dry_run=False,
        verify_after_write=True,
    )

    assert result.appended_count == 1
    assert mock_service.update_range.call_count == 2
    assert mock_service.read_range.call_count == 2
    assert mock_service.read_formulas.call_count == 2


# 21. Post-Write Verification Detects MSNO Mismatch
def test_sheet_writer_post_write_verification_msno_mismatch_fails():
    """Verify append_new_records raises error if read-back MSNO does not match expected."""
    from app.google_sheets.writer import SheetWriter

    mock_service = MagicMock()
    mock_service.read_range.side_effect = [
        [["MSNO"]],
        [["CPT", "CL-1", "GRP", "WRONG-MSNO", "NAME", "2026-10-04", "CPT", "CPT", 5000.0, "9876543210", "SS", "SURESH", "101"]],
    ]

    writer = SheetWriter(mock_service)
    reconcile_res = MagicMock()
    reconcile_res.status = "READY TO APPEND"
    reconcile_res.conflict_count = 0
    reconcile_res.new_record_count = 1
    reconcile_res.incoming_record_count = 1
    reconcile_res.already_present_count = 0
    reconcile_res.details = [
        MagicMock(msno="MSNO-NEW-01", category=RecordReconciliationCategory.NEW)
    ]

    records = [_make_cleaned_record(msno="MSNO-NEW-01")]
    with pytest.raises(GoogleSheetsError, match="MSNOs mismatch"):
        writer.append_new_records(
            spreadsheet_id="TEST_SHEET_ID",
            tab_name="SS - Oct",
            reconciliation_result=reconcile_res,
            records=records,
            dry_run=False,
            verify_after_write=True,
        )


# 22. Post-Write Verification Detects Missing Formula
def test_sheet_writer_post_write_verification_missing_formula_fails():
    """Verify append_new_records raises error if array formula in H2 or N2 is deleted."""
    from app.google_sheets.writer import SheetWriter

    mock_service = MagicMock()
    mock_service.read_range.side_effect = [
        [["MSNO"]],
        [["CPT", "CL-1", "GRP", "MSNO-NEW-01", "NAME", "2026-10-04", "CPT", "CPT", 5000.0, "9876543210", "SS", "SURESH", "101"]],
    ]
    # H2 formula missing / empty
    mock_service.read_formulas.side_effect = [
        [[]],
        [["=ARRAYFORMULA(...)"]],
    ]

    writer = SheetWriter(mock_service)
    reconcile_res = MagicMock()
    reconcile_res.status = "READY TO APPEND"
    reconcile_res.conflict_count = 0
    reconcile_res.new_record_count = 1
    reconcile_res.incoming_record_count = 1
    reconcile_res.already_present_count = 0
    reconcile_res.details = [
        MagicMock(msno="MSNO-NEW-01", category=RecordReconciliationCategory.NEW)
    ]

    records = [_make_cleaned_record(msno="MSNO-NEW-01")]
    with pytest.raises(GoogleSheetsError, match="H2 array formula missing or altered"):
        writer.append_new_records(
            spreadsheet_id="TEST_SHEET_ID",
            tab_name="SS - Oct",
            reconciliation_result=reconcile_res,
            records=records,
            dry_run=False,
            verify_after_write=True,
        )

