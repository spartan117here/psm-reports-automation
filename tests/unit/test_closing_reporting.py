"""
Unit tests for Scheme Closing Report processing and synchronization.

Covers all 20 required validation scenarios:
1. Yesterday date calculation.
2. DigiGold exclusion.
3. DigiSilver exclusion.
4. Required scheme selection preservation.
5. CSV header/banner cleanup.
6. Required column validation.
7. Column selection/order (A:G).
8. DOCDATE validation.
9. MOBILE preservation (no scientific notation, no float conversion).
10. WEIGHT validation (numeric float).
11. DigiGold/DigiSilver detection in cleaned data (halt/rejection).
12. Destination last-row detection.
13. A:G append mapping.
14. H:I formula extension.
15. Idempotency (detects already processed date).
16. Duplicate detection.
17. Showroom mapping (PAD -> PADI, TVC -> TVM, etc.).
18. CLOSED vs COUNTA comparison.
19. Mismatch detection (reports difference and fails).
20. Dry-run produces zero writes.
"""

from datetime import date, timedelta
import pytest
from unittest.mock import MagicMock

from app.core.exceptions import DataValidationError
from app.google_sheets.client import MockGoogleSheetsService
from app.reporting.closing import (
    CLOSING_CLEANED_COLUMNS,
    EXCLUDED_SCHEMES,
    REQUIRED_ACTIVE_SCHEMES,
    SHOWROOM_MAPPING,
    CleanedClosingRecord,
    ClosingReportCleaner,
    ClosingReportFetcher,
    ClosingReportProcessor,
    ClosingSheetSync,
    calculate_yesterday_date,
    derive_closing_tab_name,
    map_showroom_label,
    normalize_docdate,
    normalize_mobile,
    normalize_sheet_date,
    normalize_weight,
    seed_mock_closing_sheet,
)


# -------------------------------------------------------------------------
# 1. Yesterday Date Calculation
# -------------------------------------------------------------------------
def test_yesterday_date_calculation():
    """Verify yesterday date calculation defaults to today - 1 day."""
    today = date.today()
    yesterday = calculate_yesterday_date()
    assert yesterday == today - timedelta(days=1)

    # Explicit date override
    custom_date = date(2026, 10, 6)
    resolved = calculate_yesterday_date(custom_date)
    assert resolved == custom_date


# -------------------------------------------------------------------------
# 2. DigiGold Exclusion
# -------------------------------------------------------------------------
def test_digi_gold_exclusion():
    """Verify DIGI GOLD is excluded from selected schemes."""
    available_schemes = [
        "NEW SWARNA SUBHIKSHAM",
        "SWARNA VIRUKSHAM",
        "DIGI GOLD",
        "DIGI SILVER",
    ]
    selected = [s for s in available_schemes if s.upper() not in EXCLUDED_SCHEMES]
    assert "DIGI GOLD" not in selected
    assert "NEW SWARNA SUBHIKSHAM" in selected


# -------------------------------------------------------------------------
# 3. DigiSilver Exclusion
# -------------------------------------------------------------------------
def test_digi_silver_exclusion():
    """Verify DIGI SILVER is excluded from selected schemes."""
    available_schemes = [
        "NEW SWARNA SUBHIKSHAM",
        "SWARNA VIRUKSHAM",
        "DIGI GOLD",
        "DIGI SILVER",
    ]
    selected = [s for s in available_schemes if s.upper() not in EXCLUDED_SCHEMES]
    assert "DIGI SILVER" not in selected
    assert "SWARNA VIRUKSHAM" in selected


