"""
Unit tests for DigiGold and DigiSilver Readiness & 11:30 AM Deadline Rule.

Requirements verified:
1. DG and DS both ready before 11:30: validation may proceed.
2. DG missing at 11:30: final delivery blocked.
3. DS missing at 11:30: final delivery blocked.
4. Files exist but data is incomplete: final delivery blocked.
5. Stale previous-day data: final delivery blocked.
6. Both feeds ready but another critical validation fails: final delivery blocked.
7. Both feeds ready and all critical checks pass: report may be approved for delivery.
8. Status PENDING — DO NOT SEND when feeds not ready before 11:30 AM deadline.
9. Prevention of silent zero substitution and file existence false positives.
"""

from datetime import date, datetime
import pytest

from app.core.exceptions import CriticalValidationError
from app.core.models import ValidationSeverity, ValidationStatus
from app.reporting.digi import (
    DIGI_BRANCH_ORDER,
    SHOWROOM_BRANCHES_9,
    DigiFeedStatus,
    evaluate_digi_feed_readiness,
    evaluate_digi_readiness_at_deadline,
)
from app.validation.gate import (
    GateExecutionStatus,
    ProductionGateReport,
    ProductionValidationGate,
)


# Helper sample data for 26 Oct 2026
SAMPLE_VALID_GOLD = {
    "NELLAI": 143,
    "CHROMEPET": 250,
    "TRIVANDRUM": 180,
    "TRICHY": 210,
    "COIMBATORE": 310,
    "SALEM": 95,
    "POONAMALLEE": 85,
    "PADI": 40,
    "KANCHEEPURAM": 25,
    "ECOMMERCE": 1500,
}

SAMPLE_VALID_SILVER = {
    "NELLAI": 3,
    "CHROMEPET": 15,
    "TRIVANDRUM": 8,
    "TRICHY": 12,
    "COIMBATORE": 20,
    "SALEM": 5,
    "POONAMALLEE": 4,
    "PADI": 2,
    "KANCHEEPURAM": 1,
    "ECOMMERCE": 120,
}


def create_clean_feed(scheme: str, rep_date: date, counts: dict) -> DigiFeedStatus:
    """Helper to generate a clean, validated DigiFeedStatus."""
    sr_sum = sum(counts[b] for b in SHOWROOM_BRANCHES_9)
    gt_sum = sr_sum + counts["ECOMMERCE"]
    return evaluate_digi_feed_readiness(
        report_date=rep_date,
        scheme=scheme,
        raw_date=rep_date,
        pivot_date=rep_date,
        branch_values=counts,
        branch_subtotal=sr_sum,
        grand_total=gt_sum,
    )


# -----------------------------------------------------------------------------
# TEST 1: DG and DS both ready before 11:30 AM -> validation may proceed
# -----------------------------------------------------------------------------
def test_dg_ds_both_ready_before_1130_validation_may_proceed():
    """When both DG and DS are valid at 11:15 AM (before 11:30), gate allows validation to proceed."""
    rep_date = date(2026, 10, 26)
    time_1115 = datetime(2026, 10, 26, 11, 15, 0)

    gold_feed = create_clean_feed("DigiGold", rep_date, SAMPLE_VALID_GOLD)
    silver_feed = create_clean_feed("DigiSilver", rep_date, SAMPLE_VALID_SILVER)

    assert gold_feed.is_valid is True
    assert silver_feed.is_valid is True

    can_proceed, status, msg = evaluate_digi_readiness_at_deadline(
        report_date=rep_date,
        gold_feed=gold_feed,
        silver_feed=silver_feed,
        current_time_ist=time_1115,
        deadline_str="11:30",
    )
    assert can_proceed is True
    assert status == "PASS"

    gate = ProductionValidationGate(run_id="RUN-TEST-DG-1", report_date=rep_date)
    res = gate.check_digi_readiness_and_deadline(
        gold_feed=gold_feed,
        silver_feed=silver_feed,
        current_time_ist=time_1115,
    )
    assert res.status == ValidationStatus.PASSED

    report = gate.evaluate()
    assert report.status == GateExecutionStatus.PASS
    assert report.is_safe_for_delivery is True


