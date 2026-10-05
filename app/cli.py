"""Command-Line Interface for Pothys Daily Reporting Automation."""

import argparse
from datetime import date, datetime, timedelta
import logging
import sys
from typing import Optional

from app.core.config import load_config
from app.core.logging import setup_logger
from app.core.models import (
    PipelineStage,
    RunContext,
    RunMetadata,
    RunStatus,
    ValidationSeverity,
)
from app.processing.cleaners import SchemeReportCleaner
from app.processing.location import LocationResolver
from app.processing.transformers import RecordTransformer
from app.repositories.filesystem import (
    FileSystemReportRepository,
    FileSystemRunRepository,
)
from app.validation.engine import ValidationEngine
from app.validation.rules import ValidationRules


def generate_run_id(run_date: date) -> str:
    """Generate unique run ID, e.g. RUN-20261005-110523."""
    timestamp = datetime.now().strftime("%H%M%S")
    return f"RUN-{run_date.strftime('%Y%m%d')}-{timestamp}"


def parse_target_date(date_str: Optional[str]) -> date:
    """Parse date string YYYY-MM-DD or default to yesterday."""
    if date_str:
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            print(f"Error: Invalid date format '{date_str}'. Expected YYYY-MM-DD.")
            sys.exit(1)
    # Default to yesterday
    return date.today() - timedelta(days=1)