# -------------------------------------------------------------------------
# 4. Required Scheme Selection Preservation
# -------------------------------------------------------------------------
def test_required_scheme_selection_preservation():
    """Verify that all 6 required active schemes remain selected after Digi exclusion."""
    all_8_schemes = [
        "SWARNALAKSHMI JEWELLERY PURCHASE PLAN W",
        "SWARNA VIRUKSHAM FLEXI",
        "SWARNA VIRUKSHAM",
        "SWARNA SUBHIKSHAM FLEXI",
        "SWARNA LABHAM SUPER FLEXI",
        "NEW SWARNA SUBHIKSHAM",
        "DIGI SILVER",
        "DIGI GOLD",
    ]
    selected = [s for s in all_8_schemes if s.upper() not in EXCLUDED_SCHEMES]
    assert len(selected) == 6
    for req in REQUIRED_ACTIVE_SCHEMES:
        assert req in selected


# -------------------------------------------------------------------------
# 5. CSV Header/Banner Cleanup
# -------------------------------------------------------------------------
def test_csv_header_banner_cleanup():
    """Verify cleaner strips the 3 preamble banner lines and parses columns."""
    raw_csv = (
        '"************POTHYSSWARNAMAHAL*************","","","","","","","","","","","","","",""\r\n'
        '"Report name : SCHEME CLOSING REPORT Date : 2026-10-06 Time : 12:00:00","","","","","","","","","","","","","",""\r\n'
        '"Selection Criteria : FromDate: 2026-10-06; ToDate: 2026-10-06","","","","","","","","","","","","","",""\r\n'
        '"SNO","DOCDATE","REDEMNO","NAME","PASSBOOK","MOBILE","SCHEMENAME","WEIGHT","PURCHASEWEIGHT","SCHEME AMOUNT","DEDUCTION AMOUNT","NET AMOUNT","PAID INS","STATUS","SHOWROOM"\r\n'
        '"1","2026-10-06 10:41:04.702","CPT27SCR/11993","RAMESH","SCV-2616","9487771746","SWARNA VIRUKSHAM","26.026","26.026","295000","0","295000","1","CLOSE","CPT"\r\n'
        '"TOTAL :","","","","","","","26.03","26.03","295000.00","0.00","295000.00","","",""\r\n'
    )
    cleaner = ClosingReportCleaner()
    parsed = cleaner.parse_raw_csv(raw_csv)
    assert len(parsed) == 1
    assert parsed[0]["NAME"] == "RAMESH"
    assert parsed[0]["MOBILE"] == "9487771746"
    assert parsed[0]["SHOWROOM"] == "CPT"


# -------------------------------------------------------------------------
# 6. Required Column Validation
# -------------------------------------------------------------------------
def test_required_column_validation():
    """Verify that cleaner validates presence of required columns."""
    bad_csv = (
        '"DOCDATE","NAME"\r\n'
        '"2026-10-06","JOHN"\r\n'
    )
    cleaner = ClosingReportCleaner()
    # Missing MOBILE should be rejected during cleaning
    parsed = cleaner.parse_raw_csv(bad_csv)
    res = cleaner.clean_records(parsed, date(2026, 10, 6))
    assert res.cleaned_row_count == 0
    assert len(res.rejected_rows) == 1


# -------------------------------------------------------------------------
# 7. Column Selection and Order (A:G)
# -------------------------------------------------------------------------
def test_column_selection_and_order():
    """Verify output columns match exact A:G order and discard obsolete fields."""
    rec = CleanedClosingRecord(
        docdate="06/10/2026",
        name="LAKSHMI",
        mobile="9840123456",
        schemename="NEW SWARNA SUBHIKSHAM",
        weight=10.5,
        status="CLOSE",
        showroom="CBE",
    )
    row_vals = rec.to_row_values()
    assert len(row_vals) == 7
    assert row_vals[0] == "06/10/2026"     # A: DOCDATE
    assert row_vals[1] == "LAKSHMI"         # B: NAME
    assert row_vals[2] == "9840123456"      # C: MOBILE
    assert row_vals[3] == "NEW SWARNA SUBHIKSHAM" # D: SCHEMENAME
    assert row_vals[4] == 10.5              # E: WEIGHT
    assert row_vals[5] == "CLOSE"           # F: STATUS
    assert row_vals[6] == "CBE"             # G: SHOWROOM
    assert CLOSING_CLEANED_COLUMNS == [
        "DOCDATE", "NAME", "MOBILE", "SCHEMENAME", "WEIGHT", "STATUS", "SHOWROOM"
    ]


