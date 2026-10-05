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


def test_cli_auth_test_missing_credentials(monkeypatch):
    """Verify 'auth-test' handles missing credentials cleanly with exit code 1."""
    monkeypatch.delenv("INNERVEX_USERNAME", raising=False)
    monkeypatch.delenv("INNERVEX_PASSWORD", raising=False)

    from app.cli import cmd_auth_test
    parser = build_parser()
    args = parser.parse_args(["auth-test"])
    exit_code = cmd_auth_test(args)
    assert exit_code == 1


def test_cli_auth_test_mocked_success(monkeypatch):
    """Verify 'auth-test' succeeds with exit code 0 when mock returns 200."""
    from unittest.mock import MagicMock, patch
    import requests

    monkeypatch.setenv("INNERVEX_USERNAME", "test_user")
    monkeypatch.setenv("INNERVEX_PASSWORD", "test_pass")

    fake_resp = MagicMock(spec=requests.Response)
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"status": True, "token": "mock-token-abc"}

    from app.cli import cmd_auth_test
    parser = build_parser()
    args = parser.parse_args(["auth-test"])

    with patch("requests.Session.post", return_value=fake_resp):
        exit_code = cmd_auth_test(args)
        assert exit_code == 0
