"""Unit tests for Phase-based CLI execution (--phase 1, 2, 3, 2-3, all) and standalone commands."""

import argparse
from datetime import date
from pathlib import Path
from unittest.mock import patch
import pytest

from app.cli import (
    build_parser,
    cmd_daily_pipeline,
    cmd_validate_digi,
    cmd_consolidate_final,
)
from app.reporting.digi import DigiValidationRecord, DigiValidationStatus, DigiValidationTracker


def test_cli_parser_phase_arguments():
    """Verify parser accepts --phase argument with valid choices."""
    parser = build_parser()
    for ph in ["1", "2", "3", "2-3", "all"]:
        args = parser.parse_args(["run-daily", "--phase", ph])
        assert args.phase == ph

    # Invalid choice raises SystemExit
    with pytest.raises(SystemExit):
        parser.parse_args(["run-daily", "--phase", "4"])


def test_cli_parser_standalone_digi_commands():
    """Verify standalone validate-digi and consolidate-final commands are registered."""
    parser = build_parser()
    vargs = parser.parse_args(["validate-digi", "--report-date", "2026-10-20", "--force"])
    assert vargs.command == "validate-digi"
    assert vargs.report_date == "2026-10-20"
    assert vargs.force is True

    cargs = parser.parse_args(["consolidate-final", "--report-date", "2026-10-20", "--dry-run"])
    assert cargs.command == "consolidate-final"
    assert cargs.report_date == "2026-10-20"
    assert cargs.dry_run is True


def test_run_daily_phase1_only(capsys):
    """Verify --phase 1 executes Phase 1 only and never calls Digi sync or Phase 3."""
    parser = build_parser()
    args = parser.parse_args([
        "run-daily",
        "--report-date", "2026-10-14",
        "--phase", "1",
        "--mock",
    ])

    exit_code = cmd_daily_pipeline(args)
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "PHASE 1 (10:00 AM IST): INNERVEX REPORTING WORKFLOW" in out
    assert "PHASE 1 COMPLETE: Innervex workflow" in out
    assert "PHASE 2" not in out
    assert "PHASE 3" not in out


def test_run_daily_phase2_success_records_tracker(tmp_path: Path, capsys):
    """Verify --phase 2 in mock mode executes validation and saves attempt to tracker."""
    tracker_file = tmp_path / "digi_tracker.json"
    parser = build_parser()
    args = parser.parse_args([
        "run-daily",
        "--report-date", "2026-10-14",
        "--phase", "2",
        "--mock",
        "--force",
    ])

    with patch("app.reporting.digi.DigiValidationTracker", side_effect=lambda *a, **k: DigiValidationTracker(tracker_file)):
        exit_code = cmd_daily_pipeline(args)
        assert exit_code == 0

    out = capsys.readouterr().out
    assert "PHASE 2 (11:00 AM IST OR LATER): DIGIGOLD & DIGISILVER VALIDATION" in out
    assert "PHASE 2 VALIDATION SUCCESSFUL" in out
    assert "PHASE 1" not in out
    assert "PHASE 3" not in out


def test_run_daily_phase3_blocks_if_phase2_not_validated(tmp_path: Path, capsys):
    """Verify --phase 3 triggers Phase 2 validation if not already validated."""
    tracker_file = tmp_path / "digi_tracker.json"
    parser = build_parser()
    args = parser.parse_args([
        "run-daily",
        "--report-date", "2026-10-14",
        "--phase", "3",
        "--mock",
        "--output-dir", str(tmp_path),
        "--force",
    ])

    with patch("app.reporting.digi.DigiValidationTracker", side_effect=lambda *a, **k: DigiValidationTracker(tracker_file)):
        exit_code = cmd_daily_pipeline(args)
        assert exit_code == 0

    out = capsys.readouterr().out
    assert "PHASE 3: FINAL ENROLLMENT CONSOLIDATION & PERSONAL TELEGRAM DELIVERY" in out
    assert "PHASE 1" not in out


def test_cmd_validate_digi_standalone(capsys):
    """Verify validate-digi standalone command runs and returns 0 in mock mode."""
    parser = build_parser()
    args = parser.parse_args([
        "validate-digi",
        "--report-date", "2026-10-14",
        "--mock",
        "--force",
    ])

    exit_code = cmd_validate_digi(args)
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "PHASE 2 VALIDATION SUCCESSFUL" in out


def test_phase2_idempotent_skip_when_already_validated(tmp_path: Path, capsys):
    """Verify --phase 2 skips execution with 0 when date was already successfully validated."""
    tracker_file = tmp_path / "digi_tracker.json"
    tracker = DigiValidationTracker(tracker_file)
    tracker.record_attempt(DigiValidationRecord(
        report_date=date(2026, 10, 14),
        attempt=1,
        timestamp="2026-10-14T11:05:00",
        status=DigiValidationStatus.SUCCESS,
    ))

    parser = build_parser()
    args = parser.parse_args([
        "run-daily",
        "--report-date", "2026-10-14",
        "--phase", "2",
        "--dry-run",
    ])

    with patch("app.reporting.digi.DigiValidationTracker", side_effect=lambda *a, **k: DigiValidationTracker(tracker_file)):
        exit_code = cmd_daily_pipeline(args)
        assert exit_code == 0

    out = capsys.readouterr().out
    assert "IDEMPOTENT SKIP" in out
    assert "already PASSED" in out


def test_phase2_blocked_cutoff_prevents_duplicate_retries(tmp_path: Path, capsys):
    """Verify --phase 2 exits with 1 when date previously reached BLOCKED_CUTOFF."""
    tracker_file = tmp_path / "digi_tracker.json"
    tracker = DigiValidationTracker(tracker_file)
    tracker.record_attempt(DigiValidationRecord(
        report_date=date(2026, 10, 14),
        attempt=6,
        timestamp="2026-10-14T12:30:00",
        status=DigiValidationStatus.BLOCKED_CUTOFF,
        error_message="Cutoff exceeded",
    ))

    parser = build_parser()
    args = parser.parse_args([
        "run-daily",
        "--report-date", "2026-10-14",
        "--phase", "2",
        "--dry-run",
    ])

    with patch("app.reporting.digi.DigiValidationTracker", side_effect=lambda *a, **k: DigiValidationTracker(tracker_file)):
        exit_code = cmd_daily_pipeline(args)
        assert exit_code == 1

    out = capsys.readouterr().out
    assert "[BLOCKED]" in out
    assert "No further retries allowed" in out