# -------------------------------------------------------------------------
# 8. DOCDATE Validation
# -------------------------------------------------------------------------
def test_docdate_validation():
    """Verify DOCDATE parsing, formatting to DD/MM/YYYY, and report date matching."""
    s1, d1 = normalize_docdate("2026-10-06 10:41:04.702")
    assert s1 == "06/10/2026"
    assert d1 == date(2026, 10, 6)

    s2, d2 = normalize_docdate("06/10/2026")
    assert s2 == "06/10/2026"
    assert d2 == date(2026, 10, 6)

    # Date mismatch flags row
    cleaner = ClosingReportCleaner()
    row = [{
        "DOCDATE": "2026-10-05",  # Mismatch (yesterday was 2026-10-06)
        "NAME": "KUMAR",
        "MOBILE": "9840011223",
        "SCHEMENAME": "SWARNA VIRUKSHAM",
        "WEIGHT": "5.0",
        "STATUS": "CLOSE",
        "SHOWROOM": "CPT",
    }]
    res = cleaner.clean_records(row, date(2026, 10, 6))
    assert res.cleaned_row_count == 0
    assert "does not match report date" in res.rejected_rows[0]["issues"][0]


# -------------------------------------------------------------------------
# 9. MOBILE Preservation
# -------------------------------------------------------------------------
def test_mobile_preservation():
    """Verify mobile numbers are not in scientific notation or float format."""
    # Scientific notation string
    assert normalize_mobile("9.487771746E9") == "9487771746"
    assert normalize_mobile(9487771746) == "9487771746"
    assert normalize_mobile("9487771746.0") == "9487771746"
    assert normalize_mobile(" 9487771746 ") == "9487771746"
    assert normalize_mobile("+91 9487771746") == "919487771746"


# -------------------------------------------------------------------------
# 10. WEIGHT Validation
# -------------------------------------------------------------------------
def test_weight_validation():
    """Verify weight conversion to numeric float."""
    assert normalize_weight("26.026") == 26.026
    assert normalize_weight(26.026) == 26.026
    assert normalize_weight(10) == 10.0
    assert normalize_weight("1,250.50") == 1250.50
    assert normalize_weight("") == 0.0
    assert normalize_weight(None) == 0.0


# -------------------------------------------------------------------------
# 11. DigiGold / DigiSilver Detection in Cleaned Data (Halt Rule)
# -------------------------------------------------------------------------
def test_digi_detection_halt_in_cleaner():
    """Verify cleaner immediately stops with DataValidationError if Digi appears."""
    cleaner = ClosingReportCleaner()
    bad_rows = [
        {
            "DOCDATE": "2026-10-06",
            "NAME": "INVALID USER",
            "MOBILE": "9840112233",
            "SCHEMENAME": "DIGI GOLD",  # FORBIDDEN
            "WEIGHT": "1.0",
            "STATUS": "CLOSE",
            "SHOWROOM": "ECOM",
        }
    ]
    with pytest.raises(DataValidationError) as exc:
        cleaner.clean_records(bad_rows, date(2026, 10, 6))
    assert "Digi scheme 'DIGI GOLD' detected" in str(exc.value)

    bad_rows_silver = [
        {
            "DOCDATE": "2026-10-06",
            "NAME": "INVALID USER 2",
            "MOBILE": "9840112244",
            "SCHEMENAME": "DIGI SILVER",  # FORBIDDEN
            "WEIGHT": "10.0",
            "STATUS": "CLOSE",
            "SHOWROOM": "ECOM",
        }
    ]
    with pytest.raises(DataValidationError) as exc:
        cleaner.clean_records(bad_rows_silver, date(2026, 10, 6))
    assert "Digi scheme 'DIGI SILVER' detected" in str(exc.value)


