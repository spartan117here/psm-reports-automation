"""
Unit tests for reporting-date consistency, date propagation, stale data detection,
and date mismatch safeguards across the PSM reporting automation pipeline.
"""

from datetime import date, timedelta
from pathlib import Path
import pytest

from app.cli import build_parser, cmd_daily_pipeline, cmd_screenshot, parse_target_date, seed_mock_screenshot_data
from app.core.exceptions import TableValidationError, ScreenshotError
from app.google_sheets.client import MockGoogleSheetsService
from app.google_sheets.reconciliation import derive_monthly_tab_name
from app.reporting.closing import derive_closing_tab_name
from app.reporting.digi import derive_consolidation_tab_name
from app.reporting.screenshots import (
    GoogleSheetsScreenshotEngine,
    detect_and_validate_closing_range,
    detect_and_validate_enrollment_range,
)


# ==============================================================================
# 1. Previous-Day Rule & Date Resolution
# ==============================================================================
def test_previous_day_rule_default():
    """Verify parse_target_date(None) resolves strictly to yesterday (today - 1 day)."""
    expected = date.today() - timedelta(days=1)
    resolved = parse_target_date(None)
    assert resolved == expected


def test_explicit_target_date_parsing():
    """Verify parse_target_date parses ISO string YYYY-MM-DD correctly."""
    assert parse_target_date("2026-10-14") == date(2026, 10, 14)
    assert parse_target_date("2026-04-01") == date(2026, 4, 1)


# ==============================================================================
# 2. Date Propagation & Tab Name Derivation Across Stages
# ==============================================================================
def test_date_propagation_across_tabs():
    """Verify tab name derivation helpers correctly format destination sheet tabs from the target date."""
    target_date = date(2026, 10, 14)
    assert derive_monthly_tab_name("SS", target_date) == "SS - Oct"
    assert derive_monthly_tab_name("SV", target_date) == "SV - Oct"
    assert derive_consolidation_tab_name(target_date) == "Consolidate Report - Oct"
    assert derive_closing_tab_name(target_date) == "October - 2026"

    # Edge of month transition (November)
    nov_date = date(2026, 11, 1)
    assert derive_monthly_tab_name("SS", nov_date) == "SS - Nov"
    assert derive_consolidation_tab_name(nov_date) == "Consolidate Report - Nov"
    assert derive_closing_tab_name(nov_date) == "November - 2026"


# ==============================================================================
# 3. Stale Data Rejection & Date Mismatch Safeguards
# ==============================================================================
def test_screenshot_rejects_stale_enrollment_data():
    """Verify screenshot validation raises TableValidationError when Enrollment sheet is 1 day stale."""
    service = MockGoogleSheetsService()
    # Sheet displays 13/10/2026, but pipeline expects 14/10/2026
    seed_mock_screenshot_data(
        service,
        enrollment_date=date(2026, 10, 13),
        closing_date=date(2026, 10, 14),
    )

    with pytest.raises(TableValidationError, match="Stale enrollment data detected"):
        detect_and_validate_enrollment_range(
            sheets_service=service,
            spreadsheet_id="mock_enrollment_id",
            worksheet_name="Consolidate Report - Oct",
            expected_date=date(2026, 10, 14),
        )


def test_screenshot_rejects_stale_closing_data():
    """Verify screenshot validation raises TableValidationError when Closing sheet is 1 day stale."""
    service = MockGoogleSheetsService()
    seed_mock_screenshot_data(
        service,
        enrollment_date=date(2026, 10, 14),
        closing_date=date(2026, 10, 13),
    )

    with pytest.raises(TableValidationError, match="Stale closing data detected"):
        detect_and_validate_closing_range(
            sheets_service=service,
            spreadsheet_id="mock_closing_id",
            worksheet_name="October - 2026",
            expected_date=date(2026, 10, 14),
        )


def test_screenshot_engine_capture_all_rejects_mismatched_dates(tmp_path: Path):
    """Verify engine refuses to generate screenshots when Enrollment (13/10) and Closing (14/10) mismatch."""
    service = MockGoogleSheetsService()
    seed_mock_screenshot_data(
        service,
        enrollment_date=date(2026, 10, 13),
        closing_date=date(2026, 10, 14),
    )

    engine = GoogleSheetsScreenshotEngine(
        sheets_service=service,
        output_dir=tmp_path,
        dpi=150,
    )

    # When expected_date=2026-10-14 is passed, enrollment validation fails immediately
    with pytest.raises(TableValidationError, match="Stale enrollment data detected"):
        engine.capture_all(expected_date=date(2026, 10, 14))


def test_screenshot_engine_capture_all_synchronized_success(tmp_path: Path):
    """Verify screenshot capture succeeds when both reports are current for 14 October 2026."""
    service = MockGoogleSheetsService()
    target_date = date(2026, 10, 14)
    seed_mock_screenshot_data(
        service,
        report_date=target_date,
    )

    engine = GoogleSheetsScreenshotEngine(
        sheets_service=service,
        output_dir=tmp_path,
        dpi=150,
    )

    results = engine.capture_all(expected_date=target_date)
    assert len(results) == 2
    res_enrollment, res_closing = results[0], results[1]

    # Verify dates match exactly
    assert res_enrollment.report_date == target_date
    assert res_closing.report_date == target_date
    assert "20261014" in res_enrollment.output_path.name
    assert "20261014" in res_closing.output_path.name
    assert res_enrollment.output_path.exists()
    assert res_closing.output_path.exists()


# ==============================================================================
# 4. Unified Daily Pipeline & Stop-on-Failure Safeguard
# ==============================================================================
def test_cli_screenshot_enforces_target_date(tmp_path: Path):
    """Verify CLI screenshot command passes target date and rejects stale data."""
    parser = build_parser()
    # Expect 14 Oct, but mock data intentionally seeded with 13 Oct for enrollment
    args = parser.parse_args([
        "screenshot",
        "--report-date", "2026-10-14",
        "--target", "all",
        "--mock",
        "--output-dir", str(tmp_path),
    ])

    def mock_seed(srv, **kwargs):
        seed_mock_screenshot_data(
            srv,
            enrollment_date=date(2026, 10, 13),
            closing_date=date(2026, 10, 14),
        )

    from unittest.mock import patch
    with patch("app.cli.seed_mock_screenshot_data", side_effect=mock_seed):
        exit_code = cmd_screenshot(args)
        assert exit_code == 1  # Must fail closed


def test_cli_daily_pipeline_mock_success(tmp_path: Path):
    """Verify full 4-stage daily pipeline completes successfully for target date in mock mode."""
    parser = build_parser()
    args = parser.parse_args([
        "run-daily",
        "--report-date", "2026-10-14",
        "--mock",
        "--output-dir", str(tmp_path),
    ])

    exit_code = cmd_daily_pipeline(args)
    assert exit_code == 0

    # Verify screenshots were generated for 2026-10-14
    pngs = list(tmp_path.glob("*.png"))
    assert len(pngs) == 2
    filenames = [p.name for p in pngs]
    assert any("enrollment_report_20261014" in fn for fn in filenames)
    assert any("closing_rejoining_report_20261014" in fn for fn in filenames)
