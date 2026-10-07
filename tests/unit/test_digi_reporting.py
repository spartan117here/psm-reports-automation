"""
Unit tests for DigiGold + DigiSilver Reporting and Consolidate Report Synchronization.
"""

from datetime import date, timedelta
from typing import Any, Dict, List
import pytest

from app.google_sheets.client import MockGoogleSheetsService
from app.reporting.digi import (
    DIGI_BRANCH_ORDER,
    ConsolidateReportUpdater,
    ConsolidateVerificationError,
    DigiDataUnavailableError,
    DigiExtractionResult,
    DigiReportProcessor,
    DigiSourceReader,
    DigiValidationError,
    calculate_report_date,
    derive_consolidation_tab_name,
    derive_digi_tab_names,
    normalize_branch_name,
)

# Reference sample data for 06/10/2026 verification
SAMPLE_GOLD_COUNTS = {
    "NELLAI": 249,
    "CHROMEPET": 539,
    "TRIVANDRUM": 580,
    "TRICHY": 492,
    "COIMBATORE": 574,
    "SALEM": 376,
    "POONAMALLEE": 122,
    "PADI": 516,
    "KANCHEEPURAM": 785,
    "ECOMMERCE": 1275,
}

SAMPLE_SILVER_COUNTS = {
    "NELLAI": 187,
    "CHROMEPET": 118,
    "TRIVANDRUM": 15,
    "TRICHY": 240,
    "COIMBATORE": 388,
    "SALEM": 181,
    "POONAMALLEE": 53,
    "PADI": 154,
    "KANCHEEPURAM": 152,
    "ECOMMERCE": 267,
}


def build_mock_raw_oct_sheet(dates: List[str]) -> List[List[Any]]:
    """Build mock OCT raw sheet with given SCHDATE values."""
    headers = [
        "COSTNAME", "CLIENTID", "GROUPCODE", "MSNO", "NAME", "SCHDATE",
        "REGAMOUNT", "RECAMOUNT", "MOBILENO", "SCHEME", "PHONE",
        "REFERRAL_EMP_CODE", "REFERRAL_EMPNAME", "CBRANCHNAME", "PROMOCODE",
        "PROMOAMOUNT", "EMPLOYEE NAME", "BRANCH"
    ]
    rows = [headers]
    for d in dates:
        rows.append(["COST", "CLI1", "GRP", "MS1", "NAME", d, "1000", "1000", "999", "DIGI GOLD", "999", "E1", "EMP", "BR", "", "", "EMP", "BRANCH"])
    return rows


def build_mock_oct_pivot_sheet(
    gold_counts: Dict[str, Any] = SAMPLE_GOLD_COUNTS,
    silver_counts: Dict[str, Any] = SAMPLE_SILVER_COUNTS,
    gold_branch_total: int = 4233,
    silver_branch_total: int = 1488,
) -> List[List[Any]]:
    """Build mock OCT Pivot sheet matching the production layout."""
    # Rows 1-3
    rows: List[List[Any]] = [
        [""],
        ["", "Digi Gold", "", "", "Digi Silver"],
        ["", "Branch", "COUNTA of CLIENTID", "", "Branch", "COUNTA of CLIENTID", "", "", "", "DigiGold New Enrollment as of", "", "", "", "", "06 Oct 2026", "", "DigiSilver New Enrollment as of", "", "", "", "", "06 Oct 2026"],
        ["", "", "", "", "", "", "", "", "", "Branch ", "Minimum Target ", "Achieved Count", "Gap", "Excess", "%", "", "Branch ", "Minimum Target ", "Achieved Count", "Gap", "Excess", "%"],
    ]

    branches_9 = [
        "NELLAI", "CHROMEPET", "TRIVANDRUM", "TRICHY", "COIMBATORE",
        "SALEM", "POONAMALLEE", "PADI", "KANCHEEPURAM"
    ]

    for b in branches_9:
        g_val = str(gold_counts.get(b, ""))
        s_val = str(silver_counts.get(b, ""))
        row = [""] * 22
        row[9] = b
        row[10] = "1000"
        row[11] = g_val
        row[16] = b
        row[17] = "500"
        row[18] = s_val
        rows.append(row)

    # Branch Total row
    bt_row = [""] * 22
    bt_row[9] = "BRANCH TOTAL "
    bt_row[11] = str(gold_branch_total)
    bt_row[16] = "BRANCH TOTAL "
    bt_row[18] = str(silver_branch_total)
    rows.append(bt_row)

    # Empty spacer row
    rows.append([""] * 22)

    # ECOMMERCE row
    ec_row = [""] * 22
    ec_row[9] = "ECOMMERCE"
    ec_row[11] = str(gold_counts.get("ECOMMERCE", ""))
    ec_row[16] = "ECOMMERCE"
    ec_row[18] = str(silver_counts.get("ECOMMERCE", ""))
    rows.append(ec_row)

    return rows