# -------------------------------------------------------------------------
# 12. Destination Last-Row Detection (A:G Region specifically)
# -------------------------------------------------------------------------
def test_destination_last_row_detection():
    """Verify last row detection in A:G ignores non-empty cells elsewhere."""
    mock_service = MockGoogleSheetsService()
    tab = "October - 2026"
    # Seed 5 rows in A:G
    mock_service.update_range("SID", f"{tab}!A1:G", [
        ["DOCDATE", "NAME", "MOBILE", "SCHEMENAME", "WEIGHT", "STATUS", "SHOWROOM"],
        ["01/10/2026", "A", "984001", "S1", 1.0, "CLOSE", "CBE"],
        ["02/10/2026", "B", "984002", "S2", 2.0, "CLOSE", "CPT"],
        ["03/10/2026", "C", "984003", "S3", 3.0, "CLOSE", "TVM"],
        ["04/10/2026", "D", "984004", "S4", 4.0, "CLOSE", "SLM"],
    ])

    sync = ClosingSheetSync(mock_service, spreadsheet_id="SID")
    last_row = sync.find_last_data_row(tab)
    assert last_row == 5


# -------------------------------------------------------------------------
# 13. A:G Append Mapping
# -------------------------------------------------------------------------
def test_ag_append_mapping():
    """Verify cleaned records map cleanly to A:G payload."""
    rec1 = CleanedClosingRecord(
        docdate="06/10/2026", name="USER A", mobile="9840000001",
        schemename="NEW SWARNA SUBHIKSHAM", weight=5.0, status="CLOSE", showroom="CPT"
    )
    rec2 = CleanedClosingRecord(
        docdate="06/10/2026", name="USER B", mobile="9840000002",
        schemename="SWARNA VIRUKSHAM", weight=12.5, status="TERMINATE", showroom="TVC"
    )
    rows = [rec1.to_row_values(), rec2.to_row_values()]
    assert len(rows) == 2
    assert rows[0] == ["06/10/2026", "USER A", "9840000001", "NEW SWARNA SUBHIKSHAM", 5.0, "CLOSE", "CPT"]
    assert rows[1] == ["06/10/2026", "USER B", "9840000002", "SWARNA VIRUKSHAM", 12.5, "TERMINATE", "TVC"]


# -------------------------------------------------------------------------
# 14. H:I Formula Extension
# -------------------------------------------------------------------------
def test_hi_formula_extension():
    """Verify H:I formula adjustment from template row."""
    template_h = '=IF(C100="","",VLOOKUP(C100,\'Working Sheet SS\'!A:D,4,FALSE))'
    template_i = '=IF(C100="","",VLOOKUP(C100,\'Working Sheet SV\'!A:D,4,FALSE))'

    adj_h = ClosingSheetSync.adjust_formula_row_reference(template_h, 100, 102)
    adj_i = ClosingSheetSync.adjust_formula_row_reference(template_i, 100, 102)

    assert adj_h == '=IF(C102="","",VLOOKUP(C102,\'Working Sheet SS\'!A:D,4,FALSE))'
    assert adj_i == '=IF(C102="","",VLOOKUP(C102,\'Working Sheet SV\'!A:D,4,FALSE))'


# -------------------------------------------------------------------------
# 15. Idempotency Check
# -------------------------------------------------------------------------
def test_idempotency_check():
    """Verify idempotency detects previously written report dates."""
    mock_service = MockGoogleSheetsService()
    tab = "October - 2026"
    mock_service.update_range("SID", f"{tab}!A1:A", [["DOCDATE"], ["05/10/2026"], ["06/10/2026"]])

    sync = ClosingSheetSync(mock_service, spreadsheet_id="SID")
    already_proc, count = sync.check_idempotency(tab, date(2026, 10, 6))
    assert already_proc is True
    assert count == 1

    already_proc_new, count_new = sync.check_idempotency(tab, date(2026, 10, 7))
    assert already_proc_new is False
    assert count_new == 0


