"""Unit tests for the Command-Line Interface."""

import argparse
from app.cli import build_parser, cmd_run, cmd_validate


def test_cli_parser_defaults():
    """Verify default parser settings for 'run' command."""
    parser = build_parser()
    args = parser.parse_args(["run"])
    assert args.command == "run"
    assert args.report_date is None
    assert args.report_id == "subhiksham"
    assert args.dry_run is False
    assert args.force is False


def test_cli_parser_custom_args():
    """Verify parsing with custom flags."""
    parser = build_parser()
    args = parser.parse_args([
        "run",
        "--report-date", "2026-10-04",
        "--report-id", "viruksham",
        "--dry-run",
        "--force",
    ])
    assert args.command == "run"
    assert args.report_date == "2026-10-04"
    assert args.report_id == "viruksham"
    assert args.dry_run is True
    assert args.force is True


def test_cli_validate_execution():
    """Verify 'validate' command runs successfully."""
    parser = build_parser()
    args = parser.parse_args(["validate"])
    exit_code = cmd_validate(args)
    assert exit_code == 0


def test_cli_dry_run_execution():
    """Verify 'run --dry-run' completes with exit code 0."""
    parser = build_parser()
    args = parser.parse_args(["run", "--dry-run", "--report-date", "2026-10-04", "--force"])
    exit_code = cmd_run(args)
    assert exit_code == 0