# -----------------------------------------------------------------------------
# TEST 2: DG missing at 11:30 AM -> final delivery blocked
# -----------------------------------------------------------------------------
def test_dg_missing_at_1130_delivery_blocked():
    """At 11:30 AM deadline, missing DigiGold blocks final delivery (FAILED — DO NOT SEND)."""
    rep_date = date(2026, 10, 26)
    time_1130 = datetime(2026, 10, 26, 11, 30, 0)

    # DG is missing (branch_values is None)
    gold_feed = evaluate_digi_feed_readiness(
        report_date=rep_date,
        scheme="DigiGold",
        branch_values=None,
    )
    silver_feed = create_clean_feed("DigiSilver", rep_date, SAMPLE_VALID_SILVER)

    assert gold_feed.is_valid is False
    assert silver_feed.is_valid is True

    can_proceed, status, msg = evaluate_digi_readiness_at_deadline(
        report_date=rep_date,
        gold_feed=gold_feed,
        silver_feed=silver_feed,
        current_time_ist=time_1130,
        deadline_str="11:30",
    )
    assert can_proceed is False
    assert status == GateExecutionStatus.FAILED_DO_NOT_SEND
    assert "DigiGold" in msg

    gate = ProductionValidationGate(run_id="RUN-TEST-DG-2", report_date=rep_date)
    gate.check_digi_readiness_and_deadline(
        gold_feed=gold_feed,
        silver_feed=silver_feed,
        current_time_ist=time_1130,
    )

    report = gate.evaluate()
    assert report.status == GateExecutionStatus.FAILED_DO_NOT_SEND
    assert report.is_safe_for_delivery is False
    assert len(report.critical_failures) == 1

    with pytest.raises(CriticalValidationError):
        gate.enforce_safety_or_raise()


# -----------------------------------------------------------------------------
# TEST 3: DS missing at 11:30 AM -> final delivery blocked
# -----------------------------------------------------------------------------
def test_ds_missing_at_1130_delivery_blocked():
    """At 11:30 AM deadline, missing DigiSilver blocks final delivery (FAILED — DO NOT SEND)."""
    rep_date = date(2026, 10, 26)
    time_1130 = datetime(2026, 10, 26, 11, 30, 0)

    gold_feed = create_clean_feed("DigiGold", rep_date, SAMPLE_VALID_GOLD)
    # DS is missing
    silver_feed = evaluate_digi_feed_readiness(
        report_date=rep_date,
        scheme="DigiSilver",
        branch_values=None,
    )

    assert gold_feed.is_valid is True
    assert silver_feed.is_valid is False

    can_proceed, status, msg = evaluate_digi_readiness_at_deadline(
        report_date=rep_date,
        gold_feed=gold_feed,
        silver_feed=silver_feed,
        current_time_ist=time_1130,
        deadline_str="11:30",
    )
    assert can_proceed is False
    assert status == GateExecutionStatus.FAILED_DO_NOT_SEND
    assert "DigiSilver" in msg

    gate = ProductionValidationGate(run_id="RUN-TEST-DG-3", report_date=rep_date)
    gate.check_digi_readiness_and_deadline(
        gold_feed=gold_feed,
        silver_feed=silver_feed,
        current_time_ist=time_1130,
    )

    report = gate.evaluate()
    assert report.status == GateExecutionStatus.FAILED_DO_NOT_SEND
    assert report.is_safe_for_delivery is False

    with pytest.raises(CriticalValidationError):
        gate.enforce_safety_or_raise()


# -----------------------------------------------------------------------------
# TEST 4: Files exist but data is incomplete -> final delivery blocked
# -----------------------------------------------------------------------------
def test_files_exist_but_data_incomplete_delivery_blocked():
    """Files exist, but a branch count is null/empty or missing -> delivery blocked."""
    rep_date = date(2026, 10, 26)
    time_1130 = datetime(2026, 10, 26, 11, 30, 0)

    # Incomplete gold counts: missing KANCHEEPURAM and null PADI
    incomplete_gold = dict(SAMPLE_VALID_GOLD)
    del incomplete_gold["KANCHEEPURAM"]
    incomplete_gold["PADI"] = None

    gold_feed = evaluate_digi_feed_readiness(
        report_date=rep_date,
        scheme="DigiGold",
        raw_date=rep_date,
        pivot_date=rep_date,
        branch_values=incomplete_gold,
        branch_subtotal=1000,
        grand_total=2500,
    )
    silver_feed = create_clean_feed("DigiSilver", rep_date, SAMPLE_VALID_SILVER)

    assert gold_feed.is_valid is False
    assert "KANCHEEPURAM" in str(gold_feed.missing_branches)

    gate = ProductionValidationGate(run_id="RUN-TEST-DG-4", report_date=rep_date)
    gate.check_digi_readiness_and_deadline(
        gold_feed=gold_feed,
        silver_feed=silver_feed,
        current_time_ist=time_1130,
    )

    report = gate.evaluate()
    assert report.status == GateExecutionStatus.FAILED_DO_NOT_SEND
    assert report.is_safe_for_delivery is False


