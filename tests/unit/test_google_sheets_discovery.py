"""Unit tests for Google Sheets read-only discovery engine and mapping analysis."""

import argparse
from typing import Any, Dict, List
import pytest

from app.cli import cmd_sheets_discover
from app.google_sheets.client import MockGoogleSheetsService
from app.google_sheets.discovery import (
    CLEANED_11_COLUMNS,
    EXPECTED_MONTHLY_COLUMNS,
    SheetDiscoveryEngine,
    redact_spreadsheet_id,
    sanitize_value,
)


def test_redact_spreadsheet_id():
    """Verify spreadsheet ID redaction keeps prefixes and suffixes while masking the middle."""
    assert redact_spreadsheet_id("1AbCdEfGhIjKlMnOpQrStUvWxYz") == "1AbC...WxYz"
    assert redact_spreadsheet_id("short") == "****"
    assert redact_spreadsheet_id("") == ""
    assert redact_spreadsheet_id("12345678") == "****"
    assert redact_spreadsheet_id("123456789") == "1234...6789"


def test_sanitize_value_masks_pii():
    """Verify customer PII (names, mobiles, addresses) are masked properly."""
    # Names
    assert sanitize_value("NAME", "RAMESH") == "R****H"
    assert sanitize_value("CUSTNAME", "AB") == "***"
    assert sanitize_value("COMMNAME", "K. SURESH") == "K. SURESH"  # Employee name not masked

    # Mobile numbers
    assert sanitize_value("MOBILENO", "9876543210") == "******3210"
    assert sanitize_value("PHONE", "123") == "******"

    # Addresses
    assert sanitize_value("ADDRESS", "123 Main Street") == "[REDACTED_PII]"

    # Non-sensitive values preserved
    assert sanitize_value("COSTNAME", "CHROMEPET") == "CHROMEPET"
    assert sanitize_value("RECAMOUNT", "5000.00") == "5000.00"
    assert sanitize_value("SCHDATE", "2026-10-04") == "2026-10-04"
    assert sanitize_value("SCHEME", "NEW SWARNA SUBHIKSHAM") == "NEW SWARNA SUBHIKSHAM"


def test_analyze_column_mapping():
    """Verify 11 cleaned columns and 3 enriched columns map accurately to A-N."""
    analysis = SheetDiscoveryEngine._analyze_column_mapping(EXPECTED_MONTHLY_COLUMNS)
    assert analysis["total_sheet_columns"] == 14
    mappings = {m["column_letter"]: m for m in analysis["mappings"]}

    # Direct 11 columns
    assert mappings["A"]["source_field"] == "COSTNAME"
    assert mappings["A"]["status"] == "DIRECT"
    assert mappings["B"]["source_field"] == "CLIENTID"
    assert mappings["C"]["source_field"] == "GROUPCODE"
    assert mappings["D"]["source_field"] == "MSNO"
    assert mappings["E"]["source_field"] == "NAME"
    assert mappings["F"]["source_field"] == "SCHDATE"
    assert mappings["I"]["source_field"] == "RECAMOUNT"
    assert mappings["J"]["source_field"] == "MOBILENO"
    assert mappings["K"]["source_field"] == "SCHEME"
    assert mappings["L"]["source_field"] == "COMMNAME"
    assert mappings["M"]["source_field"] == "COMMCODE"

    # Enriched columns
    assert mappings["G"]["source_field"] == "Enriched (LocationResolver)"
    assert mappings["G"]["status"] == "ENRICHED"
    assert mappings["H"]["source_field"] == "Enriched (LocationResolver)"
    assert mappings["H"]["status"] == "ENRICHED"
    assert mappings["N"]["source_field"] == "Enriched (Helper)"
    assert mappings["N"]["status"] == "HELPER_CONCAT"


def test_check_october_data_detection():
    """Verify detection of existing October tabs and October records."""
    mock_service = MockGoogleSheetsService()
    engine = SheetDiscoveryEngine(mock_service)

    # 1. Neither tab nor records present
    res1 = engine._check_october_data("MOCK_ID", ["Employees", "SS - Sept", "SV - Sept"])
    assert res1["has_october_tab"] is False
    assert res1["status"] == "TAB_NOT_CREATED_YET"
    assert res1["october_records_in_september_tab"] == 0

    # 2. October tab already present
    res2 = engine._check_october_data("MOCK_ID", ["Employees", "SS - Sept", "SS - Oct"])
    assert res2["has_october_tab"] is True
    assert res2["status"] == "TAB_EXISTS"

    # 3. October records present inside SS - Sept tab
    mock_service.sheets["MOCK_ID:SS - Sept!F:F"] = [
        ["SCHDATE"],
        ["2026-09-30"],
        ["2026-10-01"],
        ["2026-10-04"],
    ]
    res3 = engine._check_october_data("MOCK_ID", ["Employees", "SS - Sept"])
    assert res3["october_records_in_september_tab"] == 2