# ==============================================================================
# 1. Date and Name Calculation Tests
# ==============================================================================

def test_calculate_report_date():
    """Verify default to yesterday and explicit override."""
    yesterday = date.today() - timedelta(days=1)
    assert calculate_report_date() == yesterday

    explicit = date(2026, 10, 6)
    assert calculate_report_date(explicit) == explicit


def test_derive_tab_names():
    """Verify tab name generation for both source and destination workbooks."""
    raw_tab, pivot_tab = derive_digi_tab_names(date(2026, 10, 6))
    assert raw_tab == "OCT"
    assert pivot_tab == "OCT Pivot"

    raw_sep, pivot_sep = derive_digi_tab_names(date(2026, 9, 30))
    assert raw_sep == "SEP"
    assert pivot_sep == "SEP Pivot"

    assert derive_consolidation_tab_name(date(2026, 10, 6)) == "Consolidate Report - Oct"
    assert derive_consolidation_tab_name(date(2026, 9, 30)) == "Consolidate Report - Sept"


def test_branch_mapping_normalization():
    """Verify branch name alias normalization."""
    assert normalize_branch_name("NELLAI") == "NELLAI"
    assert normalize_branch_name("TVL") == "NELLAI"
    assert normalize_branch_name("Tirunelveli") == "NELLAI"
    assert normalize_branch_name("CHROMEPET") == "CHROMEPET"
    assert normalize_branch_name("CPT") == "CHROMEPET"
    assert normalize_branch_name("E COMM") == "ECOMMERCE"
    assert normalize_branch_name("E-COMMERCE") == "ECOMMERCE"
    assert normalize_branch_name("ecommerce ") == "ECOMMERCE"
    assert normalize_branch_name("KANCHI") == "KANCHEEPURAM"
    assert normalize_branch_name("BRANCH TOTAL ") == "BRANCH TOTAL"


# ==============================================================================
# 2. Source Extraction Tests
# ==============================================================================

def test_gold_and_silver_extraction_success():
    """Verify successful extraction of all 10 Gold and Silver branch values."""
    mock_service = MockGoogleSheetsService()
    digi_sid = "MOCK_DIGI_ID"

    # Setup raw OCT tab with 06/10/2026 records
    mock_service.sheets[f"{digi_sid}:OCT!A1:R"] = build_mock_raw_oct_sheet(["2026-10-06"] * 10)
    mock_service.sheets[f"{digi_sid}:OCT Pivot!A1:Z35"] = build_mock_oct_pivot_sheet()

    reader = DigiSourceReader(mock_service, spreadsheet_id=digi_sid)
    result = reader.read_and_validate(date(2026, 10, 6))

    assert result.report_date == date(2026, 10, 6)
    assert result.source_records_for_report_date == 10
    assert result.source_latest_schdate == date(2026, 10, 6)

    # Check Gold extraction
    for branch, expected in SAMPLE_GOLD_COUNTS.items():
        assert result.gold_values[branch] == expected

    # Check Silver extraction
    for branch, expected in SAMPLE_SILVER_COUNTS.items():
        assert result.silver_values[branch] == expected


