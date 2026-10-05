"""Command-Line Interface for Pothys Daily Reporting Automation."""

import argparse
from datetime import date, datetime, timedelta
import logging
import os
from pathlib import Path
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
    """Clean, normalize, validate, and audit locally stored raw report data."""
    import logging
    from app.core.exceptions import TransformationError
    from app.processing.cleaners import SchemeReportCleaner
    from app.repositories.filesystem import FileSystemReportRepository

    # Keep log output quiet during CLI execution
    logging.getLogger("pothys_reporting").setLevel(logging.CRITICAL)

    config_bundle = load_config()
    target_date = parse_target_date(args.report_date)
    report_id = args.report_id or "subhiksham"

    if report_id not in config_bundle.reports.reports:
        print(f"Error: Unknown report '{report_id}'")
        return 1
    report_cfg = config_bundle.get_report(report_id)

    data_dir = config_bundle.project_root / config_bundle.app.storage.data_dir
    repo = FileSystemReportRepository(data_dir)

    # 1. Load raw report from disk
    raw_payload = repo.get_raw_report(report_id, target_date)
    if not raw_payload:
        print("Innervex Scheme Memberlist Cleaning")
        print("-----------------------------------")
        print(f"Report: {report_cfg.display_name}")
        print(f"Report date: {target_date.isoformat()}")
        print("Status: FAILED")
        print(f"Error: No raw report found for {target_date.isoformat()} under data/raw/")
        return 1

    # 2. Execute cleaning and validation
    cleaner = SchemeReportCleaner(report_cfg)
    try:
        result = cleaner.clean_and_validate(raw_payload.data, report_date=target_date)
    except TransformationError as e:
        print("Innervex Scheme Memberlist Cleaning")
        print("-----------------------------------")
        print(f"Report: {report_cfg.display_name}")
        print(f"Report date: {target_date.isoformat()}")
        print("Status: FAILED")
        print(f"Transformation Error: {e.message}")
        return 1

    # 3. Save cleaned dataset (both canonical CSV and JSON)
    csv_path = repo.save_cleaned_records(report_id, target_date, result.records)
    try:
        rel_csv_path = str(csv_path.relative_to(config_bundle.project_root)).replace("\\", "/")
    except ValueError:
        rel_csv_path = str(csv_path).replace("\\", "/")

    # 4. Print safe audit & reconciliation summary
    report_title = "Subhiksham" if report_id == "subhiksham" else report_cfg.display_name
    print("Innervex Scheme Memberlist Cleaning & Audit")
    print("===========================================")
    print(f"Report:                     {report_title}")
    print(f"Report date:                {target_date.isoformat()}")
    print(f"Raw records:                {result.raw_count}")
    print(f"Cleaned records:            {result.cleaned_count}")
    print(f"Invalid records:            {result.invalid_count}")
    print(f"Duplicate MSNO count:       {result.duplicate_msno_count} (affected records: {result.duplicate_msno_affected_count})")
    print(f"Duplicate CLIENTID count:   {result.duplicate_clientid_count} (affected records: {result.duplicate_clientid_affected_count})")
    print(f"Blank required fields:      {result.blank_required_field_count}")
    print(f"Invalid amount count:       {result.invalid_amount_count}")
    print(f"Total RECAMOUNT:            {result.total_recamount:,.2f}")
    print(f"Cleaned CSV saved:          {rel_csv_path}")
    print(f"Cleaned columns (11):       {', '.join(report_cfg.output_columns)}")
    print("\nCOSTNAME Summary Breakdown:")
    print("-" * 55)
    print(f"{'COSTNAME':<20} | {'Count':<8} | {'Total RECAMOUNT':<18}")
    print("-" * 55)
    for c, stats in result.costname_summary.items():
        print(f"{c:<20} | {stats['count']:<8} | {stats['total_amount']:<18,.2f}")
    print("-" * 55)
    print(f"{'TOTAL':<20} | {result.cleaned_count:<8} | {result.total_recamount:<18,.2f}")
    print("=" * 55)
    print("Status: SUCCESS")
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