# -----------------------------------------------------------------------------
# TEST 5: Stale previous-day data -> final delivery blocked
# -----------------------------------------------------------------------------
def test_stale_previous_day_data_delivery_blocked():
    """If source data is dated 25 Oct for 26 Oct report, it must be rejected as stale."""
    rep_date = date(2026, 10, 26)
    stale_date = date(2026, 10, 25)
    time_1130 = datetime(2026, 10, 26, 11, 30, 0)

    gold_feed = evaluate_digi_feed_readiness(
        report_date=rep_date,
        scheme="DigiGold",
        raw_date=stale_date,
        pivot_date=stale_date,
        branch_values=SAMPLE_VALID_GOLD,
    )

    assert gold_feed.is_stale is True
    assert gold_feed.is_valid is False
    assert "Stale previous-day data" in gold_feed.error_message

    silver_feed = create_clean_feed("DigiSilver", rep_date, SAMPLE_VALID_SILVER)

    gate = ProductionValidationGate(run_id="RUN-TEST-DG-5", report_date=rep_date)
    gate.check_digi_readiness_and_deadline(
        gold_feed=gold_feed,
        silver_feed=silver_feed,
        current_time_ist=time_1130,
    )

    report = gate.evaluate()
    assert report.status == GateExecutionStatus.FAILED_DO_NOT_SEND
    assert report.is_safe_for_delivery is False


# -----------------------------------------------------------------------------
# TEST 6: Both feeds ready but another critical validation fails -> delivery blocked
# -----------------------------------------------------------------------------
def test_both_feeds_ready_but_another_critical_validation_fails_delivery_blocked():
    """Even if DG & DS are 100% valid, if another critical check fails, delivery is halted."""
    rep_date = date(2026, 10, 26)
    time_1130 = datetime(2026, 10, 26, 11, 30, 0)

    gold_feed = create_clean_feed("DigiGold", rep_date, SAMPLE_VALID_GOLD)
    silver_feed = create_clean_feed("DigiSilver", rep_date, SAMPLE_VALID_SILVER)

    gate = ProductionValidationGate(run_id="RUN-TEST-DG-6", report_date=rep_date)
    # Check 13 passes
    gate.check_digi_readiness_and_deadline(
        gold_feed=gold_feed,
        silver_feed=silver_feed,
        current_time_ist=time_1130,
    )
    # But check 2 fails: Date cutoff leakage (record dated 27 Oct in a 26 Oct report)
    gate.check_date_cutoff_isolation([date(2026, 10, 26), date(2026, 10, 27)])

    report = gate.evaluate()
    assert report.status == GateExecutionStatus.FAILED_DO_NOT_SEND
    assert report.is_safe_for_delivery is False
    assert any("2.report_date_cutoff_isolation" in f["check_name"] for f in report.critical_failures)

    with pytest.raises(CriticalValidationError):
        gate.enforce_safety_or_raise()


# -----------------------------------------------------------------------------
# TEST 7: Both feeds ready and all critical checks pass -> report approved
# -----------------------------------------------------------------------------
def test_both_feeds_ready_and_all_critical_checks_pass_approved():
    """When both feeds are valid and all other gate checks pass, the report is approved for delivery."""
    rep_date = date(2026, 10, 26)
    time_1130 = datetime(2026, 10, 26, 11, 30, 0)

    gold_feed = create_clean_feed("DigiGold", rep_date, SAMPLE_VALID_GOLD)
    silver_feed = create_clean_feed("DigiSilver", rep_date, SAMPLE_VALID_SILVER)

    gate = ProductionValidationGate(run_id="RUN-TEST-DG-7", report_date=rep_date)

    # 1. Sources
    gate.check_required_sources({"New Enrollment": True, "Digi": True, "Closing": True})
    # 2. Cutoff isolation
    gate.check_date_cutoff_isolation([date(2026, 10, 26)])
    # 3. Showrooms
    expected_sh = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KPM"]
    gate.check_expected_showrooms_present(expected_sh, expected_sh)
    # 4. Records
    gate.check_duplicate_and_dropped_records(0, 0)
    # 5. Mappings
    gate.check_unresolved_mappings([], [])
    # 6. Source totals
    gate.check_source_reconciliation(100, 100, 1000.0, 1000.0)
    # 7. Subtotals
    gate.check_branch_subtotals([500.0, 500.0], 1000.0)
    # 8. Rejoining
    gate.check_rejoining_math(100, 20, 5, 25, 25.0)
    # 9. Cross table
    gate.check_closing_cross_table_reconciliation(100, 100, 500.0, 500.0)
    # 10. Tables populated
    gate.check_required_tables_populated({"T1": True, "T2": True})
    # 11. Targets
    gate.check_unconfigured_targets({"NGL": None}, known_unconfigured=["NGL"])
    # 12. Reproducibility
    gate.check_historical_reproducibility({"cnt": 10}, {"cnt": 10})
    # 13. DG & DS Readiness
    gate.check_digi_readiness_and_deadline(
        gold_feed=gold_feed,
        silver_feed=silver_feed,
        current_time_ist=time_1130,
    )

    report = gate.evaluate()
    assert report.status == GateExecutionStatus.PASS
    assert report.is_safe_for_delivery is True
    assert len(report.critical_failures) == 0

    approved = gate.enforce_safety_or_raise()
    assert approved.is_safe_for_delivery is True