def test_missing_branch_raises_error():
    """Verify validation error when any expected branch is missing."""
    mock_service = MockGoogleSheetsService()
    digi_sid = "MOCK_DIGI_ID"

    # Incomplete gold counts (missing POONAMALLEE)
    bad_gold = dict(SAMPLE_GOLD_COUNTS)
    del bad_gold["POONAMALLEE"]

    mock_service.sheets[f"{digi_sid}:OCT!A1:R"] = build_mock_raw_oct_sheet(["2026-10-06"])
    mock_service.sheets[f"{digi_sid}:OCT Pivot!A1:Z35"] = build_mock_oct_pivot_sheet(
        gold_counts=bad_gold
    )

    reader = DigiSourceReader(mock_service, spreadsheet_id=digi_sid)
    with pytest.raises(DigiValidationError) as exc:
        reader.read_and_validate(date(2026, 10, 6))
    assert "Missing DigiGold branches" in str(exc.value)
    assert "POONAMALLEE" in str(exc.value)


def test_non_numeric_value_raises_error():
    """Verify validation error when cell contains non-numeric text."""
    mock_service = MockGoogleSheetsService()
    digi_sid = "MOCK_DIGI_ID"

    bad_gold = dict(SAMPLE_GOLD_COUNTS)
    bad_gold["NELLAI"] = "CORRUPT_VALUE"

    mock_service.sheets[f"{digi_sid}:OCT!A1:R"] = build_mock_raw_oct_sheet(["2026-10-06"])
    mock_service.sheets[f"{digi_sid}:OCT Pivot!A1:Z35"] = build_mock_oct_pivot_sheet(gold_counts=bad_gold)

    reader = DigiSourceReader(mock_service, spreadsheet_id=digi_sid)
    with pytest.raises(DigiValidationError) as exc:
        reader.read_and_validate(date(2026, 10, 6))
    assert "Non-numeric count 'CORRUPT_VALUE'" in str(exc.value)


def test_source_data_unavailable_date_behind():
    """Verify halt when latest source date is older than target report date."""
    mock_service = MockGoogleSheetsService()
    digi_sid = "MOCK_DIGI_ID"

    # Raw sheet only has data up to 2026-10-05
    mock_service.sheets[f"{digi_sid}:OCT!A1:R"] = build_mock_raw_oct_sheet(["2026-10-05"])
    mock_service.sheets[f"{digi_sid}:OCT Pivot!A1:Z35"] = build_mock_oct_pivot_sheet()

    reader = DigiSourceReader(mock_service, spreadsheet_id=digi_sid)
    # Requested date is 2026-10-06
    with pytest.raises(DigiDataUnavailableError) as exc:
        reader.read_and_validate(date(2026, 10, 6))
    assert "Digi data for expected report date 2026-10-06 is not available yet" in str(exc.value)


def test_source_data_unavailable_empty_raw_sheet():
    """Verify halt when raw sheet has no data."""
    mock_service = MockGoogleSheetsService()
    digi_sid = "MOCK_DIGI_ID"

    mock_service.sheets[f"{digi_sid}:OCT!A1:R"] = []

    reader = DigiSourceReader(mock_service, spreadsheet_id=digi_sid)
    with pytest.raises(DigiDataUnavailableError) as exc:
        reader.read_and_validate(date(2026, 10, 6))
    assert "completely empty" in str(exc.value)


def test_pivot_branch_total_row_is_ignored():
    """The pivot BRANCH TOTAL row is not part of the manual workflow and must not block the copy."""
    mock_service = MockGoogleSheetsService()
    digi_sid = "MOCK_DIGI_ID"

    mock_service.sheets[f"{digi_sid}:OCT!A1:R"] = build_mock_raw_oct_sheet(["2026-10-06"])
    # BRANCH TOTAL deliberately inconsistent with branch values
    mock_service.sheets[f"{digi_sid}:OCT Pivot!A1:Z35"] = build_mock_oct_pivot_sheet(
        gold_branch_total=9999, silver_branch_total=1
    )

    reader = DigiSourceReader(mock_service, spreadsheet_id=digi_sid)
    result = reader.read_and_validate(date(2026, 10, 6))

    assert result.gold_values == SAMPLE_GOLD_COUNTS
    assert result.silver_values == SAMPLE_SILVER_COUNTS
    assert "BRANCH TOTAL" not in result.gold_values
    assert "BRANCH TOTAL" not in result.silver_values