def test_inspect_workbook_full():
    """Verify full end-to-end read-only inspection of a mock workbook."""
    sid = "TEST_WORKBOOK_123"
    meta = {
        "properties": {"title": "New Enrollment from April 2026"},
        "sheets": [
            {"properties": {"title": "Employees", "sheetId": 1, "gridProperties": {"rowCount": 100, "columnCount": 6}}},
            {"properties": {"title": "SS - Sept", "sheetId": 2, "gridProperties": {"rowCount": 500, "columnCount": 14}}},
            {"properties": {"title": "SV - Sept", "sheetId": 3, "gridProperties": {"rowCount": 200, "columnCount": 14}}},
            {"properties": {"title": "Consolidate Report - Sept", "sheetId": 4, "gridProperties": {"rowCount": 40, "columnCount": 15}}},
        ],
    }
    mock_service = MockGoogleSheetsService(meta)
    # Populate tab data
    mock_service.sheets[f"{sid}:SS - Sept!A1:Z1"] = [EXPECTED_MONTHLY_COLUMNS]
    mock_service.sheets[f"{sid}:SS - Sept!A1:Z10"] = [
        EXPECTED_MONTHLY_COLUMNS,
        ["CPT", "CLI-01", "GRP-SS", "MSNO-01", "A. RAMESH", "2026-09-30", "CHROMEPET", "CHROMEPET", "5000", "9876543210", "NEW SWARNA SUBHIKSHAM", "K. SURESH", "2998", "2998 - K. SURESH"],
    ]
    mock_service.formulas[f"{sid}:SS - Sept!A1:Z10"] = [
        [],
        ["", "", "", "", "", "", "", "", "", "", "", "", "", '=M2&" - "&L2']
    ]
    mock_service.sheets[f"{sid}:SS - Sept!D:D"] = [["MSNO"], ["MSNO-01"], ["MSNO-02"]]
    mock_service.sheets[f"{sid}:Employees!A1:Z1"] = [["EMPCODE", "EMPNAME", "BRANCH", "LOCATION"]]
    mock_service.sheets[f"{sid}:Employees!A:A"] = [["EMPCODE"], ["101"], ["102"]]

    engine = SheetDiscoveryEngine(mock_service)
    report = engine.inspect_workbook(sid)

    assert report["spreadsheet_title"] == "New Enrollment from April 2026"
    assert report["redacted_spreadsheet_id"] == "TEST..._123"
    assert report["total_sheets"] == 4
    assert len(report["categorized_tabs"]["employees"]) == 1
    assert "SS - Sept" in report["categorized_tabs"]["subhiksham_monthly"]
    assert "SV - Sept" in report["categorized_tabs"]["viruksham_monthly"]
    assert "Consolidate Report - Sept" in report["categorized_tabs"]["consolidation"]

    ss_insp = report["subhiksham_inspection"]
    assert ss_insp["tab_name"] == "SS - Sept"
    assert ss_insp["detected_column_count"] == 14
    assert ss_insp["estimated_data_row_count"] == 2
    assert ss_insp["sample_formulas"]["N2"] == '=M2&" - "&L2'

    # Sanitization check on sample row
    sample_row = ss_insp["sample_rows_sanitized"][0]
    assert sample_row[4] == "A*******H"  # Name masked
    assert sample_row[9] == "******3210"  # Mobile masked


def test_cli_sheets_discover_missing_config(monkeypatch, capsys):
    """Verify CLI reports missing configuration cleanly when environment is empty."""
    from unittest.mock import patch

    with patch("app.core.config.load_dotenv"):
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_PATH", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
        monkeypatch.delenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID", raising=False)

        args = argparse.Namespace(
            command="sheets-discover",
            spreadsheet_id=None,
            service_account=None,
            mock=False,
        )
        exit_code = cmd_sheets_discover(args)
        captured = capsys.readouterr()

        assert exit_code == 1
        assert "Status: CONFIGURATION_MISSING" in captured.out
        assert "Identified Missing Configuration:" in captured.out
        assert "GOOGLE_SHEET_NEW_ENROLLMENT_ID" in captured.out
        assert "GOOGLE_SERVICE_ACCOUNT_PATH" in captured.out


def test_cli_sheets_discover_mock(capsys):
    """Verify CLI executes simulated discovery when --mock flag is passed."""
    args = argparse.Namespace(
        command="sheets-discover",
        spreadsheet_id=None,
        service_account=None,
        mock=True,
    )
    exit_code = cmd_sheets_discover(args)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Status: MOCK_MODE" in captured.out
    assert "New Enrollment from April 2026" in captured.out
    assert "Write operation performed: NO" in captured.out
