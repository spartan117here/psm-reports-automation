"""
Validation rules implementing the 18 data integrity and audit requirements.
"""

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from app.core.models import (
    CleanedRecord,
    RawReportPayload,
    ValidationResult,
    ValidationSeverity,
    ValidationStatus,
)


class ValidationRules:
    """Core rule implementations for data health and business reconciliation."""

    @staticmethod
    def check_non_empty_response(raw_payload: RawReportPayload) -> ValidationResult:
        """Check 3: Response is not empty."""
        has_data = raw_payload.row_count > 0
        return ValidationResult(
            check_name="non_empty_response",
            status=ValidationStatus.PASSED if has_data else ValidationStatus.FAILED,
            severity=ValidationSeverity.CRITICAL,
            expected="> 0 rows",
            actual=f"{raw_payload.row_count} rows",
            message="Raw report contains records" if has_data else "Raw report returned 0 records",
        )

    @staticmethod
    def check_required_headers(
        raw_headers: List[str], required_headers: List[str]
    ) -> ValidationResult:
        """Check 1: Required headers exist."""
        normalized_raw = {h.strip().upper() for h in raw_headers}
        missing = [h for h in required_headers if h.strip().upper() not in normalized_raw]

        is_valid = len(missing) == 0
        return ValidationResult(
            check_name="required_headers_present",
            status=ValidationStatus.PASSED if is_valid else ValidationStatus.FAILED,
            severity=ValidationSeverity.CRITICAL,
            expected=f"All {len(required_headers)} required headers",
            actual=f"Missing: {missing}" if missing else "All present",
            message="All required headers exist" if is_valid else f"Missing headers: {missing}",
        )

    @staticmethod
    def check_duplicate_msno(records: List[CleanedRecord]) -> ValidationResult:
        """Check 6: Duplicate MSNO detection."""
        msnos = [r.MSNO for r in records if r.MSNO]
        counts = Counter(msnos)
        duplicates = {k: v for k, v in counts.items() if v > 1}

        has_dupes = len(duplicates) > 0
        return ValidationResult(
            check_name="duplicate_msno_detection",
            status=ValidationStatus.WARNING if has_dupes else ValidationStatus.PASSED,
            severity=ValidationSeverity.WARNING,
            expected="0 duplicate MSNOs",
            actual=f"{len(duplicates)} duplicates found",
            message=f"Duplicate MSNOs detected: {list(duplicates.keys())[:10]}" if has_dupes else "No duplicate MSNOs",
        )

    @staticmethod
    def check_unresolved_employee_codes(unresolved_codes: List[str]) -> ValidationResult:
        """Check 7 & 8: Employee/branch mappings validated, unknown locations detected."""
        has_unresolved = len(unresolved_codes) > 0
        return ValidationResult(
            check_name="employee_branch_mappings",
            status=ValidationStatus.WARNING if has_unresolved else ValidationStatus.PASSED,
            severity=ValidationSeverity.WARNING,
            expected="0 unresolved employee codes",
            actual=f"{len(unresolved_codes)} unresolved: {unresolved_codes[:10]}",
            message="All employee codes mapped" if not has_unresolved else f"Unresolved codes: {unresolved_codes}",
        )

    @staticmethod
    def check_amount_sanity(records: List[CleanedRecord], max_allowed_row_amount: float = 5000000.0) -> ValidationResult:
        """Check 16: Outlier/sanity checks (negative or astronomical amounts)."""
        negative_rows = [r.MSNO for r in records if r.RECAMOUNT < 0]
        outliers = [r.MSNO for r in records if r.RECAMOUNT > max_allowed_row_amount]

        is_clean = len(negative_rows) == 0 and len(outliers) == 0
        severity = ValidationSeverity.CRITICAL if negative_rows else ValidationSeverity.WARNING

        return ValidationResult(
            check_name="amount_sanity_and_outliers",
            status=ValidationStatus.PASSED if is_clean else ValidationStatus.FAILED,
            severity=severity,
            expected="No negative or extreme outlier amounts",
            actual=f"Negatives: {len(negative_rows)}, Outliers: {len(outliers)}",
            message="Amounts are within sane bounds" if is_clean else f"Found {len(negative_rows)} negative and {len(outliers)} outlier rows",
        )

    @staticmethod
    def check_reconciliation(
        source_count: int,
        cleaned_count: int,
        source_amount: float,
        cleaned_amount: float,
    ) -> ValidationResult:
        """Check 5 & 10: Source total vs processed total reconciliation."""
        count_match = source_count == cleaned_count
        diff = round(abs(source_amount - cleaned_amount), 2)
        amt_match = diff < 0.01

        is_valid = count_match and amt_match
        return ValidationResult(
            check_name="source_vs_processed_reconciliation",
            status=ValidationStatus.PASSED if is_valid else ValidationStatus.FAILED,
            severity=ValidationSeverity.CRITICAL,
            expected=f"Count: {source_count}, Amount: {source_amount:,.2f}",
            actual=f"Count: {cleaned_count}, Amount: {cleaned_amount:,.2f}",
            message="Totals match perfectly" if is_valid else f"Reconciliation discrepancy: amount diff={diff}",
        )

    @staticmethod
    def check_targets_sanity(targets: Dict[str, float]) -> ValidationResult:
        """Check 15: No negative or blank target values."""
        invalid = {k: v for k, v in targets.items() if v is None or v <= 0}
        is_valid = len(invalid) == 0

        return ValidationResult(
            check_name="targets_configuration_validity",
            status=ValidationStatus.PASSED if is_valid else ValidationStatus.FAILED,
            severity=ValidationSeverity.CRITICAL,
            expected="All targets > 0",
            actual=f"Invalid: {invalid}" if invalid else "All targets valid",
            message="Branch targets configured correctly" if is_valid else f"Invalid targets found: {invalid}",
        )
