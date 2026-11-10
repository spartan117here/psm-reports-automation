"""
Comprehensive Test Suite for Monthly Transition & Reliability Enhancements (Phase 7).

Covers 12 controlled test scenarios:
1. October historical worksheet discovery.
2. November live worksheet discovery.
3. Simulated December 2026 month transition.
4. Simulated January 2027 new-year transition.
5. Missing enrollment tab error handling (fail-closed).
6. Missing closing tab error handling (fail-closed).
7. Missing Digi raw tab error handling (fail-closed).
8. Worksheet header schema validation failure handling.
9. Business calendar operational day & weekend resolution.
10. Business calendar holiday skipping and lookback.
11. Innervex connection & read timeout retry with exponential backoff.
12. Delivery history idempotency & duplicate delivery prevention.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch
import pytest
import requests

from app.core.config import InnervexConfig, TelegramConfig
from app.delivery.base import DeliveryStatus
from app.delivery.telegram import TelegramDeliveryHistory, TelegramDeliveryChannel
from app.google_sheets.client import MockGoogleSheetsService
from app.google_sheets.monthly_source import (
    MonthlySourceDiscovery,
    MonthlySourceInfo,
    MonthlySourceNotFoundError,
    MonthlySourceHeaderValidationError,
)
from app.innervex.client import InnervexClient
from app.core.exceptions import InnervexConnectionError
from app.reporting.calendar import BusinessCalendar, CalendarConfig


ENR_ID = "mock_enrollment_id"
CLOSE_ID = "mock_closing_id"
DIGI_ID = "mock_digi_id"

VALID_SS_HEADER = [["COSTNAME", "CLIENTID", "GROUPCODE", "MSNO", "NAME", "SCHDATE", "COMMNAME", "COMMCODE", "RECAMOUNT", "MOBILENO", "ADDRESS", "EMPNAME", "SHOWROOM", "COMMCODE"]]
VALID_SV_HEADER = [["COSTNAME", "CLIENTID", "GROUPCODE", "MSNO", "NAME", "SCHDATE", "COMMNAME", "COMMCODE", "RECAMOUNT", "MOBILENO", "ADDRESS", "EMPNAME", "SHOWROOM", "COMMCODE"]]
VALID_CLOSE_HEADER = [["DOCDATE", "NAME", "PASSBOOK", "MOBILE", "SCHEMENAME", "WEIGHT", "STATUS", "SHOWROOM"]]
VALID_DIGI_HEADER = [["SNO", "LOCATION", "SCHDATE", "MSNO", "NAME", "SCHEME", "AMOUNT", "WEIGHT", "MOBILENO", "ADDRESS"]]


def _setup_mock_service_for_month(service: MockGoogleSheetsService, month_short: str, month_full: str, month_upper: str, year: int = 2026):
    """Seed MockGoogleSheetsService with valid metadata and headers for a target month."""
    service.set_metadata({
        "sheets": [
            {"properties": {"title": f"SS - {month_short}"}},
            {"properties": {"title": f"SV - {month_short}"}},
            {"properties": {"title": f"Consolidate Report - {month_short}"}},
        ]
    }, spreadsheet_id=ENR_ID)

    service.set_metadata({
        "sheets": [
            {"properties": {"title": f"{month_full} - {year}"}},
        ]
    }, spreadsheet_id=CLOSE_ID)

    service.set_metadata({
        "sheets": [
            {"properties": {"title": f"{month_upper}"}},
            {"properties": {"title": f"{month_upper} PIVOT"}},
        ]
    }, spreadsheet_id=DIGI_ID)

    # Seed valid headers
    service.sheets[f"{ENR_ID}:'SS - {month_short}'!A1:N1"] = VALID_SS_HEADER
    service.sheets[f"{ENR_ID}:'SV - {month_short}'!A1:N1"] = VALID_SV_HEADER
    service.sheets[f"{CLOSE_ID}:'{month_full} - {year}'!A1:H1"] = VALID_CLOSE_HEADER
    service.sheets[f"{DIGI_ID}:'{month_upper}'!A1:R1"] = VALID_DIGI_HEADER


# -----------------------------------------------------------------------------
# Test 1: October historical worksheet discovery
# -----------------------------------------------------------------------------
def test_1_october_historical_discovery():
    mock_service = MockGoogleSheetsService()
    _setup_mock_service_for_month(mock_service, "Oct", "October", "OCT", 2026)

    discovery = MonthlySourceDiscovery(mock_service)
    info = discovery.discover_sources(
        report_date=date(2026, 10, 30),
        enrollment_sheet_id=ENR_ID,
        closing_sheet_id=CLOSE_ID,
        digi_sheet_id=DIGI_ID,
        validate_headers=True,
    )

    assert info.ss_tab_name == "SS - Oct"
    assert info.sv_tab_name == "SV - Oct"
    assert info.closing_tab_name == "October - 2026"
    assert info.digi_raw_tab_name == "OCT"
    assert info.digi_pivot_tab_name == "OCT PIVOT"
    assert info.consolidation_tab_name == "Consolidate Report - Oct"


# -----------------------------------------------------------------------------
# Test 2: November live worksheet discovery
# -----------------------------------------------------------------------------
def test_2_november_live_discovery():
    mock_service = MockGoogleSheetsService()
    _setup_mock_service_for_month(mock_service, "Nov", "November", "NOV", 2026)

    discovery = MonthlySourceDiscovery(mock_service)
    info = discovery.discover_sources(
        report_date=date(2026, 11, 5),
        enrollment_sheet_id=ENR_ID,
        closing_sheet_id=CLOSE_ID,
        digi_sheet_id=DIGI_ID,
        validate_headers=True,
    )

    assert info.ss_tab_name == "SS - Nov"
    assert info.sv_tab_name == "SV - Nov"
    assert info.closing_tab_name == "November - 2026"
    assert info.digi_raw_tab_name == "NOV"
    assert info.digi_pivot_tab_name == "NOV PIVOT"


# -----------------------------------------------------------------------------
# Test 3: Simulated December 2026 month transition
# -----------------------------------------------------------------------------
def test_3_simulated_december_transition():
    mock_service = MockGoogleSheetsService()
    _setup_mock_service_for_month(mock_service, "Dec", "December", "DEC", 2026)

    discovery = MonthlySourceDiscovery(mock_service)
    info = discovery.discover_sources(
        report_date=date(2026, 12, 1),
        enrollment_sheet_id=ENR_ID,
        closing_sheet_id=CLOSE_ID,
        digi_sheet_id=DIGI_ID,
        validate_headers=True,
    )

    assert info.ss_tab_name == "SS - Dec"
    assert info.sv_tab_name == "SV - Dec"
    assert info.closing_tab_name == "December - 2026"
    assert info.digi_raw_tab_name == "DEC"


# -----------------------------------------------------------------------------
# Test 4: Simulated January 2027 new-year transition
# -----------------------------------------------------------------------------
def test_4_simulated_january_new_year_transition():
    mock_service = MockGoogleSheetsService()
    _setup_mock_service_for_month(mock_service, "Jan", "January", "JAN", 2027)

    discovery = MonthlySourceDiscovery(mock_service)
    info = discovery.discover_sources(
        report_date=date(2027, 1, 2),
        enrollment_sheet_id=ENR_ID,
        closing_sheet_id=CLOSE_ID,
        digi_sheet_id=DIGI_ID,
        validate_headers=True,
    )

    assert info.ss_tab_name == "SS - Jan"
    assert info.sv_tab_name == "SV - Jan"
    assert info.closing_tab_name == "January - 2027"
    assert info.digi_raw_tab_name == "JAN"
    assert info.year == 2027
    assert info.month == 1


# -----------------------------------------------------------------------------
# Test 5: Missing enrollment tab error handling (fail-closed)
# -----------------------------------------------------------------------------
def test_5_missing_enrollment_tab_error():
    mock_service = MockGoogleSheetsService()
    # Enrollment workbook only has October tabs, but query is for November
    _setup_mock_service_for_month(mock_service, "Oct", "October", "OCT", 2026)

    discovery = MonthlySourceDiscovery(mock_service)
    with pytest.raises(MonthlySourceNotFoundError) as exc_info:
        discovery.discover_sources(
            report_date=date(2026, 11, 1),
            enrollment_sheet_id=ENR_ID,
            closing_sheet_id=CLOSE_ID,
            digi_sheet_id=DIGI_ID,
            validate_headers=False,
        )
    assert "Required Subhiksham monthly tab for Nov 2026 not found" in str(exc_info.value)


# -----------------------------------------------------------------------------
# Test 6: Missing closing tab error handling (fail-closed)
# -----------------------------------------------------------------------------
def test_6_missing_closing_tab_error():
    mock_service = MockGoogleSheetsService()
    _setup_mock_service_for_month(mock_service, "Nov", "November", "NOV", 2026)
    # Clear closing workbook tabs
    mock_service.set_metadata({"sheets": []}, spreadsheet_id=CLOSE_ID)

    discovery = MonthlySourceDiscovery(mock_service)
    with pytest.raises(MonthlySourceNotFoundError) as exc_info:
        discovery.discover_sources(
            report_date=date(2026, 11, 1),
            enrollment_sheet_id=ENR_ID,
            closing_sheet_id=CLOSE_ID,
            digi_sheet_id=DIGI_ID,
            validate_headers=False,
        )
    assert "Required Closing tab for November 2026 not found" in str(exc_info.value)


# -----------------------------------------------------------------------------
# Test 7: Missing Digi raw tab error handling (fail-closed)
# -----------------------------------------------------------------------------
def test_7_missing_digi_raw_tab_error():
    mock_service = MockGoogleSheetsService()
    _setup_mock_service_for_month(mock_service, "Nov", "November", "NOV", 2026)
    # Clear Digi workbook tabs
    mock_service.set_metadata({"sheets": []}, spreadsheet_id=DIGI_ID)

    discovery = MonthlySourceDiscovery(mock_service)
    with pytest.raises(MonthlySourceNotFoundError) as exc_info:
        discovery.discover_sources(
            report_date=date(2026, 11, 1),
            enrollment_sheet_id=ENR_ID,
            closing_sheet_id=CLOSE_ID,
            digi_sheet_id=DIGI_ID,
            validate_headers=False,
        )
    assert "Required DigiGold/DigiSilver raw tab 'NOV' not found" in str(exc_info.value)


# -----------------------------------------------------------------------------
# Test 8: Worksheet header schema validation failure handling
# -----------------------------------------------------------------------------
def test_8_header_validation_failure():
    mock_service = MockGoogleSheetsService()
    _setup_mock_service_for_month(mock_service, "Nov", "November", "NOV", 2026)
    # Overwrite SS header with corrupted columns (missing SCHDATE and MOBILENO)
    mock_service.sheets[f"{ENR_ID}:'SS - Nov'!A1:N1"] = [["COL1", "COL2", "COL3"]]

    discovery = MonthlySourceDiscovery(mock_service)
    with pytest.raises(MonthlySourceHeaderValidationError) as exc_info:
        discovery.discover_sources(
            report_date=date(2026, 11, 1),
            enrollment_sheet_id=ENR_ID,
            closing_sheet_id=CLOSE_ID,
            digi_sheet_id=DIGI_ID,
            validate_headers=True,
        )
    assert "Missing required columns in 'SS - Nov'" in str(exc_info.value)


# -----------------------------------------------------------------------------
# Test 9: Business calendar operational day & weekend resolution
# -----------------------------------------------------------------------------
def test_9_business_calendar_standard_week():
    # Retail default: 7 days a week are active reporting days
    cal = BusinessCalendar()
    # Wednesday
    assert cal.is_reporting_day(date(2026, 11, 4)) is True
    # Saturday
    assert cal.is_reporting_day(date(2026, 11, 7)) is True
    # Sunday
    assert cal.is_reporting_day(date(2026, 11, 8)) is True

    # 5-day work week configuration
    config_5day = CalendarConfig(reporting_days=["mon", "tue", "wed", "thu", "fri"])
    cal_5day = BusinessCalendar(config_5day)
    assert cal_5day.is_reporting_day(date(2026, 11, 6)) is True   # Friday
    assert cal_5day.is_reporting_day(date(2026, 11, 7)) is False  # Saturday
    assert cal_5day.is_reporting_day(date(2026, 11, 8)) is False  # Sunday


# -----------------------------------------------------------------------------
# Test 10: Business calendar holiday skipping and lookback
# -----------------------------------------------------------------------------
def test_10_business_calendar_holiday_skip():
    # Configure 2026-11-04 as Deepavali holiday
    cfg = CalendarConfig(
        reporting_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        holidays=["2026-11-04"],
    )
    cal = BusinessCalendar(cfg)
    assert cal.is_reporting_day(date(2026, 11, 4)) is False
    assert cal.is_reporting_day(date(2026, 11, 3)) is True

    # When resolving from 2026-11-05 morning, previous day 2026-11-04 is holiday -> skips to 2026-11-03
    prev = cal.get_previous_reporting_day(date(2026, 11, 5))
    assert prev == date(2026, 11, 3)


# -----------------------------------------------------------------------------
# Test 11: Innervex connection & read timeout retry with exponential backoff
# -----------------------------------------------------------------------------
def test_11_innervex_timeout_retry():
    cfg = InnervexConfig(
        base_url="http://192.168.5.213:4499",
        timeout_seconds=2,
        max_retries=3,
        backoff_factor=1.0,
    )
    mock_auth = MagicMock()
    mock_auth.is_authenticated.return_value = True

    client = InnervexClient(config=cfg, auth_strategy=mock_auth)

    # Simulate 2 timeouts followed by 1 successful response
    mock_success_resp = MagicMock()
    mock_success_resp.status_code = 200
    mock_success_resp.text = '{"Success": true, "data": [{"row": 1}]}'
    mock_success_resp.json.return_value = {"Success": True, "data": [{"row": 1}]}

    with patch.object(client.session, "post", side_effect=[
        requests.exceptions.ReadTimeout("Read timed out"),
        requests.exceptions.ReadTimeout("Read timed out"),
        mock_success_resp,
    ]) as mock_post:
        resp = client.post_report("/SchemeClosingReport", {"Action": "test"})
        assert len(resp.data) == 1
        assert resp.data[0]["row"] == 1
        assert mock_post.call_count == 3

    # Simulate failure exceeding max_retries
    with patch.object(client.session, "post", side_effect=requests.exceptions.ReadTimeout("Exhausted")):
        with pytest.raises(InnervexConnectionError):
            client.post_report("/SchemeClosingReport", {"Action": "test"})


# -----------------------------------------------------------------------------
# Test 12: Delivery history idempotency & duplicate delivery prevention
# -----------------------------------------------------------------------------
def test_12_delivery_history_idempotency(tmp_path):
    hist_file = tmp_path / "test_delivery_history.json"
    history = TelegramDeliveryHistory(hist_file)

    test_date = date(2026, 11, 9)
    recipient = "1688169109"
    report_name = "enrollment_report_20261109.png"

    # Initially not delivered
    assert history.is_already_delivered(test_date, recipient, report_name) is False

    # Record delivery
    history.record_delivery(test_date, recipient, message_id="5501", attachments=[report_name])

    # Now marked as already delivered
    assert history.is_already_delivered(test_date, recipient, report_name) is True

    # Different report on same day is not delivered yet
    assert history.is_already_delivered(test_date, recipient, "closing_report_20261109.png") is False

    # Different recipient on same day is not delivered yet
    assert history.is_already_delivered(test_date, "9132692037", report_name) is False
