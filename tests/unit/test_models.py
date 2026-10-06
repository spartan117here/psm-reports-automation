"""Unit tests for domain models and date parsing."""

from datetime import date, timedelta
from app.cli import generate_run_id, parse_target_date
from app.core.models import CleanedRecord, RunMetadata, RunStatus


def test_parse_target_date_default_yesterday():
    """Verify that omitting report date defaults to yesterday."""
    target = parse_target_date(None)
    expected = date.today() - timedelta(days=1)
    assert target == expected


def test_parse_target_date_explicit():
    """Verify that providing an explicit ISO date is parsed accurately."""
    target = parse_target_date("2026-10-04")
    assert target == date(2026, 10, 4)


def test_generate_run_id_format():
    """Verify run ID format RUN-YYYYMMDD-HHMMSS."""
    today = date(2026, 10, 5)
    run_id = generate_run_id(today)
    assert run_id.startswith("RUN-20261005-")


def test_cleaned_record_serialization():
    """Verify CleanedRecord serializes and deserializes accurately."""
    record = CleanedRecord(
        COSTNAME="CHROMEPET",
        MSNO="MSNO-100",
        RECAMOUNT=5000.0,
        LOCATION="CHROMEPET",
        LOCATION_2="CHROMEPET",
    )
    dumped = record.model_dump()
    assert dumped["MSNO"] == "MSNO-100"
    assert dumped["RECAMOUNT"] == 5000.0

    restored = CleanedRecord(**dumped)
    assert restored == record


def test_run_status_dry_run():
    """Verify that RunStatus contains DRY_RUN enum value."""
    assert RunStatus.DRY_RUN.value == "DRY_RUN"
    assert RunStatus.DRY_RUN == "DRY_RUN"

