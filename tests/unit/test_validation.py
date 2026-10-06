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


def test_validation_engine_passed_logs_info(caplog):
    """Verify that a PASSED validation check is logged at INFO level even with CRITICAL severity."""
    import logging
    caplog.set_level(logging.DEBUG, logger="pothys_reporting")
    engine = ValidationEngine(halt_on_critical=True)

    # check_targets_sanity has severity=CRITICAL, but here passes
    passed_check = ValidationRules.check_targets_sanity({"CPT": 100000.0})
    assert passed_check.status == ValidationStatus.PASSED
    assert passed_check.severity == ValidationSeverity.CRITICAL

    engine.create_report("RUN-LOG-001", [passed_check])

    validation_records = [
        r for r in caplog.records if r.name == "pothys_reporting" and "[VALIDATION]" in r.message
    ]
    assert len(validation_records) == 1
    assert validation_records[0].levelno == logging.INFO
    assert "PASSED: Branch targets configured correctly" in validation_records[0].message
    # Critical severity must NOT cause ERROR level on PASSED checks
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_validation_engine_warning_logs_warning(caplog):
    """Verify that a WARNING status validation check is logged at WARNING level."""
    import logging
    caplog.set_level(logging.DEBUG, logger="pothys_reporting")
    engine = ValidationEngine(halt_on_critical=True)

    records = [
        CleanedRecord(MSNO="DUPE-01", RECAMOUNT=100.0),
        CleanedRecord(MSNO="DUPE-01", RECAMOUNT=200.0),
    ]
    warning_check = ValidationRules.check_duplicate_msno(records)
    assert warning_check.status == ValidationStatus.WARNING

    engine.create_report("RUN-LOG-002", [warning_check])

    validation_records = [
        r for r in caplog.records if r.name == "pothys_reporting" and "[VALIDATION]" in r.message
    ]
    assert len(validation_records) == 1
    assert validation_records[0].levelno == logging.WARNING


def test_validation_engine_failed_logs_error(caplog):
    """Verify that a FAILED status validation check is logged at ERROR level."""
    import logging
    caplog.set_level(logging.DEBUG, logger="pothys_reporting")
    engine = ValidationEngine(halt_on_critical=False)

    records = [CleanedRecord(MSNO="M1", RECAMOUNT=-500.0)]
    failed_check = ValidationRules.check_amount_sanity(records)
    assert failed_check.status == ValidationStatus.FAILED

    engine.create_report("RUN-LOG-003", [failed_check])

    validation_records = [
        r for r in caplog.records if r.name == "pothys_reporting" and "[VALIDATION]" in r.message
    ]
    assert len(validation_records) == 1
    assert validation_records[0].levelno == logging.ERROR

