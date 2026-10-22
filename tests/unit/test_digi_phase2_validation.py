"""Unit tests for Phase 2 DigiGold and DigiSilver validation, pivot math, timing window, and tracker."""

from datetime import date, datetime
from pathlib import Path
import pytest

from app.google_sheets.client import MockGoogleSheetsService
from app.reporting.digi import (
    DigiSourceReader,
    DigiValidationError,
    DigiValidationRecord,
    DigiValidationStatus,
    DigiValidationTracker,
    evaluate_digi_timing_window,
    extract_pivot_header_date,
    validate_pivot_subtotals_and_totals,
    SHOWROOM_BRANCHES_9,
)
from tests.unit.test_digi_reporting import (
    SAMPLE_GOLD_COUNTS,
    SAMPLE_SILVER_COUNTS,
    build_mock_oct_pivot_sheet,
    build_mock_raw_oct_sheet,
)


def test_validate_pivot_subtotals_and_totals_success():
    """Verify that consistent pivot data passes showroom and overall math verification."""
    # Sum of 9 showrooms in SAMPLE_GOLD_COUNTS
    gold_sr = sum(SAMPLE_GOLD_COUNTS[b] for b in SHOWROOM_BRANCHES_9)  # 4233
    gold_total = gold_sr + SAMPLE_GOLD_COUNTS["ECOMMERCE"]            # 5508
    silver_sr = sum(SAMPLE_SILVER_COUNTS[b] for b in SHOWROOM_BRANCHES_9)  # 1488
    silver_total = silver_sr + SAMPLE_SILVER_COUNTS["ECOMMERCE"]          # 1755

    # Should not raise any error
    validate_pivot_subtotals_and_totals(
        gold_values=SAMPLE_GOLD_COUNTS,
        silver_values=SAMPLE_SILVER_COUNTS,
        gold_branch_total=gold_sr,
        silver_branch_total=silver_sr,
        gold_grand_total=gold_total,
        silver_grand_total=silver_total,
    )


def test_extract_pivot_header_date_formats():
    """Verify regex extraction of dates from various pivot header formats."""
    grid1 = [["", "", "", ""], ["SCHEME", "Oct", "as of 20 Oct 2026", "Total"]]
    assert extract_pivot_header_date(grid1) == date(2026, 10, 20)

    grid2 = [["", "", "", ""], ["SCHEME", "Oct", "as on 14/10/2026", "Total"]]
    assert extract_pivot_header_date(grid2) == date(2026, 10, 14)

    grid3 = [["", "", "", ""], ["SCHEME", "Oct", "as of 2026-10-15", "Total"]]
    assert extract_pivot_header_date(grid3) == date(2026, 10, 15)

    # Missing header date
    grid_no_date = [["", "", "", ""], ["SCHEME", "Oct", "Status Table", "Total"]]
    assert extract_pivot_header_date(grid_no_date) is None


def test_validate_pivot_subtotal_mismatch_raises_error():
    """Verify that a discrepancy in showroom subtotal raises DigiValidationError."""
    gold_sr = sum(SAMPLE_GOLD_COUNTS[b] for b in SHOWROOM_BRANCHES_9)
    silver_sr = sum(SAMPLE_SILVER_COUNTS[b] for b in SHOWROOM_BRANCHES_9)

    with pytest.raises(DigiValidationError, match="DigiGold showroom subtotal mismatch"):
        validate_pivot_subtotals_and_totals(
            gold_values=SAMPLE_GOLD_COUNTS,
            silver_values=SAMPLE_SILVER_COUNTS,
            gold_branch_total=gold_sr + 100,  # Mismatch!
            silver_branch_total=silver_sr,
            gold_grand_total=5508,
            silver_grand_total=1755,
        )


def test_validate_pivot_ecommerce_mismatch_raises_error():
    """Verify that grand total != showroom + ecommerce raises DigiValidationError."""
    gold_sr = sum(SAMPLE_GOLD_COUNTS[b] for b in SHOWROOM_BRANCHES_9)
    silver_sr = sum(SAMPLE_SILVER_COUNTS[b] for b in SHOWROOM_BRANCHES_9)

    with pytest.raises(DigiValidationError, match="DigiGold overall total mismatch"):
        validate_pivot_subtotals_and_totals(
            gold_values=SAMPLE_GOLD_COUNTS,
            silver_values=SAMPLE_SILVER_COUNTS,
            gold_branch_total=gold_sr,
            silver_branch_total=silver_sr,
            gold_grand_total=9999,  # Mismatch!
            silver_grand_total=1755,
        )


