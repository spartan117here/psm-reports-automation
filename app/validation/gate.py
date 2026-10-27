"""
Fail-Closed Production Validation Gate for Pothys Daily & Executive Reporting.

Mandatory Audit Gate:
Before any report is considered ready or approved for delivery/upload,
it must pass through this gate. If any critical check fails, the report
is explicitly marked:
    'FAILED — DO NOT SEND'
and all downstream upload, delivery, and distribution actions are blocked.
"""

from datetime import date, datetime
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from app.core.exceptions import CriticalValidationError
from app.core.models import (
    ValidationReport,
    ValidationResult,
    ValidationSeverity,
    ValidationStatus,
)

logger = logging.getLogger("pothys_reporting")


class GateExecutionStatus:
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS WITH WARNINGS"
    PENDING_DO_NOT_SEND = "PENDING — DO NOT SEND"
    FAILED_DO_NOT_SEND = "FAILED — DO NOT SEND"


class ProductionGateReport(BaseModel):
    """Consolidated outcome from the production validation gate."""
    run_id: str
    report_date: date
    status: str  # PASS, PASS WITH WARNINGS, PENDING — DO NOT SEND, FAILED — DO NOT SEND
    is_safe_for_delivery: bool = False
    critical_failures: List[Dict[str, Any]] = Field(default_factory=list)
    pending_checks: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[Dict[str, Any]] = Field(default_factory=list)
    passed_checks: List[str] = Field(default_factory=list)
    all_results: List[ValidationResult] = Field(default_factory=list)

    def summary_markdown(self) -> str:
        """Render markdown summary of gate results."""
        lines = [
            f"### Production Validation Gate Status: **{self.status}**",
            f"- **Report Date**: {self.report_date.isoformat()}",
            f"- **Safe for Delivery/Upload**: {'YES' if self.is_safe_for_delivery else 'NO (BLOCKED)'}",
            f"- **Passed Checks**: {len(self.passed_checks)}",
            f"- **Pending Checks**: {len(self.pending_checks)}",
            f"- **Warnings**: {len(self.warnings)}",
            f"- **Critical Failures**: {len(self.critical_failures)}",
        ]
        if self.critical_failures:
            lines.append("\n#### Critical Failures (HALTING):")
            for f in self.critical_failures:
                lines.append(f"- **{f['check_name']}** ({f.get('affected_report', 'General')}): {f['message']}")
                lines.append(f"  - Expected: `{f['expected']}` | Actual: `{f['actual']}`")
        if self.pending_checks:
            lines.append("\n#### Pending Checks (Awaiting Source Upload / Delivery Blocked):")
            for p in self.pending_checks:
                lines.append(f"- **{p['check_name']}**: {p['message']}")
        if self.warnings:
            lines.append("\n#### Warnings (Non-Blocking):")
            for w in self.warnings:
                lines.append(f"- **{w['check_name']}**: {w['message']}")
        return "\n".join(lines)


