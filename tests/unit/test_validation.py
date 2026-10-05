"""Unit tests for validation rules and audit engine."""

from datetime import date
import pytest

from app.core.exceptions import CriticalValidationError
from app.core.models import (
    CleanedRecord,
    RawReportPayload,
    ValidationSeverity,
    ValidationStatus,
)
from app.validation.engine import ValidationEngine
from app.validation.rules import ValidationRules


def test_validation_empty_response():
    """Verify empty response is flagged with CRITICAL severity."""
    empty_payload = RawReportPayload(
        report_id="subhiksham",
        report_date=date(2026, 10, 4),
        row_count=0,
        data=[],
    )
    result = ValidationRules.check_non_empty_response(empty_payload)
    assert result.status == ValidationStatus.FAILED
    assert result.severity == ValidationSeverity.CRITICAL


def test_validation_duplicate_msno():
    """Verify duplicate MSNOs are identified and flagged as WARNING."""
    records = [
        CleanedRecord(MSNO="DUPE-01", RECAMOUNT=100.0),
        CleanedRecord(MSNO="DUPE-01", RECAMOUNT=200.0),
        CleanedRecord(MSNO="UNIQUE-02", RECAMOUNT=300.0),
    ]
    result = ValidationRules.check_duplicate_msno(records)
    assert result.status == ValidationStatus.WARNING
    assert "DUPE-01" in result.message


def test_validation_negative_amounts():
    """Verify negative amounts trigger CRITICAL validation failure."""
    records = [
        CleanedRecord(MSNO="M1", RECAMOUNT=-500.0),
        CleanedRecord(MSNO="M2", RECAMOUNT=1000.0),
    ]
    result = ValidationRules.check_amount_sanity(records)
    assert result.status == ValidationStatus.FAILED
    assert result.severity == ValidationSeverity.CRITICAL


def test_validation_engine_critical_halt():
    """Verify ValidationEngine raises CriticalValidationError when critical failure occurs."""
    engine = ValidationEngine(halt_on_critical=True)
    failing_check = ValidationRules.check_targets_sanity({"CPT": -100.0})

    with pytest.raises(CriticalValidationError):
        engine.create_report("RUN-TEST-001", [failing_check])


def test_validation_engine_non_critical_continues():
    """Verify ValidationEngine continues when only warnings occur."""
    engine = ValidationEngine(halt_on_critical=True)
    records = [
        CleanedRecord(MSNO="DUPE-01", RECAMOUNT=100.0),
        CleanedRecord(MSNO="DUPE-01", RECAMOUNT=200.0),
    ]
    warning_check = ValidationRules.check_duplicate_msno(records)

    report = engine.create_report("RUN-TEST-002", [warning_check])
    assert report.has_critical_failures is False
    assert report.warning_count == 1
