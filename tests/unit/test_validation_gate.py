"""
Unit tests for the Fail-Closed Production Validation Gate.

Validates the 12 mandatory rules from Prompt Section 4 and safety requirements from Section 5:
1. Required source files and worksheets exist.
2. Report date and source date ranges are correct (cutoff isolation).
3. Expected branches/showrooms are present.
4. Duplicate and dropped-record counts are explained.
5. Unknown or unresolved branch mappings are reported.
6. Row counts and monetary/weight totals reconcile with source data.
7. Branch subtotals reconcile with grand totals (detects 9-showroom omissions like NGL / KANCHI).
8. Rejoining counts and percentages reconcile.
9. Closing weight and closing count reports reconcile.
10. All required report tables are populated.
11. Missing targets are clearly marked as unconfigured rather than treated as zero.
12. Historical output is reproducible for the same report date and source snapshot.

Safety & Upload Verification (Section 5):
- Proves the validation gate marks critical failures as 'FAILED — DO NOT SEND'.
- Proves enforce_safety_or_raise() raises CriticalValidationError to halt any future upload.
"""

from datetime import date
import pytest

from app.core.exceptions import CriticalValidationError
from app.core.models import ValidationSeverity, ValidationStatus
from app.validation.gate import (
    GateExecutionStatus,
    ProductionGateReport,
    ProductionValidationGate,
)


def test_gate_all_clean_produces_pass():
    """Verify that a clean report meeting all 12 checks produces status PASS and is safe for delivery."""
    gate = ProductionValidationGate(run_id="RUN-TEST-001", report_date=date(2026, 10, 25))

    # 1. Sources
    gate.check_required_sources({"SS - Oct": True, "SV - Oct": True, "October - 2026": True, "Consolidate Report - Oct": True})
    # 2. Cutoff isolation
    gate.check_date_cutoff_isolation([date(2026, 10, 20), date(2026, 10, 24), date(2026, 10, 25)])
    # 3. Expected showrooms
    expected = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KPM"]
    gate.check_expected_showrooms_present(expected, expected)
    # 4. Duplicate / dropped records
    gate.check_duplicate_and_dropped_records(duplicate_count=0, dropped_count=0)
    # 5. Mappings
    gate.check_unresolved_mappings(unresolved_branch_codes=[], unresolved_employee_codes=[])
    # 6. Source totals
    gate.check_source_reconciliation(source_count=100, processed_count=100, source_amount=500000.0, processed_amount=500000.0)
    # 7. Subtotals
    gate.check_branch_subtotals(branch_values=[100.0, 200.0, 300.0], grand_total=600.0, table_name="Revenue")
    # 8. Rejoining math
    gate.check_rejoining_math(closed_count=1000, rejoin_ss=200, rejoin_sv=50, total_rejoin=250, rejoin_pct=25.0)
    # 9. Cross-table closing
    gate.check_closing_cross_table_reconciliation(table1_closed_count=1000, table3_total_count=1000, table2_closed_weight=7500.0, source_closed_weight=7500.0)
    # 10. Required tables
    gate.check_required_tables_populated({"Revenue": True, "Counts": True, "Rejoining": True, "Weight": True, "SchemeBreakdown": True})
    # 11. Missing targets
    gate.check_unconfigured_targets({"TVL": 5000.0, "NGL": None, "KANCHI_COUNT": None}, known_unconfigured=["NGL", "KANCHI_COUNT"])
    # 12. Reproducibility
    gate.check_historical_reproducibility({"total_ss": 867}, {"total_ss": 867})

    report = gate.evaluate()
    assert report.status == GateExecutionStatus.PASS
    assert report.is_safe_for_delivery is True
    assert len(report.critical_failures) == 0
    assert len(report.warnings) == 0

    # Safety enforce call must succeed without exception
    approved = gate.enforce_safety_or_raise()
    assert approved.is_safe_for_delivery is True


def test_gate_critical_failure_marks_failed_do_not_send():
    """Verify that any critical check failure marks report FAILED — DO NOT SEND."""
    gate = ProductionValidationGate(run_id="RUN-TEST-002", report_date=date(2026, 10, 25))

    # Pass rule 1
    gate.check_required_sources({"SS - Oct": True})
    # Fail rule 2: Future date leak (record dated 26 Oct in a 25 Oct report)
    gate.check_date_cutoff_isolation([date(2026, 10, 25), date(2026, 10, 26)])

    report = gate.evaluate()
    assert report.status == GateExecutionStatus.FAILED_DO_NOT_SEND
    assert report.is_safe_for_delivery is False
    assert len(report.critical_failures) == 1
    assert "2.report_date_cutoff_isolation" in report.critical_failures[0]["check_name"]


def test_gate_enforce_blocks_delivery_when_validation_fails():
    """
    SAFETY REQUIREMENT (Section 5):
    Test that the validation gate strictly blocks downstream delivery/upload
    when validation fails by raising CriticalValidationError.
    """
    gate = ProductionValidationGate(run_id="RUN-TEST-003", report_date=date(2026, 10, 25))

    # Fail rule 1: Missing source sheet
    gate.check_required_sources({"SS - Oct": True, "October - 2026": False})

    with pytest.raises(CriticalValidationError) as exc_info:
        gate.enforce_safety_or_raise()

    assert "Production Validation Gate FAILED (FAILED — DO NOT SEND)" in str(exc_info.value)
    assert "Downstream upload and delivery operations are blocked" in str(exc_info.value)