def test_reader_extract_pivot_data_with_subtotals():
    """Verify DigiSourceReader extracts pivot data including subtotals and header date."""
    mock_service = MockGoogleSheetsService()
    digi_sid = "MOCK_DIGI_ID"
    mock_grid = build_mock_oct_pivot_sheet()
    gt_row = [""] * 22
    gt_row[9] = "GRAND TOTAL"
    gt_row[11] = "5,508"
    gt_row[16] = "GRAND TOTAL"
    gt_row[18] = "1,755"
    mock_grid.append(gt_row)
    mock_service.sheets[f"{digi_sid}:OCT Pivot!A1:Z35"] = mock_grid

    reader = DigiSourceReader(mock_service, spreadsheet_id=digi_sid)
    extraction = reader.extract_pivot_data("OCT Pivot")

    assert extraction.gold_showroom_subtotal == 4233
    assert extraction.silver_showroom_subtotal == 1488
    assert extraction.gold_grand_total == 5508
    assert extraction.silver_grand_total == 1755
    assert extraction.pivot_date_header == date(2026, 10, 6)
    assert len(extraction.gold_values) == 10
    assert len(extraction.silver_values) == 10


def test_digi_timing_window():
    """Verify timing window: before 11:00 AM IST blocked, 11:00-12:30 valid, after 12:30 cutoff."""
    # Before 11:00 AM IST
    early_dt = datetime(2026, 10, 20, 10, 30, 0)
    allowed, st, msg = evaluate_digi_timing_window(early_dt)
    assert allowed is False
    assert st == DigiValidationStatus.TOO_EARLY
    assert "before scheduled start time" in msg

    # Within 11:00 AM - 12:30 PM IST window
    valid_dt = datetime(2026, 10, 20, 11, 15, 0)
    allowed, st, msg = evaluate_digi_timing_window(valid_dt)
    assert allowed is True
    assert st == DigiValidationStatus.SUCCESS
    assert "within operational window" in msg

    # Exactly at start: 11:00 AM IST
    exact_start = datetime(2026, 10, 20, 11, 0, 0)
    allowed, st, msg = evaluate_digi_timing_window(exact_start)
    assert allowed is True
    assert st == DigiValidationStatus.SUCCESS

    # Exactly at cutoff: 12:30 PM IST
    exact_cutoff = datetime(2026, 10, 20, 12, 30, 0)
    allowed, st, msg = evaluate_digi_timing_window(exact_cutoff)
    assert allowed is True
    assert st == DigiValidationStatus.SUCCESS

    # After cutoff: 12:35 PM IST
    late_dt = datetime(2026, 10, 20, 12, 35, 0)
    allowed, st, msg = evaluate_digi_timing_window(late_dt)
    assert allowed is False
    assert st == DigiValidationStatus.BLOCKED_CUTOFF
    assert "exceeds cutoff time" in msg

    # Force bypasses timing window restrictions
    allowed_forced, st_forced, msg_forced = evaluate_digi_timing_window(early_dt, force=True)
    assert allowed_forced is True
    assert st_forced == DigiValidationStatus.SUCCESS
    assert "bypasses timing window" in msg_forced


def test_digi_validation_tracker_persistence(tmp_path: Path):
    """Verify tracker records attempts, checks validation state, and tracks retries."""
    history_file = tmp_path / "digi_history.json"
    tracker = DigiValidationTracker(history_file)
    target = date(2026, 10, 20)

    # Initial state
    assert tracker.is_already_validated(target) is False
    assert len(tracker.get_attempts(target)) == 0
    assert tracker.next_attempt_number(target) == 1

    # Record first attempt: pending retry
    rec1 = DigiValidationRecord(
        report_date=target,
        attempt=1,
        timestamp="2026-10-20T11:00:00",
        status=DigiValidationStatus.PENDING_RETRY,
        error_message="Data stale: Oct Pivot as of 19 Oct 2026",
    )
    tracker.record_attempt(rec1)
    assert tracker.is_already_validated(target) is False
    assert len(tracker.get_attempts(target)) == 1
    assert tracker.next_attempt_number(target) == 2

    # Record second attempt: success
    rec2 = DigiValidationRecord(
        report_date=target,
        attempt=2,
        timestamp="2026-10-20T11:15:00",
        status=DigiValidationStatus.SUCCESS,
        gold_showroom_subtotal=4233,
        silver_showroom_subtotal=1488,
        gold_overall_total=5508,
        silver_overall_total=1755,
    )
    tracker.record_attempt(rec2)
    assert tracker.is_already_validated(target) is True
    assert len(tracker.get_attempts(target)) == 2

    # New tracker instance re-reads from disk
    tracker2 = DigiValidationTracker(history_file)
    assert tracker2.is_already_validated(target) is True
    attempts = tracker2.get_attempts(target)
    assert len(attempts) == 2
    assert attempts[-1].status == DigiValidationStatus.SUCCESS
    assert attempts[-1].gold_overall_total == 5508
    assert tracker2.is_blocked(target) is False

    # Blocked record test
    rec_blocked = DigiValidationRecord(
        report_date=date(2026, 10, 21),
        attempt=6,
        timestamp="2026-10-21T12:30:00",
        status=DigiValidationStatus.BLOCKED_CUTOFF,
        error_message="Cutoff exceeded",
    )
    tracker2.record_attempt(rec_blocked)
    assert tracker2.is_blocked(date(2026, 10, 21)) is True
