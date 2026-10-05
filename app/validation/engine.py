"""Validation execution engine and reporting orchestrator."""

import logging
from typing import List
from app.core.exceptions import CriticalValidationError
from app.core.models import ValidationReport, ValidationResult, ValidationSeverity

logger = logging.getLogger("pothys_reporting")


class ValidationEngine:
    """Orchestrates validation checks and enforces pipeline stop policies."""

    def __init__(self, halt_on_critical: bool = True):
        self.halt_on_critical = halt_on_critical

    def create_report(self, run_id: str, results: List[ValidationResult]) -> ValidationReport:
        """Compile a list of individual validation results into a comprehensive ValidationReport."""
        report = ValidationReport(run_id=run_id)
        for res in results:
            report.add_result(res)
            log_fn = logger.info
            if res.severity == ValidationSeverity.WARNING:
                log_fn = logger.warning
            elif res.severity == ValidationSeverity.CRITICAL:
                log_fn = logger.error
            log_fn(f"[VALIDATION] [{res.check_name}] {res.status.value}: {res.message}")

        if report.has_critical_failures and self.halt_on_critical:
            msg = (
                f"Validation failed with CRITICAL errors for run {run_id}. "
                "Publication and delivery must be blocked."
            )
            logger.critical(msg)
            raise CriticalValidationError(msg, details={"failed_checks": [
                c.check_name for c in report.checks if c.severity == ValidationSeverity.CRITICAL
            ]})

        return report