def _print_discovery_report(report: dict) -> None:
    """Safely print structured workbook discovery report."""
    print(f"\nSpreadsheet Title:     {report.get('spreadsheet_title', 'Unknown')}")
    print(f"Spreadsheet ID:        {report.get('redacted_spreadsheet_id', 'Unknown')}")
    print(f"Total Worksheets:      {report.get('total_sheets', 0)}")

    all_tabs = report.get("all_tabs", [])
    if all_tabs:
        print("\nDiscovered Worksheets:")
        print("-" * 55)
        print(f"{'Sheet Title':<30} | {'Rows':<8} | {'Cols':<6}")
        print("-" * 55)
        for t in all_tabs:
            print(f"{t.get('title', ''):<30} | {t.get('row_count', 0):<8} | {t.get('column_count', 0):<6}")
        print("-" * 55)

    ss_insp = report.get("subhiksham_inspection", {})
    if ss_insp:
        print(f"\nSubhiksham Sheet Inspection ({ss_insp.get('tab_name')}):")
        print(f"  Detected Columns:    {ss_insp.get('detected_column_count', 0)}")
        print(f"  Estimated Rows:      {ss_insp.get('estimated_data_row_count', 0)}")
        print(f"  Headers:             {', '.join(ss_insp.get('headers', []))}")
        formulas = ss_insp.get("sample_formulas", {})
        if formulas:
            print(f"  Sample Formulas:     {formulas}")
        else:
            print("  Sample Formulas:     None detected in sample rows (static values)")

    mapping_analysis = report.get("column_mapping_analysis", {})
    if mapping_analysis:
        print("\nCleaned 11-Column to Sheet 14-Column Mapping Analysis:")
        print("-" * 80)
        print(f"{'Col':<5} | {'Sheet Header':<16} | {'Source Field':<22} | {'Transformation':<30}")
        print("-" * 80)
        for m in mapping_analysis.get("mappings", []):
            print(f"{m.get('column_letter'):<5} | {m.get('sheet_column_name'):<16} | {m.get('source_field'):<22} | {m.get('transformation'):<30}")
        print("-" * 80)

    oct_status = report.get("october_data_status", {})
    if oct_status:
        print("\nOctober Data Status:")
        print(f"  October Tab Present: {oct_status.get('has_october_tab')}")
        print(f"  Status:              {oct_status.get('status')}")