# -----------------------------------------------------------------------------
# TEST 8: Feeds missing before 11:30 AM -> PENDING — DO NOT SEND
# -----------------------------------------------------------------------------
def test_feeds_missing_before_1130_marks_pending_do_not_send():
    """Before the 11:30 AM deadline (e.g. 10:45 AM), missing feeds produce PENDING — DO NOT SEND."""
    rep_date = date(2026, 10, 26)
    time_1045 = datetime(2026, 10, 26, 10, 45, 0)

    gold_feed = evaluate_digi_feed_readiness(
        report_date=rep_date,
        scheme="DigiGold",
        branch_values=None,
    )
    silver_feed = evaluate_digi_feed_readiness(
        report_date=rep_date,
        scheme="DigiSilver",
        branch_values=None,
    )

    can_proceed, status, msg = evaluate_digi_readiness_at_deadline(
        report_date=rep_date,
        gold_feed=gold_feed,
        silver_feed=silver_feed,
        current_time_ist=time_1045,
        deadline_str="11:30",
    )
    assert can_proceed is False
    assert status == GateExecutionStatus.PENDING_DO_NOT_SEND

    gate = ProductionValidationGate(run_id="RUN-TEST-DG-8", report_date=rep_date)
    gate.check_digi_readiness_and_deadline(
        gold_feed=gold_feed,
        silver_feed=silver_feed,
        current_time_ist=time_1045,
    )

    report = gate.evaluate()
    assert report.status == GateExecutionStatus.PENDING_DO_NOT_SEND
    assert report.is_safe_for_delivery is False
    assert len(report.pending_checks) == 1
    assert len(report.critical_failures) == 0


# -----------------------------------------------------------------------------
# TEST 9: Reconciliation mismatch in showroom or grand total blocks delivery
# -----------------------------------------------------------------------------
def test_subtotal_reconciliation_mismatch_blocks_delivery():
    """If showroom subtotal != branch total in pivot, feed is marked invalid and delivery blocked."""
    rep_date = date(2026, 10, 26)
    time_1130 = datetime(2026, 10, 26, 11, 30, 0)

    sr_actual = sum(SAMPLE_VALID_GOLD[b] for b in SHOWROOM_BRANCHES_9)  # 1258

    gold_feed = evaluate_digi_feed_readiness(
        report_date=rep_date,
        scheme="DigiGold",
        raw_date=rep_date,
        pivot_date=rep_date,
        branch_values=SAMPLE_VALID_GOLD,
        branch_subtotal=sr_actual + 50,  # Intentional discrepancy
        grand_total=sr_actual + 1500 + 50,
    )
    silver_feed = create_clean_feed("DigiSilver", rep_date, SAMPLE_VALID_SILVER)

    assert gold_feed.is_valid is False
    assert gold_feed.subtotal_reconciled is False
    assert "showroom subtotal mismatch" in gold_feed.error_message

    gate = ProductionValidationGate(run_id="RUN-TEST-DG-9", report_date=rep_date)
    gate.check_digi_readiness_and_deadline(
        gold_feed=gold_feed,
        silver_feed=silver_feed,
        current_time_ist=time_1130,
    )

    report = gate.evaluate()
    assert report.status == GateExecutionStatus.FAILED_DO_NOT_SEND
    assert report.is_safe_for_delivery is False