# ==============================================================================
# 3. Destination Writing and Verification Tests
# ==============================================================================

def test_destination_update_and_verification():
    """Verify destination update writes I20:J29, L20:L30, E3 and passes verification."""
    mock_service = MockGoogleSheetsService()
    dest_sid = "MOCK_DEST_ID"
    tab_name = "Consolidate Report - Oct"

    # Pre-populate surrounding cells and existing formulas to simulate real sheet
    mock_service.sheets[f"{dest_sid}:{tab_name}!E3"] = [["New Enrollment as on 05/10/2026"]]
    mock_service.sheets[f"{dest_sid}:{tab_name}!I20:J29"] = [["100", "50"] for _ in range(10)]
    mock_service.formulas[f"{dest_sid}:{tab_name}!L20:L30"] = [[f"=K{r}/5"] for r in range(20, 31)]
    # Populate surrounding formula range G20:K30
    mock_service.formulas[f"{dest_sid}:{tab_name}!G20:K30"] = [
        ["=G", "=H", "100", "50", f"=G{r}+H{r}+I{r}+J{r}"] for r in range(20, 31)
    ]

    updater = ConsolidateReportUpdater(mock_service, spreadsheet_id=dest_sid)
    extraction = DigiExtractionResult(
        report_date=date(2026, 10, 6),
        gold_values=SAMPLE_GOLD_COUNTS,
        silver_values=SAMPLE_SILVER_COUNTS,
    )

    result = updater.update_consolidate_report(extraction, tab_name=tab_name)

    assert result.verified is True
    assert result.report_title == "New Enrollment as on 06/10/2026"
    assert result.avg_divisor == 6

    # Verify I20:J29 in mock sheet
    written_data = mock_service.sheets[f"{dest_sid}:{tab_name}!I20:J29"]
    assert len(written_data) == 10
    for i, branch in enumerate(DIGI_BRANCH_ORDER):
        assert written_data[i][0] == SAMPLE_GOLD_COUNTS[branch]
        assert written_data[i][1] == SAMPLE_SILVER_COUNTS[branch]

    # Verify L20:L30 formulas
    written_avg = mock_service.sheets[f"{dest_sid}:{tab_name}!L20:L30"]
    assert len(written_avg) == 11
    for i, r in enumerate(range(20, 31)):
        assert written_avg[i][0] == f"=K{r}/6"

    # Verify E3 title
    written_title = mock_service.sheets[f"{dest_sid}:{tab_name}!E3"]
    assert written_title[0][0] == "New Enrollment as on 06/10/2026"


def test_avg_divisor_dynamic_calculation():
    """Verify AVG divisor equals day of month for different dates."""
    mock_service = MockGoogleSheetsService()
    dest_sid = "MOCK_DEST_ID"
    tab_name = "Consolidate Report - Oct"

    mock_service.formulas[f"{dest_sid}:{tab_name}!G20:K30"] = [
        ["=G", "=H", "100", "50", f"=G{r}+H{r}+I{r}+J{r}"] for r in range(20, 31)
    ]

    updater = ConsolidateReportUpdater(mock_service, spreadsheet_id=dest_sid)

    # Test day 7 (07/10/2026) -> divisor = 7
    extraction_7 = DigiExtractionResult(
        report_date=date(2026, 10, 7),
        gold_values=SAMPLE_GOLD_COUNTS,
        silver_values=SAMPLE_SILVER_COUNTS,
    )
    updater.update_consolidate_report(extraction_7, tab_name=tab_name)
    written_avg_7 = mock_service.sheets[f"{dest_sid}:{tab_name}!L20:L30"]
    assert written_avg_7[0][0] == "=K20/7"
    assert written_avg_7[10][0] == "=K30/7"

    # Test day 15 (15/10/2026) -> divisor = 15
    extraction_15 = DigiExtractionResult(
        report_date=date(2026, 10, 15),
        gold_values=SAMPLE_GOLD_COUNTS,
        silver_values=SAMPLE_SILVER_COUNTS,
    )
    updater.update_consolidate_report(extraction_15, tab_name=tab_name)
    written_avg_15 = mock_service.sheets[f"{dest_sid}:{tab_name}!L20:L30"]
    assert written_avg_15[0][0] == "=K20/15"