def test_gate_detects_subtotal_formula_discrepancy():
    """
    AUDIT REQUIREMENT (Section 3 & 4):
    Detects formula discrepancies where branch rows do not reconcile with grand total
    (e.g. 9-showroom formula omitting NGL row 14 or KANCHI target row 14).
    """
    gate = ProductionValidationGate(run_id="RUN-TEST-004", report_date=date(2026, 10, 25))

    # True branch values sum to 340M, but sheet formula hardcodes =SUM(F6:F13) = 313M (omitting KANCHI 27M)
    branch_values_with_kanchi = [30.0, 40.0, 35.0, 45.0, 50.0, 30.0, 40.0, 43.0, 27.0]  # Sum = 340.0
    sheet_formula_grand_total = 313.0  # Discrepancy!

    res = gate.check_branch_subtotals(
        branch_values=branch_values_with_kanchi,
        grand_total=sheet_formula_grand_total,
        table_name="Consolidate Report Table 1 Target Revenue",
    )

    assert res.status == ValidationStatus.FAILED
    assert res.severity == ValidationSeverity.CRITICAL

    report = gate.evaluate()
    assert report.status == GateExecutionStatus.FAILED_DO_NOT_SEND
    assert report.is_safe_for_delivery is False


def test_gate_detects_missing_showrooms():
    """Verify that gate detects missing showroom rows."""
    gate = ProductionValidationGate(run_id="RUN-TEST-005", report_date=date(2026, 10, 25))

    expected = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KPM", "NGL"]
    present = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KPM"]  # Missing NGL

    res = gate.check_expected_showrooms_present(present_showrooms=present, expected_showrooms=expected)
    assert res.status == ValidationStatus.FAILED
    assert "NGL" in str(res.actual)

    report = gate.evaluate()
    assert report.status == GateExecutionStatus.FAILED_DO_NOT_SEND


def test_gate_unconfigured_targets_not_silently_zeroed():
    """
    AUDIT REQUIREMENT (Section 3 & 4):
    Missing targets must be clearly marked as unconfigured rather than treated as zero.
    """
    gate = ProductionValidationGate(run_id="RUN-TEST-006", report_date=date(2026, 10, 25))

    # Case A: Missing targets are explicitly None -> PASSED
    targets_clean = {"NGL_REVENUE": None, "NGL_COUNT": None, "KANCHI_COUNT": None}
    res_clean = gate.check_unconfigured_targets(targets_clean, known_unconfigured=["NGL_REVENUE", "NGL_COUNT", "KANCHI_COUNT"])
    assert res_clean.status == ValidationStatus.PASSED

    # Case B: Missing targets silently converted to 0.0 without unconfigured marker -> WARNING
    gate_bad = ProductionValidationGate(run_id="RUN-TEST-007", report_date=date(2026, 10, 25))
    targets_silent_zero = {"NGL_REVENUE": 0.0, "NGL_COUNT": 0.0, "KANCHI_COUNT": 0.0}
    res_bad = gate_bad.check_unconfigured_targets(targets_silent_zero, known_unconfigured=["NGL_REVENUE", "NGL_COUNT", "KANCHI_COUNT"])
    assert res_bad.status == ValidationStatus.WARNING
    assert "NGL_REVENUE" in str(res_bad.actual)


def test_gate_rejoining_math_reconciliation():
    """Verify that Rejoining count and percentage formulas are strictly audited."""
    gate = ProductionValidationGate(run_id="RUN-TEST-008", report_date=date(2026, 10, 25))

    # Inconsistent formula: Total != SS + SV
    res_bad = gate.check_rejoining_math(closed_count=1000, rejoin_ss=100, rejoin_sv=50, total_rejoin=200, rejoin_pct=20.0)
    assert res_bad.status == ValidationStatus.FAILED
    assert res_bad.severity == ValidationSeverity.CRITICAL


def test_gate_non_blocking_warnings_produce_pass_with_warnings():
    """Verify that non-critical issues (e.g. unmapped employee codes) yield PASS WITH WARNINGS without halting."""
    gate = ProductionValidationGate(run_id="RUN-TEST-009", report_date=date(2026, 10, 25))

    # All critical checks pass, but employee codes have unmapped entries
    gate.check_required_sources({"SS - Oct": True})
    gate.check_date_cutoff_isolation([date(2026, 10, 25)])
    gate.check_expected_showrooms_present(["TVL"], ["TVL"])
    gate.check_unresolved_mappings(unresolved_branch_codes=[], unresolved_employee_codes=["EMP999"])

    report = gate.evaluate()
    assert report.status == GateExecutionStatus.PASS_WITH_WARNINGS
    assert report.is_safe_for_delivery is True
    assert len(report.warnings) == 1
    assert len(report.critical_failures) == 0