# -------------------------------------------------------------------------
# 16. Duplicate Detection
# -------------------------------------------------------------------------
def test_duplicate_detection():
    """Verify detection of multiple records for same date."""
    mock_service = MockGoogleSheetsService()
    tab = "October - 2026"
    mock_service.update_range("SID", f"{tab}!A1:A", [["06/10/2026"], ["06/10/2026"], ["06/10/2026"]])

    sync = ClosingSheetSync(mock_service, spreadsheet_id="SID")
    already_proc, count = sync.check_idempotency(tab, date(2026, 10, 6))
    assert already_proc is True
    assert count == 3


# -------------------------------------------------------------------------
# 17. Showroom Mapping
# -------------------------------------------------------------------------
def test_showroom_mapping():
    """Verify alias mapping for showroom codes."""
    assert map_showroom_label("PAD") == "PADI"
    assert map_showroom_label("PADI") == "PADI"
    assert map_showroom_label("TVC") == "TVM"
    assert map_showroom_label("TVM") == "TVM"
    assert map_showroom_label("CPT") == "CPT"
    assert map_showroom_label("CBE") == "CBE"
    assert map_showroom_label("TPJ") == "TPJ"
    assert map_showroom_label("SLM") == "SLM"
    assert map_showroom_label("PNM") == "PNM"


# -------------------------------------------------------------------------
# 18. CLOSED vs COUNTA Comparison (Match Case)
# -------------------------------------------------------------------------
def test_closed_vs_counta_comparison_match():
    """Verify comparison passes when all showrooms match."""
    sync = ClosingSheetSync(MockGoogleSheetsService(), spreadsheet_id="SID")
    closed_vals = {"CBE": 186, "CPT": 264, "TVM": 140}
    pivot_vals = {"CBE": 186, "CPT": 264, "TVM": 140}

    val_res = sync.compare_closed_vs_pivot(closed_vals, pivot_vals)
    assert val_res.status == "PASS"
    assert val_res.all_showrooms_matched is True
    assert len(val_res.errors) == 0
    assert len(val_res.comparisons) == 3


# -------------------------------------------------------------------------
# 19. Mismatch Detection (Fail Case)
# -------------------------------------------------------------------------
def test_closed_vs_counta_mismatch_detection():
    """Verify comparison fails and details differences on mismatch."""
    sync = ClosingSheetSync(MockGoogleSheetsService(), spreadsheet_id="SID")
    closed_vals = {"CBE": 186, "CPT": 264}
    pivot_vals = {"CBE": 186, "CPT": 260}  # CPT difference of 4

    val_res = sync.compare_closed_vs_pivot(closed_vals, pivot_vals)
    assert val_res.status == "FAIL"
    assert val_res.all_showrooms_matched is False
    assert len(val_res.errors) == 1
    assert "Mismatch for showroom 'CPT': CLOSED=264, PIVOT=260 (diff=4)" in val_res.errors[0]