def test_idempotent_repeated_execution():
    """Verify running the update twice yields identical results and no corruption."""
    mock_service = MockGoogleSheetsService()
    dest_sid = "MOCK_DEST_ID"
    tab_name = "Consolidate Report - Oct"

    mock_service.formulas[f"{dest_sid}:{tab_name}!G20:K30"] = [
        ["=G", "=H", "100", "50", f"=G{r}+H{r}+I{r}+J{r}"] for r in range(20, 31)
    ]

    updater = ConsolidateReportUpdater(mock_service, spreadsheet_id=dest_sid)
    extraction = DigiExtractionResult(
        report_date=date(2026, 10, 6),
        gold_values=SAMPLE_GOLD_COUNTS,
        silver_values=SAMPLE_SILVER_COUNTS,
    )

    # Run 1
    updater.update_consolidate_report(extraction, tab_name=tab_name)
    state_after_run_1_data = list(mock_service.sheets[f"{dest_sid}:{tab_name}!I20:J29"])
    state_after_run_1_avg = list(mock_service.sheets[f"{dest_sid}:{tab_name}!L20:L30"])
    state_after_run_1_title = list(mock_service.sheets[f"{dest_sid}:{tab_name}!E3"])

    # Run 2
    updater.update_consolidate_report(extraction, tab_name=tab_name)
    state_after_run_2_data = list(mock_service.sheets[f"{dest_sid}:{tab_name}!I20:J29"])
    state_after_run_2_avg = list(mock_service.sheets[f"{dest_sid}:{tab_name}!L20:L30"])
    state_after_run_2_title = list(mock_service.sheets[f"{dest_sid}:{tab_name}!E3"])

    assert state_after_run_1_data == state_after_run_2_data
    assert state_after_run_1_avg == state_after_run_2_avg
    assert state_after_run_1_title == state_after_run_2_title


def test_dry_run_mode_does_not_mutate():
    """Verify dry_run mode performs validation without mutating mock sheets."""
    mock_service = MockGoogleSheetsService()
    dest_sid = "MOCK_DEST_ID"
    tab_name = "Consolidate Report - Oct"

    updater = ConsolidateReportUpdater(mock_service, spreadsheet_id=dest_sid)
    extraction = DigiExtractionResult(
        report_date=date(2026, 10, 6),
        gold_values=SAMPLE_GOLD_COUNTS,
        silver_values=SAMPLE_SILVER_COUNTS,
    )

    result = updater.update_consolidate_report(extraction, tab_name=tab_name, dry_run=True)
    assert result.dry_run is True
    assert result.verified is True

    # Ensure nothing was written to mock sheets
    assert f"{dest_sid}:{tab_name}!I20:J29" not in mock_service.sheets
    assert f"{dest_sid}:{tab_name}!L20:L30" not in mock_service.sheets