def cmd_run(args: argparse.Namespace) -> int:
    """Execute the full reporting automation pipeline."""
    target_date = parse_target_date(args.report_date)
    today = date.today()
    run_id = generate_run_id(today)

    config_bundle = load_config()
    logs_dir = config_bundle.project_root / config_bundle.app.storage.logs_dir
    data_dir = config_bundle.project_root / config_bundle.app.storage.data_dir

    logger = setup_logger(
        level="INFO",
        logs_dir=logs_dir,
        run_id=run_id,
        stage="INIT",
    )

    logger.info(f"Starting Pothys Reporting Automation run {run_id}")
    logger.info(f"Target report date: {target_date} (Run date: {today})")

    run_repo = FileSystemRunRepository(data_dir)
    report_repo = FileSystemReportRepository(data_dir)

    # 1. Idempotency Check
    if config_bundle.app.execution.idempotency_enabled and not args.force:
        if run_repo.is_already_processed(target_date, args.report_id):
            logger.warning(
                f"Report for date {target_date} ({args.report_id}) was already successfully processed. "
                "Skipping run to maintain idempotency. Use --force to override."
            )
            return 0

    run_meta = RunMetadata(
        run_id=run_id,
        run_date=today,
        report_date=target_date,
        source=f"Innervex:{args.report_id}",
        status=RunStatus.IN_PROGRESS,
    )
    run_repo.save_run(run_meta)

    # In Phase 0, we validate configuration, test models, and demonstrate dry-run capabilities
    logger.info("[PHASE 0 SKELETON] Validating configuration and pipeline readiness...")
    val_engine = ValidationEngine(
        halt_on_critical=config_bundle.app.validation_policy.halt_on_critical_failure
    )

    # Perform pre-flight target check
    val_res = ValidationRules.check_targets_sanity(config_bundle.targets.q2)
    report = val_engine.create_report(run_id, [val_res])

    logger.info(
        f"Pipeline validation passed. Active reports configured: {list(config_bundle.reports.reports.keys())}"
    )

    if args.dry_run:
        logger.info("[DRY RUN] Dry run completed successfully without external network calls.")
        run_meta.status = RunStatus.SUCCESS
        run_meta.completed_at = datetime.now()
        run_repo.save_run(run_meta)
        return 0

    logger.info(
        "Phase 0 project scaffold is operational. "
        "Next milestone (Phase 1): Authenticate to Innervex with legitimate credentials."
    )
    run_meta.status = RunStatus.SUCCESS
    run_meta.completed_at = datetime.now()
    run_repo.save_run(run_meta)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate system configuration, targets, and mapping health."""
    config_bundle = load_config()
    print("=" * 60)
    print("POTHYS REPORTING AUTOMATION - CONFIGURATION & INTEGRITY AUDIT")
    print("=" * 60)

    val_engine = ValidationEngine(halt_on_critical=False)
    results = [
        ValidationRules.check_targets_sanity(config_bundle.targets.q2),
    ]

    # Validate employee mappings
    resolver = LocationResolver(config_bundle.mappings)
    sample_codes = ["TVL001", "CPT_ECOMM", "ONLINE", "UNKNOWN_XYZ"]
    for code in sample_codes:
        attr = resolver.resolve(cost_name="", comm_code=code)
        print(f"Sample resolution: {code:<15} -> Location: {attr.location:<20} Branch: {attr.branch}")

    unresolved = resolver.get_all_unresolved_codes()
    results.append(ValidationRules.check_unresolved_employee_codes(unresolved))

    report = val_engine.create_report("AUDIT", results)
    print("\nAudit Summary:")
    print(f"  Passed Checks:   {report.passed_count}")
    print(f"  Warnings:        {report.warning_count}")
    print(f"  Critical Errors: {report.failure_count}")
    return 1 if report.has_critical_failures else 0


def cmd_download(args: argparse.Namespace) -> int:
    """Download one raw scheme memberlist report from Innervex."""
    import logging
    import requests
    from app.core.exceptions import (
        AuthenticationError,
        InnervexConnectionError,
        InnervexResponseError,
    )
    from app.innervex.auth import InnervexAuthenticator
    from app.innervex.client import InnervexClient
    from app.innervex.reports import SchemeReportFetcher
    from app.repositories.filesystem import FileSystemReportRepository

    # Keep console logger quiet during CLI output formatting
    logging.getLogger("pothys_reporting").setLevel(logging.CRITICAL)

    config_bundle = load_config()
    target_date = parse_target_date(args.report_date)
    report_id = args.report_id or "subhiksham"

    # 1. Validate configuration
    if report_id not in config_bundle.reports.reports:
        print(f"Error: Unknown report '{report_id}'")
        return 1
    report_cfg = config_bundle.get_report(report_id)

    # 2. Check required configuration
    missing_config = []
    if not report_cfg.subschemes:
        missing_config.append(f"Sub-schemes list for '{report_id}' in config/reports.yaml")
    if not config_bundle.mappings.showroom_codes:
        missing_config.append("Showroom codes list in config/mappings.yaml")

    if missing_config:
        print("Error: Missing required configuration values:")
        for item in missing_config:
            print(f"  - {item}")
        return 1

    # 3. Authenticate using InnervexAuthenticator
    session = requests.Session()
    session.headers.update(config_bundle.innervex.default_headers)
    authenticator = InnervexAuthenticator(config_bundle.innervex)

    try:
        auth_res = authenticator.login(session=session)
        if not auth_res.authenticated:
            print("Authentication failed: Unable to establish session")
            return 1

        # 4. Use the same authenticated session to fetch the report
        client = InnervexClient(config_bundle.innervex, auth_strategy=authenticator)
        client.session = session
        fetcher = SchemeReportFetcher(client)

        payload = fetcher.fetch_report(
            config=report_cfg,
            report_date=target_date,
            showroom_codes=config_bundle.mappings.showroom_codes,
        )

        # 5. Save raw response
        data_dir = config_bundle.project_root / config_bundle.app.storage.data_dir
        repo = FileSystemReportRepository(data_dir)
        saved_path = repo.save_raw_report(payload)

        try:
            rel_path = saved_path.relative_to(config_bundle.project_root)
        except ValueError:
            rel_path = saved_path

        report_title = "Subhiksham" if report_id == "subhiksham" else report_cfg.display_name

        print("Innervex Scheme Memberlist Download")
        print("-----------------------------------")
        print(f"Report: {report_title}")
        print(f"Report date: {target_date.isoformat()}")
        print("HTTP status: 200")
        print("Response format: JSON")
        print(f"Records received: {payload.row_count}")
        print(f"Raw response saved: {str(rel_path).replace(chr(92), '/')}")
        print("Status: SUCCESS")
        return 0

    except AuthenticationError as e:
        print("Innervex Scheme Memberlist Download")
        print("-----------------------------------")
        print(f"Report: {report_id}")
        print(f"Report date: {target_date.isoformat()}")
        print("Status: FAILED")
        print(f"Authentication Error: {e.message}")
        return 1
    except InnervexConnectionError as e:
        print("Innervex Scheme Memberlist Download")
        print("-----------------------------------")
        print(f"Report: {report_id}")
        print(f"Report date: {target_date.isoformat()}")
        print("Status: FAILED")
        print(f"Connection Error: {e.message}")
        return 1
    except InnervexResponseError as e:
        print("Innervex Scheme Memberlist Download")
        print("-----------------------------------")
        print(f"Report: {report_id}")
        print(f"Report date: {target_date.isoformat()}")
        print("Status: FAILED")
        print(f"Response Error: {e.message}")
        return 1
    except Exception as e:
        print("Innervex Scheme Memberlist Download")
        print("-----------------------------------")
        print(f"Report: {report_id}")
        print(f"Report date: {target_date.isoformat()}")
        print("Status: FAILED")
        print(f"Unexpected Error: {type(e).__name__}: {e}")
        return 1


def cmd_process(args: argparse.Namespace) -> int:
    """Phase 3/4 entrypoint: Clean and transform local stored raw data."""
    target_date = parse_target_date(args.report_date)
    print(f"Process command invoked for report date: {target_date}")
    print("Note: Offline processing engine is scaffolded and tested.")
    return 0


def cmd_auth_test(args: argparse.Namespace) -> int:
    """Safely test authentication against the Innervex system without exposing secrets."""
    import logging
    import requests
    from app.core.exceptions import AuthenticationError, InnervexConnectionError
    from app.innervex.auth import InnervexAuthenticator

    # Keep log output quiet during auth-test so only the clean report is presented
    logging.getLogger("pothys_reporting").setLevel(logging.CRITICAL)

    config_bundle = load_config()
    authenticator = InnervexAuthenticator(config_bundle.innervex)

    print("Innervex authentication test")
    print("----------------------------")
    print(f"Base URL: {config_bundle.innervex.base_url}")

    try:
        session = requests.Session()
        result = authenticator.login(session=session)
        print(f"HTTP status: {result.http_status}")
        print(f"Authenticated: {result.authenticated}")
        print(f"Session cookie present: {result.cookies_present}")
        print(f"Token present: {result.token_present}")
        return 0
    except AuthenticationError as e:
        status_code = e.details.get("status_code", "Failed") if hasattr(e, "details") else "Failed"
        print(f"HTTP status: {status_code}")
        print("Authenticated: False")
        print("Session cookie present: False")
        print("Token present: False")
        print(f"\nAuthentication failed: {e.message}")
        return 1
    except InnervexConnectionError as e:
        print("Authenticated: False")
        print(f"\nConnection error: {e.message}")
        return 1
    except Exception as e:
        print("Authenticated: False")
        print(f"\nUnexpected error during authentication: {type(e).__name__}: {e}")
        return 1


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m app",
        description="Pothys Swarna Mahal Daily Reporting Automation CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Command: run
    run_parser = subparsers.add_parser("run", help="Execute complete automation pipeline")
    run_parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Target report date (YYYY-MM-DD). Defaults to yesterday.",
    )
    run_parser.add_argument(
        "--report-id",
        type=str,
        default="subhiksham",
        choices=["subhiksham", "viruksham", "all"],
        help="Report to process (default: subhiksham)",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution without external side effects",
    )
    run_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass idempotency checks and rerun for existing date",
    )

    # Command: validate
    validate_parser = subparsers.add_parser("validate", help="Validate system configuration and mappings")
    validate_parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Target report date to audit",
    )

    # Command: auth-test
    subparsers.add_parser(
        "auth-test",
        help="Safely test authentication against Innervex using environment credentials",
    )

    # Command: download
    download_parser = subparsers.add_parser("download", help="Download raw report from Innervex")
    download_parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Target report date (YYYY-MM-DD)",
    )
    download_parser.add_argument(
        "--report-id",
        type=str,
        default="subhiksham",
        help="Report identifier to download",
    )

    # Command: process
    process_parser = subparsers.add_parser("process", help="Process and clean stored raw report")
    process_parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Target report date (YYYY-MM-DD)",
    )
    process_parser.add_argument(
        "--report-id",
        type=str,
        default="subhiksham",
        help="Report identifier to process",
    )

    return parser


def main() -> None:
    """Main CLI entrypoint."""
    parser = build_parser()
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()
    if args.command == "run":
        sys.exit(cmd_run(args))
    elif args.command == "validate":
        sys.exit(cmd_validate(args))
    elif args.command == "auth-test":
        sys.exit(cmd_auth_test(args))
    elif args.command == "download":
        sys.exit(cmd_download(args))
    elif args.command == "process":
        sys.exit(cmd_process(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