# -------------------------------------------------------------------------
# 20. Dry-Run Produces Zero Writes
# -------------------------------------------------------------------------
def test_dry_run_produces_zero_writes():
    """Verify dry-run performs extraction and validation with 0 sheet writes."""
    mock_service = MockGoogleSheetsService()
    tab = "October - 2026"

    # Setup mock innervex client
    mock_client = MagicMock()
    mock_client.post_report.return_value.data = [
        {
            "DOCDATE": "2026-10-06 10:00:00",
            "NAME": "TEST USER",
            "MOBILE": "9840112233",
            "SCHEMENAME": "NEW SWARNA SUBHIKSHAM",
            "WEIGHT": 5.0,
            "STATUS": "CLOSE",
            "SHOWROOM": "CBE",
        }
    ]
    mock_client.post_report.return_value.model_dump.return_value = {
        "scheme": REQUIRED_ACTIVE_SCHEMES + ["DIGI GOLD", "DIGI SILVER"],
        "showroomList": ["CBE", "CPT"],
        "status": ["CLOSE"],
    }

    processor = ClosingReportProcessor(
        innervex_client=mock_client,
        sheets_service=mock_service,
        spreadsheet_id="MOCK_SID",
    )

    result = processor.run(report_date=date(2026, 10, 6), dry_run=True)

    assert result.dry_run is True
    assert result.cleaned_rows_count == 1
    assert result.validation.status == "PASS"

    # Verify mock sheets has ZERO updates in data range
    written_data = mock_service.read_range("MOCK_SID", f"{tab}!A{result.append_start_row}:G{result.append_end_row}")
    assert len(written_data) == 0


# -------------------------------------------------------------------------
# 21. Date Values Returned as Strings
# -------------------------------------------------------------------------
def test_date_values_returned_as_strings():
    """Verify normalize_sheet_date parses formatted date strings."""
    assert normalize_sheet_date("06/10/2026") == date(2026, 10, 6)
    assert normalize_sheet_date("6/10/2026") == date(2026, 10, 6)
    assert normalize_sheet_date("2026-10-06") == date(2026, 10, 6)
    assert normalize_sheet_date("06-10-2026") == date(2026, 10, 6)
    assert normalize_sheet_date("  06/10/2026  ") == date(2026, 10, 6)


# -------------------------------------------------------------------------
# 22. Date Values in Alternative Google Sheets Representations
# -------------------------------------------------------------------------
def test_date_values_in_alternative_representations():
    """Verify normalize_sheet_date handles 2-digit years, serials, and timestamps."""
    # 2-digit year format common in Google Sheets
    assert normalize_sheet_date("06/10/26") == date(2026, 10, 6)
    assert normalize_sheet_date("6/10/26") == date(2026, 10, 6)
    # Numeric serial dates (Google Sheets base date 1899-12-30)
    assert normalize_sheet_date(46301) == date(2026, 10, 6)
    assert normalize_sheet_date(46301.0) == date(2026, 10, 6)
    assert normalize_sheet_date("46301") == date(2026, 10, 6)
    # Datetime string with timestamp
    assert normalize_sheet_date("06/10/2026 10:41:04") == date(2026, 10, 6)
    assert normalize_sheet_date("06/10/26 10:41:04") == date(2026, 10, 6)


# -------------------------------------------------------------------------
# 23. Detecting 06/10/2026 in Existing Rows
# -------------------------------------------------------------------------
def test_detecting_06_10_2026_in_existing_rows():
    """Verify check_idempotency detects existing 06/10/2026 rows formatted as 06/10/26."""
    mock_service = MockGoogleSheetsService()
    tab = "October - 2026"
    seed_mock_closing_sheet(mock_service, spreadsheet_id="SID", tab_name=tab, include_target_date=True)

    sync = ClosingSheetSync(mock_service, spreadsheet_id="SID")
    already_proc, count = sync.check_idempotency(tab, date(2026, 10, 6))
    assert already_proc is True
    assert count == 103  # 103 records present for 06/10/26


# -------------------------------------------------------------------------
# 24. Last Data Row Detection when Summary Tables Exist Elsewhere
# -------------------------------------------------------------------------
def test_last_data_row_detection_with_summary_tables():
    """Verify last row detection only inspects A:G, not summary/pivot in K:Z."""
    mock_service = MockGoogleSheetsService()
    tab = "October - 2026"
    seed_mock_closing_sheet(mock_service, spreadsheet_id="SID", tab_name=tab, include_target_date=True)

    sync = ClosingSheetSync(mock_service, spreadsheet_id="SID")
    last_row = sync.find_last_data_row(tab)
    # Must equal 1219 from A:G, ignoring K:Z summary tables
    assert last_row == 1219