def test_verification_detects_formula_error():
    """Verify verification fails if a formula error (#REF!, #VALUE!) is returned in read-back."""
    mock_service = MockGoogleSheetsService()
    dest_sid = "MOCK_DEST_ID"
    tab_name = "Consolidate Report - Oct"

    mock_service.formulas[f"{dest_sid}:{tab_name}!G20:K30"] = [
        ["=G", "=H", "100", "50", f"=G{r}+H{r}+I{r}+J{r}"] for r in range(20, 31)
    ]

    updater = ConsolidateReportUpdater(mock_service, spreadsheet_id=dest_sid)
    extraction = DigiExtractionResult(
        report_date=date(2026, 10, 6),
        gold_values=SAMPLE_GOLD_COUNTS,
        silver_values=SAMPLE_SILVER_COUNTS,
    )

    # Perform actual update so valid data is written
    updater.update_consolidate_report(extraction, tab_name=tab_name, dry_run=False)

    # Simulate a #REF! appearing in the computed value of a cell this step wrote (L20)
    avg_values = [["103"] for _ in range(11)]
    avg_values[0] = ["#REF!"]
    mock_service.sheets[f"{dest_sid}:{tab_name}!L20:L30"] = avg_values

    with pytest.raises(ConsolidateVerificationError) as exc:
        updater.verify_destination_state(tab_name, extraction, 6, "New Enrollment as on 06/10/2026")
    assert "#REF!" in str(exc.value)


def test_total_row_cells_never_written():
    """I30, J30 and K30 are not part of the Digi step and must never be written or cleared."""
    mock_service = MockGoogleSheetsService()
    dest_sid = "MOCK_DEST_ID"
    tab_name = "Consolidate Report - Oct"

    written_ranges = []
    cleared_ranges = []
    orig_update = mock_service.update_range
    orig_clear = mock_service.clear_range

    def spy_update(sid, rng, values):
        written_ranges.append(rng)
        return orig_update(sid, rng, values)

    def spy_clear(sid, rng):
        cleared_ranges.append(rng)
        return orig_clear(sid, rng)

    mock_service.update_range = spy_update
    mock_service.clear_range = spy_clear

    updater = ConsolidateReportUpdater(mock_service, spreadsheet_id=dest_sid)
    extraction = DigiExtractionResult(
        report_date=date(2026, 10, 6),
        gold_values=SAMPLE_GOLD_COUNTS,
        silver_values=SAMPLE_SILVER_COUNTS,
    )
    updater.update_consolidate_report(extraction, tab_name=tab_name)

    assert cleared_ranges == [f"{tab_name}!I20:J29"]
    assert sorted(written_ranges) == sorted([
        f"{tab_name}!I20:J29",
        f"{tab_name}!L20:L30",
        f"{tab_name}!E3",
    ])

    # Data payload is exactly 10 rows (rows 20-29), never reaching row 30
    assert len(mock_service.sheets[f"{dest_sid}:{tab_name}!I20:J29"]) == 10


# ==============================================================================
# 4. End-to-End Orchestrator Test
# ==============================================================================

def test_digi_report_processor_end_to_end():
    """Verify complete DigiReportProcessor pipeline with mock services."""
    source_service = MockGoogleSheetsService()
    dest_service = MockGoogleSheetsService()

    digi_sid = "MOCK_DIGI_ID"
    dest_sid = "MOCK_DEST_ID"
    tab_name = "Consolidate Report - Oct"

    # Pre-populate source
    source_service.sheets[f"{digi_sid}:OCT!A1:R"] = build_mock_raw_oct_sheet(["2026-10-06"] * 5)
    source_service.sheets[f"{digi_sid}:OCT Pivot!A1:Z35"] = build_mock_oct_pivot_sheet()

    # Pre-populate dest surrounding formulas
    dest_service.formulas[f"{dest_sid}:{tab_name}!G20:K30"] = [
        ["=G", "=H", "100", "50", f"=G{r}+H{r}+I{r}+J{r}"] for r in range(20, 31)
    ]

    processor = DigiReportProcessor(
        source_service=source_service,
        dest_service=dest_service,
        source_spreadsheet_id=digi_sid,
        dest_spreadsheet_id=dest_sid,
    )

    result = processor.run(report_date=date(2026, 10, 6), destination_tab=tab_name)

    assert result.verified is True
    assert result.avg_divisor == 6
    assert result.report_title == "New Enrollment as on 06/10/2026"
    assert result.gold_values["ECOMMERCE"] == 1275
    assert result.silver_values["ECOMMERCE"] == 267
