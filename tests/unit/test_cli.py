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


def test_cli_dry_run_execution(sample_raw_payload, tmp_path):
    """Verify 'run --dry-run' completes with exit code 0 executing the core pipeline."""
    from unittest.mock import patch
    from app.innervex.auth import AuthResult
    from app.repositories.filesystem import FileSystemReportRepository

    parser = build_parser()
    args = parser.parse_args(["run", "--dry-run", "--report-date", "2026-10-04", "--force"])

    mock_auth_res = AuthResult(
        authenticated=True, http_status=200, cookies_present=True, token_present=True
    )
    with patch("app.innervex.auth.InnervexAuthenticator.login", return_value=mock_auth_res), \
         patch("app.innervex.reports.SchemeReportFetcher.fetch_report", return_value=sample_raw_payload), \
         patch.object(FileSystemReportRepository, "save_raw_report", return_value=tmp_path / "raw.json"), \
         patch.object(FileSystemReportRepository, "save_cleaned_records", return_value=tmp_path / "clean.csv"):
        exit_code = cmd_run(args)
    assert exit_code == 0


def test_dry_run_does_not_create_production_success_record(tmp_path):
    """Verify dry-run status DRY_RUN does not trigger is_already_processed."""
    from datetime import date
    from app.core.models import RunMetadata, RunStatus
    from app.repositories.filesystem import FileSystemRunRepository

    run_repo = FileSystemRunRepository(tmp_path)
    target_d = date(2026, 10, 4)

    meta = RunMetadata(
        run_id="RUN-DRY-TEST-001",
        run_date=date.today(),
        report_date=target_d,
        source="Innervex:subhiksham",
        status=RunStatus.DRY_RUN,
    )
    run_repo.save_run(meta)

    # DRY_RUN must NOT be treated as already processed
    assert run_repo.is_already_processed(target_d, "subhiksham") is False


def test_real_run_allowed_after_dry_run(tmp_path):
    """Verify a subsequent real run is still allowed without --force after a dry run."""
    from datetime import date
    from app.core.models import RunMetadata, RunStatus
    from app.repositories.filesystem import FileSystemRunRepository

    run_repo = FileSystemRunRepository(tmp_path)
    report_date = date(2026, 10, 4)

    # 1. Dry run executed
    dry_meta = RunMetadata(
        run_id="RUN-DRY-001",
        run_date=date.today(),
        report_date=report_date,
        source="Innervex:subhiksham",
        status=RunStatus.DRY_RUN,
    )
    run_repo.save_run(dry_meta)
    assert run_repo.is_already_processed(report_date, "subhiksham") is False

    # 2. Production run executed
    prod_meta = RunMetadata(
        run_id="RUN-PROD-001",
        run_date=date.today(),
        report_date=report_date,
        source="Innervex:subhiksham",
        status=RunStatus.SUCCESS,
    )
    run_repo.save_run(prod_meta)
    assert run_repo.is_already_processed(report_date, "subhiksham") is True


def test_cmd_run_dry_run_summary_output(sample_raw_payload, capsys, tmp_path):
    """Verify dry-run produces clear summary with 0 live writes and reports proposed appends."""
    from unittest.mock import patch
    from app.innervex.auth import AuthResult
    from app.repositories.filesystem import FileSystemReportRepository

    parser = build_parser()
    args = parser.parse_args(["run", "--dry-run", "--report-date", "2026-10-04", "--force"])

    mock_auth_res = AuthResult(
        authenticated=True, http_status=200, cookies_present=True, token_present=True
    )
    with patch("app.innervex.auth.InnervexAuthenticator.login", return_value=mock_auth_res), \
         patch("app.innervex.reports.SchemeReportFetcher.fetch_report", return_value=sample_raw_payload), \
         patch.object(FileSystemReportRepository, "save_raw_report", return_value=tmp_path / "raw.json"), \
         patch.object(FileSystemReportRepository, "save_cleaned_records", return_value=tmp_path / "clean.csv"):
        exit_code = cmd_run(args)

    assert exit_code == 0
    captured = capsys.readouterr().out

    assert "POTHYS REPORTING AUTOMATION -- PIPELINE DRY RUN SUMMARY" in captured
    assert "Pipeline Mode:              DRY RUN (Simulation)" in captured
    assert "Downloaded records:         4" in captured
    assert "Cleaned records:            4" in captured
    assert "Enriched records (A-N):     4" in captured
    assert "Incoming records:           4" in captured
    assert "Would write:" in captured
    assert "Actual writes:              0" in captured
    assert "Live Google Sheets writes:   0" in captured
    assert "Status:                     DRY_RUN SUCCESS" in captured



def test_cli_auth_test_missing_credentials(monkeypatch):
    """Verify 'auth-test' handles missing credentials cleanly with exit code 1."""
    from unittest.mock import patch
    from app.cli import cmd_auth_test

    parser = build_parser()
    args = parser.parse_args(["auth-test"])

    with patch("app.core.config.load_dotenv"):
        monkeypatch.delenv("INNERVEX_USERNAME", raising=False)
        monkeypatch.delenv("INNERVEX_PASSWORD", raising=False)
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


def test_cli_process_execution(capsys, sample_raw_payload):
    """Verify 'process' command cleans, validates, and audits cleanly with exit code 0."""
    from pathlib import Path
    from unittest.mock import patch
    from app.cli import cmd_process

    parser = build_parser()
    args = parser.parse_args(["process", "--report-date", "2026-10-04"])

    mock_csv_path = Path("data/processed/2026/10/04/subhiksham_memberlist_2026-10-04.csv")
    with patch("app.repositories.filesystem.FileSystemReportRepository.get_raw_report", return_value=sample_raw_payload), \
         patch("app.repositories.filesystem.FileSystemReportRepository.save_cleaned_records", return_value=mock_csv_path):
        exit_code = cmd_process(args)

    assert exit_code == 0
    captured = capsys.readouterr()
    output = captured.out

    assert "Innervex Scheme Memberlist Cleaning & Audit" in output
    assert "Raw records:                4" in output
    assert "Cleaned records:            4" in output
    assert "Cleaned CSV saved:          data/processed/2026/10/04/subhiksham_memberlist_2026-10-04.csv" in output
    assert "Status: SUCCESS" in output