class ProductionValidationGate:
    """
    12-Rule Fail-Closed Validation Gate implementing Section 4 requirements.
    """

    def __init__(self, run_id: str, report_date: date):
        self.run_id = run_id
        self.report_date = report_date
        self.results: List[ValidationResult] = []

    def add_result(self, res: ValidationResult) -> None:
        self.results.append(res)

    # -------------------------------------------------------------------------
    # 1. Required Source Files & Worksheets
    # -------------------------------------------------------------------------
    def check_required_sources(
        self, available_sources: Dict[str, bool], affected_report: str = "Source Integrity"
    ) -> ValidationResult:
        missing = [src for src, exists in available_sources.items() if not exists]
        passed = len(missing) == 0
        res = ValidationResult(
            check_name="1.required_source_files_and_worksheets",
            status=ValidationStatus.PASSED if passed else ValidationStatus.FAILED,
            severity=ValidationSeverity.CRITICAL,
            expected="All required sources exist and are accessible",
            actual=f"Missing: {missing}" if missing else "All present",
            message="Required source files and worksheets exist" if passed else f"Missing required sources: {missing}",
        )
        self.add_result(res)
        return res

    # -------------------------------------------------------------------------
    # 2. Date Ranges & Reporting Cutoff Isolation
    # -------------------------------------------------------------------------
    def check_date_cutoff_isolation(
        self,
        record_dates: List[date],
        affected_report: str = "Temporal Cutoff",
    ) -> ValidationResult:
        future_dates = [d for d in record_dates if d and d > self.report_date]
        passed = len(future_dates) == 0
        res = ValidationResult(
            check_name="2.report_date_cutoff_isolation",
            status=ValidationStatus.PASSED if passed else ValidationStatus.FAILED,
            severity=ValidationSeverity.CRITICAL,
            expected=f"All record dates <= report date cutoff ({self.report_date.isoformat()})",
            actual=f"Found {len(future_dates)} records with dates > {self.report_date.isoformat()}: {future_dates[:5]}",
            message="Strict date cutoff maintained with zero future contamination" if passed else f"Date leakage detected: {len(future_dates)} future records present",
        )
        self.add_result(res)
        return res

    # -------------------------------------------------------------------------
    # 3. Expected Branches/Showrooms Present
    # -------------------------------------------------------------------------
    def check_expected_showrooms_present(
        self,
        present_showrooms: List[str],
        expected_showrooms: List[str],
        affected_report: str = "Showroom Completeness",
    ) -> ValidationResult:
        normalized_present = {s.strip().upper() for s in present_showrooms if s}
        missing = [s for s in expected_showrooms if s.strip().upper() not in normalized_present]
        passed = len(missing) == 0
        res = ValidationResult(
            check_name="3.expected_showrooms_present",
            status=ValidationStatus.PASSED if passed else ValidationStatus.FAILED,
            severity=ValidationSeverity.CRITICAL,
            expected=f"All {len(expected_showrooms)} expected showrooms present",
            actual=f"Missing showrooms: {missing}" if missing else "All present",
            message="All expected showrooms present in report" if passed else f"Missing expected showrooms: {missing}",
        )
        self.add_result(res)
        return res

    # -------------------------------------------------------------------------
    # 4. Duplicate & Dropped Records Explained
    # -------------------------------------------------------------------------
    def check_duplicate_and_dropped_records(
        self,
        duplicate_count: int,
        dropped_count: int,
        explanation: Optional[str] = None,
        affected_report: str = "Record Health",
    ) -> ValidationResult:
        if dropped_count > 0 and not explanation:
            status = ValidationStatus.FAILED
            severity = ValidationSeverity.CRITICAL
            msg = f"{dropped_count} dropped records unexplained"
        elif duplicate_count > 0:
            status = ValidationStatus.WARNING
            severity = ValidationSeverity.WARNING
            msg = f"{duplicate_count} duplicate records detected (handled: {explanation or 'tracked'})"
        else:
            status = ValidationStatus.PASSED
            severity = ValidationSeverity.INFO
            msg = "Zero dropped records and clean record flow"

        res = ValidationResult(
            check_name="4.duplicate_and_dropped_records",
            status=status,
            severity=severity,
            expected="0 unexplained dropped records",
            actual=f"Duplicates: {duplicate_count}, Dropped: {dropped_count}",
            message=msg,
        )
        self.add_result(res)
        return res

    # -------------------------------------------------------------------------
    # 5. Unknown or Unresolved Branch/Employee Mappings
    # -------------------------------------------------------------------------
    def check_unresolved_mappings(
        self,
        unresolved_branch_codes: List[str],
        unresolved_employee_codes: List[str],
        affected_report: str = "Location Resolution",
    ) -> ValidationResult:
        has_branch_unresolved = len(unresolved_branch_codes) > 0
        has_emp_unresolved = len(unresolved_employee_codes) > 0

        if has_branch_unresolved:
            status = ValidationStatus.FAILED
            severity = ValidationSeverity.CRITICAL
            msg = f"Unknown showroom branch codes detected: {unresolved_branch_codes}"
        elif has_emp_unresolved:
            status = ValidationStatus.WARNING
            severity = ValidationSeverity.WARNING
            msg = f"Unmapped employee codes detected: {unresolved_employee_codes[:10]}"
        else:
            status = ValidationStatus.PASSED
            severity = ValidationSeverity.INFO
            msg = "All branch and employee mappings resolved cleanly"

        res = ValidationResult(
            check_name="5.unresolved_mappings",
            status=status,
            severity=severity,
            expected="0 unresolved showroom codes",
            actual=f"Branches: {len(unresolved_branch_codes)}, Employees: {len(unresolved_employee_codes)}",
            message=msg,
        )
        self.add_result(res)
        return res

    # -------------------------------------------------------------------------
    # 6. Source Row Count & Monetary/Weight Reconciliation
    # -------------------------------------------------------------------------
    def check_source_reconciliation(
        self,
        source_count: int,
        processed_count: int,
        source_amount: float,
        processed_amount: float,
        tolerance: float = 0.01,
        affected_report: str = "Source Reconciliation",
    ) -> ValidationResult:
        count_match = source_count == processed_count
        amt_diff = abs(source_amount - processed_amount)
        amt_match = amt_diff <= tolerance

        passed = count_match and amt_match
        res = ValidationResult(
            check_name="6.source_totals_reconciliation",
            status=ValidationStatus.PASSED if passed else ValidationStatus.FAILED,
            severity=ValidationSeverity.CRITICAL,
            expected=f"Count: {source_count}, Total: {source_amount:,.2f}",
            actual=f"Count: {processed_count}, Total: {processed_amount:,.2f} (diff={amt_diff:.4f})",
            message="Source counts and monetary/weight totals reconcile perfectly" if passed else f"Reconciliation discrepancy: count_match={count_match}, amt_diff={amt_diff:.2f}",
        )
        self.add_result(res)
        return res

    # -------------------------------------------------------------------------
    # 7. Branch Subtotals Reconcile with Grand Totals
    # -------------------------------------------------------------------------
    def check_branch_subtotals(
        self,
        branch_values: List[float],
        grand_total: float,
        tolerance: float = 0.01,
        table_name: str = "Report Table",
    ) -> ValidationResult:
        sum_branches = round(sum(branch_values), 2)
        expected_total = round(grand_total, 2)
        diff = round(abs(sum_branches - expected_total), 2)
        passed = diff <= tolerance

        res = ValidationResult(
            check_name=f"7.branch_subtotals_reconciliation_{table_name.lower().replace(' ', '_')}",
            status=ValidationStatus.PASSED if passed else ValidationStatus.FAILED,
            severity=ValidationSeverity.CRITICAL,
            expected=f"Sum of branch rows ({sum_branches:,.2f}) == Grand Total ({expected_total:,.2f})",
            actual=f"Sum: {sum_branches:,.2f}, Total: {expected_total:,.2f}, Diff: {diff:.2f}",
            message=f"{table_name} subtotals reconcile with grand total without omissions or double-counting" if passed else f"{table_name} subtotal mismatch: diff={diff:.2f}",
        )
        self.add_result(res)
        return res

    # -------------------------------------------------------------------------
    # 8. Rejoining Counts and Percentages Reconcile
    # -------------------------------------------------------------------------
    def check_rejoining_math(
        self,
        closed_count: int,
        rejoin_ss: int,
        rejoin_sv: int,
        total_rejoin: int,
        rejoin_pct: float,
        affected_report: str = "Scheme Rejoining Table 1",
    ) -> ValidationResult:
        expected_total = rejoin_ss + rejoin_sv
        math_total_match = total_rejoin == expected_total
        expected_pct = round((total_rejoin / closed_count * 100.0), 2) if closed_count > 0 else 0.0
        pct_diff = abs(rejoin_pct - expected_pct)
        math_pct_match = pct_diff <= 0.05

        passed = math_total_match and math_pct_match
        res = ValidationResult(
            check_name="8.rejoining_math_reconciliation",
            status=ValidationStatus.PASSED if passed else ValidationStatus.FAILED,
            severity=ValidationSeverity.CRITICAL,
            expected=f"Total={expected_total} (SS={rejoin_ss} + SV={rejoin_sv}), %={expected_pct:.2f}%",
            actual=f"Total={total_rejoin}, %={rejoin_pct:.2f}%",
            message="Rejoining math reconciles cleanly" if passed else f"Rejoining math mismatch: total_match={math_total_match}, pct_diff={pct_diff:.2f}",
        )
        self.add_result(res)
        return res

    # -------------------------------------------------------------------------
    # 9. Closing Weight and Closing Count Reports Reconcile
    # -------------------------------------------------------------------------
    def check_closing_cross_table_reconciliation(
        self,
        table1_closed_count: int,
        table3_total_count: int,
        table2_closed_weight: float,
        source_closed_weight: float,
        affected_report: str = "Closing Tables Cross-Reconciliation",
    ) -> ValidationResult:
        count_match = table1_closed_count == table3_total_count
        weight_diff = abs(table2_closed_weight - source_closed_weight)
        weight_match = weight_diff <= 0.01

        passed = count_match and weight_match
        res = ValidationResult(
            check_name="9.closing_cross_table_reconciliation",
            status=ValidationStatus.PASSED if passed else ValidationStatus.FAILED,
            severity=ValidationSeverity.CRITICAL,
            expected=f"Counts: T1={table1_closed_count} == T3={table1_closed_count}, Weight={source_closed_weight:,.3f}g",
            actual=f"Counts: T1={table1_closed_count}, T3={table3_total_count}; Weight: T2={table2_closed_weight:,.3f}g, Source={source_closed_weight:,.3f}g",
            message="Closing counts and weights cross-reconcile between Table 1, Table 2, and Table 3" if passed else f"Cross-table discrepancy: count_match={count_match}, weight_diff={weight_diff:.3f}g",
        )
        self.add_result(res)
        return res

    # -------------------------------------------------------------------------
    # 10. All Required Report Tables Populated
    # -------------------------------------------------------------------------
    def check_required_tables_populated(
        self,
        tables_populated: Dict[str, bool],
        affected_report: str = "Report Completeness",
    ) -> ValidationResult:
        empty_tables = [tbl for tbl, populated in tables_populated.items() if not populated]
        passed = len(empty_tables) == 0

        res = ValidationResult(
            check_name="10.required_report_tables_populated",
            status=ValidationStatus.PASSED if passed else ValidationStatus.FAILED,
            severity=ValidationSeverity.CRITICAL,
            expected="All required tables populated with valid rows",
            actual=f"Empty tables: {empty_tables}" if empty_tables else "All tables populated",
            message="All required report tables are populated" if passed else f"Unpopulated tables: {empty_tables}",
        )
        self.add_result(res)
        return res

    # -------------------------------------------------------------------------
    # 11. Missing Targets Explicitly Marked as Unconfigured
    # -------------------------------------------------------------------------
    def check_unconfigured_targets(
        self,
        targets_dict: Dict[str, Any],
        known_unconfigured: List[str],
        affected_report: str = "Target Configuration",
    ) -> ValidationResult:
        silently_zeroed = []
        for k in known_unconfigured:
            val = targets_dict.get(k)
            # If a missing target was set to 0 without being marked as unconfigured
            if val == 0 or val == 0.0:
                silently_zeroed.append(k)

        has_silent_zeros = len(silently_zeroed) > 0
        if has_silent_zeros:
            status = ValidationStatus.WARNING
            severity = ValidationSeverity.WARNING
            msg = f"Unconfigured targets treated as 0: {silently_zeroed}. Must be marked UNCONFIGURED."
        else:
            status = ValidationStatus.PASSED
            severity = ValidationSeverity.INFO
            msg = "Missing targets are explicitly tagged as unconfigured rather than silently assumed zero"

        res = ValidationResult(
            check_name="11.missing_targets_handling",
            status=status,
            severity=severity,
            expected="Unconfigured targets flagged as None/UNCONFIGURED",
            actual=f"Silently zeroed: {silently_zeroed}" if has_silent_zeros else "Correctly tagged",
            message=msg,
        )
        self.add_result(res)
        return res

    # -------------------------------------------------------------------------
    # 12. Historical Output Reproducibility
    # -------------------------------------------------------------------------
    def check_historical_reproducibility(
        self,
        baseline_metrics: Dict[str, Any],
        current_metrics: Dict[str, Any],
        affected_report: str = "Historical Regression",
    ) -> ValidationResult:
        mismatches = {}
        for k, v in baseline_metrics.items():
            curr_v = current_metrics.get(k)
            if curr_v != v:
                mismatches[k] = {"expected": v, "actual": curr_v}

        passed = len(mismatches) == 0
        res = ValidationResult(
            check_name="12.historical_reproducibility",
            status=ValidationStatus.PASSED if passed else ValidationStatus.FAILED,
            severity=ValidationSeverity.CRITICAL,
            expected="Output identical to verified historical baseline",
            actual=f"Mismatches: {mismatches}" if mismatches else "Exact match",
            message="Historical output is 100% reproducible for the same report date" if passed else f"Historical reproducibility failed: {mismatches}",
        )
        self.add_result(res)
        return res

    # -------------------------------------------------------------------------
    # 13. DigiGold & DigiSilver Readiness & 11:30 AM Deadline Rule
    # -------------------------------------------------------------------------
    def check_digi_readiness_and_deadline(
        self,
        gold_feed: Any,
        silver_feed: Any,
        current_time_ist: Optional[datetime] = None,
        deadline_str: str = "11:30",
        affected_report: str = "DigiGold/DigiSilver Readiness",
    ) -> ValidationResult:
        """
        Evaluate DigiGold and DigiSilver readiness and enforce the 11:30 AM local India time deadline.

        Guarantees:
        - Both required team-uploaded feeds must be available, complete, and reconciled.
        - At 11:30 AM, actual readiness is checked; never assumes arrival just because 11:30 is reached.
        - If either feed is missing, stale, incomplete, or inconsistent:
            * Before 11:30 AM: status is PENDING — DO NOT SEND (delivery blocked).
            * At/after 11:30 AM: status is FAILED — DO NOT SEND (delivery blocked).
        - Never silently reuses previous-day data, substitutes zeroes, or infers completion from file existence alone.
        """
        from app.reporting.digi import evaluate_digi_readiness_at_deadline
        can_proceed, gate_stat, msg = evaluate_digi_readiness_at_deadline(
            report_date=self.report_date,
            gold_feed=gold_feed,
            silver_feed=silver_feed,
            current_time_ist=current_time_ist,
            deadline_str=deadline_str,
        )

        if can_proceed:
            val_status = ValidationStatus.PASSED
            val_severity = ValidationSeverity.CRITICAL
        elif gate_stat == GateExecutionStatus.PENDING_DO_NOT_SEND:
            val_status = ValidationStatus.PENDING
            val_severity = ValidationSeverity.CRITICAL
        else:
            val_status = ValidationStatus.FAILED
            val_severity = ValidationSeverity.CRITICAL

        res = ValidationResult(
            check_name="13.digigold_digisilver_readiness_and_deadline",
            status=val_status,
            severity=val_severity,
            expected=f"Both DG and DS feeds verified ready by {deadline_str} AM deadline",
            actual=gate_stat,
            message=msg,
        )
        self.add_result(res)
        return res

    # -------------------------------------------------------------------------
    # Gate Evaluation & Enforcement
    # -------------------------------------------------------------------------
    def evaluate(self) -> ProductionGateReport:
        """
        Evaluate all accumulated checks and determine final gate decision.
        Fail-closed policy:
        - ANY critical failure marks report FAILED — DO NOT SEND (delivery blocked).
        - ANY pending check (without critical failure) marks report PENDING — DO NOT SEND (delivery blocked).
        - Non-critical warnings mark report PASS WITH WARNINGS.
        - All checks passed marks report PASS.
        """
        critical_failures = []
        pending_checks = []
        warnings = []
        passed_checks = []

        for r in self.results:
            if r.status == ValidationStatus.FAILED and r.severity == ValidationSeverity.CRITICAL:
                critical_failures.append({
                    "check_name": r.check_name,
                    "expected": str(r.expected),
                    "actual": str(r.actual),
                    "message": r.message,
                })
            elif r.status == ValidationStatus.PENDING:
                pending_checks.append({
                    "check_name": r.check_name,
                    "message": r.message,
                })
            elif r.status == ValidationStatus.WARNING:
                warnings.append({
                    "check_name": r.check_name,
                    "message": r.message,
                })
            elif r.status == ValidationStatus.PASSED:
                passed_checks.append(r.check_name)

        if critical_failures:
            status = GateExecutionStatus.FAILED_DO_NOT_SEND
            is_safe = False
        elif pending_checks:
            status = GateExecutionStatus.PENDING_DO_NOT_SEND
            is_safe = False
        elif warnings:
            status = GateExecutionStatus.PASS_WITH_WARNINGS
            is_safe = True
        else:
            status = GateExecutionStatus.PASS
            is_safe = True

        gate_report = ProductionGateReport(
            run_id=self.run_id,
            report_date=self.report_date,
            status=status,
            is_safe_for_delivery=is_safe,
            critical_failures=critical_failures,
            pending_checks=pending_checks,
            warnings=warnings,
            passed_checks=passed_checks,
            all_results=self.results,
        )

        if not is_safe:
            logger.critical(
                f"[VALIDATION GATE HALT] Report for {self.report_date} failed or pending validation. "
                f"Failures: {len(critical_failures)}, Pending: {len(pending_checks)}. Status: {status}."
            )
        else:
            logger.info(
                f"[VALIDATION GATE APPROVED] Report for {self.report_date} approved. "
                f"Status: {status} ({len(passed_checks)} passed, {len(warnings)} warnings)."
            )

        return gate_report

    def enforce_safety_or_raise(self) -> ProductionGateReport:
        """
        Enforce the fail-closed gate. If report failed, raises CriticalValidationError
        to block any downstream upload or delivery function.
        """
        report = self.evaluate()
        if not report.is_safe_for_delivery:
            raise CriticalValidationError(
                f"Production Validation Gate FAILED ({report.status}). "
                f"Downstream upload and delivery operations are blocked.",
                details={"critical_failures": report.critical_failures},
            )
        return report