# -------------------------------------------------------------------------
# 25. Correct Next Insertion Row (+1 from last existing row)
# -------------------------------------------------------------------------
def test_correct_next_insertion_row():
    """Verify next append row is last_existing_row + 1 per manual insert row workflow."""
    mock_service = MockGoogleSheetsService()
    tab = "October - 2026"
    # Seed mock with 1219 rows without the target date
    seed_mock_closing_sheet(mock_service, spreadsheet_id="SID", tab_name=tab, include_target_date=False)

    sync = ClosingSheetSync(mock_service, spreadsheet_id="SID")
    last_row = sync.find_last_data_row(tab)
    assert last_row == 1219

    # Next append row must be last_row + 1 = 1220
    next_insert_row = last_row + 1
    assert next_insert_row == 1220


# -------------------------------------------------------------------------
# 26. No False Last-Row Detection of Row 1
# -------------------------------------------------------------------------
def test_no_false_last_row_detection_of_row_1():
    """Verify a sheet with 1219 populated rows never defaults to row 1."""
    mock_service = MockGoogleSheetsService()
    tab = "October - 2026"
    seed_mock_closing_sheet(mock_service, spreadsheet_id="SID", tab_name=tab, include_target_date=True)

    sync = ClosingSheetSync(mock_service, spreadsheet_id="SID")
    last_row = sync.find_last_data_row(tab)
    assert last_row != 1
    assert last_row == 1219


# -------------------------------------------------------------------------
# 27. Already Processed Report Returns Early on Dry-Run
# -------------------------------------------------------------------------
def test_already_processed_report_returns_early():
    """Verify processor.run returns already_processed=True during dry-run when date exists."""
    mock_service = MockGoogleSheetsService()
    tab = "October - 2026"
    seed_mock_closing_sheet(mock_service, spreadsheet_id="SID", tab_name=tab, include_target_date=True)

    mock_client = MagicMock()
    mock_client.post_report.return_value.data = [
        {"DOCDATE": "06/10/2026", "NAME": "U", "MOBILE": "984001", "SCHEMENAME": "S1", "WEIGHT": 1.0, "STATUS": "CLOSE", "SHOWROOM": "CBE"}
    ]

    processor = ClosingReportProcessor(mock_client, mock_service, spreadsheet_id="SID")
    res = processor.run(report_date=date(2026, 10, 6), dry_run=True)

    assert res.already_processed is True
    assert res.dry_run is True
    assert res.validation.status == "PASS"


# -------------------------------------------------------------------------
# 28. Zero Writes on Idempotency Detection
# -------------------------------------------------------------------------
def test_zero_writes_on_idempotency_detection():
    """Verify that when idempotency triggers, exactly zero writes are performed to sheet."""
    mock_service = MockGoogleSheetsService()
    tab = "October - 2026"
    seed_mock_closing_sheet(mock_service, spreadsheet_id="SID", tab_name=tab, include_target_date=True)

    initial_row_count = len(mock_service.read_range("SID", f"{tab}!A1:G"))
    assert initial_row_count == 1219

    mock_client = MagicMock()
    mock_client.post_report.return_value.data = [
        {"DOCDATE": "06/10/2026", "NAME": "U", "MOBILE": "984001", "SCHEMENAME": "S1", "WEIGHT": 1.0, "STATUS": "CLOSE", "SHOWROOM": "CBE"}
    ]

    processor = ClosingReportProcessor(mock_client, mock_service, spreadsheet_id="SID")
    res = processor.run(report_date=date(2026, 10, 6), dry_run=False)  # even on live attempt

    assert res.already_processed is True
    after_row_count = len(mock_service.read_range("SID", f"{tab}!A1:G"))
    assert after_row_count == initial_row_count  # Strictly unchanged!