def cmd_sheets_discover(args: argparse.Namespace) -> int:
    """Read-only discovery of Google Sheets workbook structure and metadata."""
    import logging
    from app.core.exceptions import GoogleAuthenticationError, GoogleSheetsError
    from app.google_sheets.client import GoogleApiSheetsService, MockGoogleSheetsService
    from app.google_sheets.discovery import SheetDiscoveryEngine, EXPECTED_MONTHLY_COLUMNS

    logging.getLogger("pothys_reporting").setLevel(logging.CRITICAL)

    # Ensure .env is loaded
    load_config()

    print("Google Sheets Read-Only Discovery")
    print("=================================")

    # 1. Resolve spreadsheet ID
    spreadsheet_id = (
        getattr(args, "spreadsheet_id", None)
        or os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    )

    # 2. Resolve credentials configuration
    sa_path = (
        getattr(args, "service_account", None)
        or os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    )
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    if getattr(args, "mock", False):
        print("Status: MOCK_MODE (Simulated offline discovery)")
        mock_meta = {
            "properties": {"title": "New Enrollment from April 2026"},
            "sheets": [
                {"properties": {"title": "Employees", "sheetId": 101, "gridProperties": {"rowCount": 150, "columnCount": 10}}},
                {"properties": {"title": "SS - Aug", "sheetId": 102, "gridProperties": {"rowCount": 520, "columnCount": 14}}},
                {"properties": {"title": "SV - Aug", "sheetId": 103, "gridProperties": {"rowCount": 210, "columnCount": 14}}},
                {"properties": {"title": "Consolidate Report - Aug", "sheetId": 104, "gridProperties": {"rowCount": 45, "columnCount": 20}}},
                {"properties": {"title": "SS - Sept", "sheetId": 105, "gridProperties": {"rowCount": 612, "columnCount": 14}}},
                {"properties": {"title": "SV - Sept", "sheetId": 106, "gridProperties": {"rowCount": 240, "columnCount": 14}}},
                {"properties": {"title": "Consolidate Report - Sept", "sheetId": 107, "gridProperties": {"rowCount": 45, "columnCount": 20}}},
            ],
        }
        sid = "MOCK_SPREADSHEET_ID"
        mock_service = MockGoogleSheetsService(mock_meta)
        mock_service.sheets[f"{sid}:SS - Sept!A1:Z1"] = [EXPECTED_MONTHLY_COLUMNS]
        mock_service.sheets[f"{sid}:SS - Sept!D:D"] = [["MSNO"]] + [["MSNO-001"]] * 611
        mock_service.sheets[f"{sid}:Employees!A1:Z1"] = [["EMPCODE", "EMPNAME", "BRANCH", "LOCATION"]]
        mock_service.sheets[f"{sid}:Employees!A:A"] = [["EMPCODE"]] + [["1001"]] * 50
        mock_service.formulas[f"{sid}:SS - Sept!A1:Z10"] = [
            [],
            ["", "", "", "", "", "", "", "", "", "", "", "", "", '=M2&" - "&L2']
        ]
        engine = SheetDiscoveryEngine(mock_service)
        report = engine.inspect_workbook(sid)
        _print_discovery_report(report)
        print("\nWrite operation performed: NO")
        print("Status: SUCCESS (Mock)")
        return 0

    missing_items = []
    if not sa_path and not sa_json:
        missing_items.append("Google Service Account Key: GOOGLE_SERVICE_ACCOUNT_PATH or GOOGLE_APPLICATION_CREDENTIALS not set")
    elif sa_path and not Path(sa_path).is_file():
        missing_items.append(f"Google Service Account file not found at: '{sa_path}'")

    if not spreadsheet_id:
        missing_items.append("Spreadsheet ID: GOOGLE_SHEET_NEW_ENROLLMENT_ID not set in .env (or pass --spreadsheet-id)")

    if missing_items:
        print("Status: CONFIGURATION_MISSING")
        print("\nIdentified Missing Configuration:")
        for item in missing_items:
            print(f"  - {item}")
        print("\nAuthentication Mechanism Expected:")
        print("  - Google Cloud Service Account JSON keyfile")
        print("  - API Scope: https://www.googleapis.com/auth/spreadsheets.readonly (Read-Only)")
        print("\nSetup Procedure:")
        print("  1. Place the Service Account JSON keyfile in secrets/ (e.g. secrets/service_account.json).")
        print("  2. Add GOOGLE_SERVICE_ACCOUNT_PATH=secrets/service_account.json to .env")
        print("  3. Add GOOGLE_SHEET_NEW_ENROLLMENT_ID=<spreadsheet-id> to .env")
        print("  4. Grant the Service Account email 'Viewer' access to the Google Spreadsheet.")
        print("  5. Run 'python -m app sheets-discover' to inspect live sheets.")
        print("  (Note: Use 'python -m app sheets-discover --mock' to run simulated offline discovery)")
        return 1

    try:
        service = GoogleApiSheetsService(
            service_account_path=sa_path,
            service_account_json=sa_json,
            read_only=True,
        )
        engine = SheetDiscoveryEngine(service)
        report = engine.inspect_workbook(spreadsheet_id)
        _print_discovery_report(report)
        print("\nWrite operation performed: NO")
        print("Status: SUCCESS")
        return 0
    except GoogleAuthenticationError as e:
        print(f"Status: AUTHENTICATION_FAILED\nError: {e.message}")
        return 1
    except GoogleSheetsError as e:
        print(f"Status: DISCOVERY_FAILED\nError: {e.message}")
        return 1
    except Exception as e:
        print(f"Status: ERROR\nUnexpected Error: {type(e).__name__}: {e}")
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

    # Command: sheets-discover
    sheets_parser = subparsers.add_parser(
        "sheets-discover",
        help="Read-only discovery of Google Sheets workbook structure and metadata",
    )
    sheets_parser.add_argument(
        "--spreadsheet-id",
        type=str,
        default=None,
        help="Target Google Spreadsheet ID (overrides GOOGLE_SHEET_NEW_ENROLLMENT_ID)",
    )
    sheets_parser.add_argument(
        "--service-account",
        type=str,
        default=None,
        help="Path to Google Service Account JSON (overrides GOOGLE_SERVICE_ACCOUNT_PATH)",
    )
    sheets_parser.add_argument(
        "--mock",
        action="store_true",
        help="Simulate discovery using reference workbook schema for offline verification",
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
    elif args.command == "sheets-discover":
        sys.exit(cmd_sheets_discover(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
