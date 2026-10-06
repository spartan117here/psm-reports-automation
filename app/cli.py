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


def _set_stage(logger: logging.Logger, stage: str) -> None:
    """Update active pipeline stage in logger context filters."""
    from app.core.logging import RunContextFilter

    for f in getattr(logger, "filters", []):
        if isinstance(f, RunContextFilter):
            f.stage = stage
    for h in getattr(logger, "handlers", []):
        for f in getattr(h, "filters", []):
            if isinstance(f, RunContextFilter):
                f.stage = stage


def cmd_run(args: argparse.Namespace) -> int:
    """Execute the full reporting automation pipeline."""
    import requests
    from app.core.exceptions import (
        AuthenticationError,
        CriticalValidationError,
        InnervexConnectionError,
        InnervexResponseError,
        TransformationError,
    )
    from app.google_sheets.client import MockGoogleSheetsService
    from app.google_sheets.writer import SheetWriter
    from app.innervex.auth import InnervexAuthenticator
    from app.innervex.client import InnervexClient
    from app.innervex.reports import SchemeReportFetcher
    from app.processing.aggregators import LocationAggregator
    from app.processing.reconciliation import DataReconciler

    # 1. Resolve target report date
    target_date = parse_target_date(args.report_date)
    today = date.today()

    # 2. Resolve run ID
    run_id = generate_run_id(today)
    report_id = args.report_id or "subhiksham"

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

    # 3. Perform idempotency check
    if config_bundle.app.execution.idempotency_enabled and not args.force:
        if run_repo.is_already_processed(target_date, report_id):
            logger.warning(
                f"Report for date {target_date} ({report_id}) was already successfully processed. "
                "Skipping run to maintain idempotency. Use --force to override."
            )
            return 0

    run_meta = RunMetadata(
        run_id=run_id,
        run_date=today,
        report_date=target_date,
        source=f"Innervex:{report_id}",
        status=RunStatus.IN_PROGRESS,
    )
    run_repo.save_run(run_meta)

    # 4. Validate configuration
    _set_stage(logger, "VALIDATION")
    if report_id not in config_bundle.reports.reports:
        logger.error(f"Unknown report '{report_id}'. Configured reports: {list(config_bundle.reports.reports.keys())}")
        run_meta.status = RunStatus.FAILED
        run_meta.completed_at = datetime.now()
        run_repo.save_run(run_meta)
        return 1

    report_cfg = config_bundle.get_report(report_id)

    missing_config = []
    if not report_cfg.subschemes:
        missing_config.append(f"Sub-schemes list for '{report_id}' in config/reports.yaml")
    if not config_bundle.mappings.showroom_codes:
        missing_config.append("Showroom codes list in config/mappings.yaml")

    if missing_config:
        for item in missing_config:
            logger.error(f"Missing required configuration: {item}")
        run_meta.status = RunStatus.FAILED
        run_meta.completed_at = datetime.now()
        run_repo.save_run(run_meta)
        return 1

    val_engine = ValidationEngine(
        halt_on_critical=config_bundle.app.validation_policy.halt_on_critical_failure
    )
    val_res = ValidationRules.check_targets_sanity(config_bundle.targets.q2)
    val_engine.create_report(run_id, [val_res])

    try:
        # 5. Authenticate with Innervex
        _set_stage(logger, "AUTH")
        logger.info(f"Authenticating with Innervex at {config_bundle.innervex.base_url}...")
        session = requests.Session()
        session.headers.update(config_bundle.innervex.default_headers)
        authenticator = InnervexAuthenticator(config_bundle.innervex)
        auth_res = authenticator.login(session=session)
        if not auth_res.authenticated:
            logger.error("Authentication failed: Unable to establish session with Innervex.")
            run_meta.status = RunStatus.FAILED
            run_meta.completed_at = datetime.now()
            run_repo.save_run(run_meta)
            return 1
        logger.info("Innervex authentication established successfully.")

        # 6. Fetch the configured report
        _set_stage(logger, "DOWNLOAD")
        logger.info(f"Fetching report '{report_cfg.display_name}' for date {target_date}...")
        client = InnervexClient(config_bundle.innervex, auth_strategy=authenticator)
        client.session = session
        fetcher = SchemeReportFetcher(client)
        raw_payload = fetcher.fetch_report(
            config=report_cfg,
            report_date=target_date,
            showroom_codes=config_bundle.mappings.showroom_codes,
        )
        run_meta.row_count = raw_payload.row_count
        logger.info(f"Received {raw_payload.row_count} records from Innervex.")

        # 7. Save raw JSON
        raw_saved_path = report_repo.save_raw_report(raw_payload)
        logger.info(f"Raw report successfully saved to: {raw_saved_path}")

        # 8. Clean and validate the raw report
        _set_stage(logger, "CLEANING")
        v_non_empty = ValidationRules.check_non_empty_response(raw_payload)
        v_headers = ValidationRules.check_required_headers(
            raw_payload.raw_headers, report_cfg.output_columns
        )
        val_engine.create_report(run_id, [v_non_empty, v_headers])

        cleaner = SchemeReportCleaner(report_cfg)
        cleaning_result = cleaner.clean_and_validate(raw_payload.data, report_date=target_date)
        cleaned_records = cleaning_result.records
        run_meta.processed_count = cleaning_result.cleaned_count
        run_meta.error_count = cleaning_result.invalid_count
        logger.info(
            f"Cleaning complete: {cleaning_result.cleaned_count} valid records, "
            f"{cleaning_result.invalid_count} invalid."
        )

        # 9. Save canonical cleaned CSV/JSON
        cleaned_csv_path = report_repo.save_cleaned_records(report_id, target_date, cleaned_records)
        logger.info(f"Cleaned dataset saved: CSV: {cleaned_csv_path}")

        # 10. Reconcile raw vs cleaned (record count & RECAMOUNT total)
        _set_stage(logger, "RECONCILIATION")
        rec_result = DataReconciler.reconcile_raw_vs_cleaned(raw_payload, cleaned_records)
        v_rec = ValidationRules.check_reconciliation(
            source_count=rec_result.source_row_count,
            cleaned_count=rec_result.processed_row_count,
            source_amount=rec_result.source_amount_total,
            cleaned_amount=rec_result.processed_amount_total,
        )
        val_engine.create_report(run_id, [v_rec])
        if not rec_result.is_matched:
            logger.error(f"Reconciliation failure: {rec_result.message}")
            run_meta.status = RunStatus.FAILED
            run_meta.completed_at = datetime.now()
            run_repo.save_run(run_meta)
            return 1
        logger.info(
            f"Reconciliation successful: Raw {rec_result.source_row_count} rows "
            f"(INR {rec_result.source_amount_total:,.2f}) == Cleaned {rec_result.processed_row_count} rows "
            f"(INR {rec_result.processed_amount_total:,.2f})"
        )

        # 11. Enrich records using the EXISTING LocationResolver
        _set_stage(logger, "TRANSFORMATION")
        resolver = LocationResolver(config_bundle.mappings)
        scheme_type = "ss" if report_id == "subhiksham" else "sv"
        transformer = RecordTransformer(resolver)
        enriched_records = transformer.enrich_records(cleaned_records, scheme_type=scheme_type)
        unresolved_codes = resolver.get_all_unresolved_codes()
        run_meta.unresolved_employee_codes = unresolved_codes

        # 12. Transform records into existing A:N sheet-row structure
        sheet_rows = [RecordTransformer.to_sheet_row_values(r) for r in enriched_records]
        logger.info(f"Transformed {len(sheet_rows)} records into 14-column layout (A through N).")

        # 13. Run existing validation checks
        _set_stage(logger, "VALIDATION")
        v_dupes = ValidationRules.check_duplicate_msno(cleaned_records)
        v_amount = ValidationRules.check_amount_sanity(cleaned_records)
        v_unresolved = ValidationRules.check_unresolved_employee_codes(unresolved_codes)
        val_engine.create_report(run_id, [v_dupes, v_amount, v_unresolved])

        # 14. Calculate existing store-level aggregation using LocationAggregator
        _set_stage(logger, "AGGREGATION")
        aggregator = LocationAggregator(config_bundle.targets)
        location_metrics = aggregator.aggregate_by_location(
            records=enriched_records,
            target_branch_map=config_bundle.targets.q2,
            day_of_month=target_date.day,
        )
        logger.info(f"Calculated store aggregations across {len(location_metrics)} locations.")

        # Target monthly sheet tab name dynamically derived (e.g. 'SS - Oct' or 'SV - Oct')
        from app.google_sheets.reconciliation import derive_monthly_tab_name
        target_tab = derive_monthly_tab_name(report_cfg.target_sheet_tab_prefix, target_date)

        # 15. In --dry-run mode
        if args.dry_run:
            _set_stage(logger, "DRY_RUN")

            sa_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
            spreadsheet_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")

            live_service_available = bool((sa_path or sa_json) and spreadsheet_id)
            if live_service_available and sa_path and not Path(sa_path).is_file() and not sa_json:
                live_service_available = False

            reconcile_res = None
            if live_service_available:
                try:
                    from app.google_sheets.client import GoogleApiSheetsService
                    from app.google_sheets.reconciliation import LiveSheetReconciler
                    sheets_service = GoogleApiSheetsService(
                        service_account_path=sa_path,
                        service_account_json=sa_json,
                        read_only=True,
                    )
                    reconciler = LiveSheetReconciler(sheets_service)
                    reconcile_res = reconciler.reconcile_records(
                        spreadsheet_id=spreadsheet_id,
                        target_tab=target_tab,
                        report_date=target_date,
                        records=cleaning_result.records,
                    )
                except Exception as e:
                    logger.warning(f"Could not connect to live Google Sheet for dry-run reconciliation: {e}")
                    reconcile_res = None

            if reconcile_res is not None and not reconcile_res.status.startswith("FAILED"):
                writer = SheetWriter(sheets_service)
                append_result = writer.append_new_records(
                    spreadsheet_id=spreadsheet_id,
                    tab_name=target_tab,
                    reconciliation_result=reconcile_res,
                    records=cleaning_result.records,
                    dry_run=True,
                )
                would_append = reconcile_res.new_record_count
                would_skip = reconcile_res.already_present_count
                conflicts = reconcile_res.conflict_count
                tab_mode = f"{target_tab} (Live Read-Only Reconciliation)"
            else:
                mock_service = MockGoogleSheetsService()
                writer = SheetWriter(mock_service)
                append_result = writer.append_rows_idempotently(
                    spreadsheet_id="DRY_RUN_MOCK_SPREADSHEET",
                    tab_name=target_tab,
                    rows=sheet_rows,
                    key_column_index=3,
                )
                would_append = append_result.appended_count
                would_skip = append_result.skipped_duplicates_count
                conflicts = cleaning_result.duplicate_msno_affected_count + cleaning_result.invalid_count
                tab_mode = f"{target_tab} (Mock Simulation)"

            # Produce clear summary
            print("\n" + "=" * 70)
            print("POTHYS REPORTING AUTOMATION -- PIPELINE DRY RUN SUMMARY")
            print("=" * 70)
            print(f"Report:                     {report_cfg.display_name}")
            print(f"Report Date:                {target_date.isoformat()}")
            print(f"Run ID:                     {run_id}")
            print("Pipeline Mode:              DRY RUN (Simulation)")
            print("-" * 70)
            print(f"Downloaded records:         {raw_payload.row_count}")
            print(f"Cleaned records:            {cleaning_result.cleaned_count}")
            print(f"Invalid records:            {cleaning_result.invalid_count}")
            print(f"Duplicate MSNO count:       {cleaning_result.duplicate_msno_count}")
            print(f"Enriched records (A-N):     {len(enriched_records)}")
            print(f"Total RECAMOUNT:            INR {cleaning_result.total_recamount:,.2f}")
            print("-" * 70)
            print(f"Target Sheet Tab:           {tab_mode}")
            print(f"Incoming records:           {cleaning_result.cleaned_count}")
            print(f"Already present:            {would_skip}")
            print(f"New:                        {would_append}")
            print(f"Conflicts:                  {conflicts}")
            print(f"Would write:                {would_append}")
            print("Actual writes:              0")
            print("Live Google Sheets writes:   0")
            print("-" * 70)
            print(f"Cleaned CSV saved:          {cleaned_csv_path}")
            print(f"Raw JSON saved:             {raw_saved_path}")
            print("Status:                     DRY_RUN SUCCESS")
            print("=" * 70 + "\n")

            run_meta.status = RunStatus.DRY_RUN
            run_meta.completed_at = datetime.now()
            run_meta.execution_time_seconds = (run_meta.completed_at - run_meta.started_at).total_seconds()
            run_meta.validation_status = "PASSED"
            run_meta.notes.append("[DRY RUN] Simulation executed without external Google Sheets writes.")
            run_repo.save_run(run_meta)
            logger.info("[DRY RUN] Dry run completed successfully with 0 live writes.")
            return 0

        # 16. In live mode: Authenticate and perform safe reconciliation and append
        _set_stage(logger, "SHEETS_UPDATE")
        sa_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        spreadsheet_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")

        if not ((sa_path or sa_json) and spreadsheet_id):
            logger.error(
                "Google Sheets credentials not configured (GOOGLE_SERVICE_ACCOUNT_PATH / "
                "GOOGLE_SHEET_NEW_ENROLLMENT_ID). Cannot perform live sheet append."
            )
            run_meta.status = RunStatus.FAILED
            run_meta.completed_at = datetime.now()
            run_repo.save_run(run_meta)
            return 1

        from app.google_sheets.client import GoogleApiSheetsService
        from app.google_sheets.reconciliation import LiveSheetReconciler
        sheets_service = GoogleApiSheetsService(
            service_account_path=sa_path,
            service_account_json=sa_json,
            read_only=False,
        )

        reconciler = LiveSheetReconciler(sheets_service)
        reconcile_res = reconciler.reconcile_records(
            spreadsheet_id=spreadsheet_id,
            target_tab=target_tab,
            report_date=target_date,
            records=cleaning_result.records,
        )

        if reconcile_res.status.startswith("FAILED"):
            logger.error(f"Live reconciliation failed: {reconcile_res.error_message}")
            run_meta.status = RunStatus.FAILED
            run_meta.completed_at = datetime.now()
            run_repo.save_run(run_meta)
            return 1

        if reconcile_res.conflict_count > 0:
            logger.error(
                f"Live reconciliation detected {reconcile_res.conflict_count} conflict(s). "
                "Failing closed to prevent corrupted writes."
            )
            run_meta.status = RunStatus.FAILED
            run_meta.completed_at = datetime.now()
            run_repo.save_run(run_meta)
            return 1

        writer = SheetWriter(sheets_service)
        append_res = writer.append_new_records(
            spreadsheet_id=spreadsheet_id,
            tab_name=target_tab,
            reconciliation_result=reconcile_res,
            records=enriched_records,
            dry_run=False,
            verify_after_write=True,
        )

        print("\n" + "=" * 70)
        print("POTHYS REPORTING AUTOMATION -- PIPELINE EXECUTION SUMMARY")
        print("=" * 70)
        print(f"Report:                     {report_cfg.display_name}")
        print(f"Report Date:                {target_date.isoformat()}")
        print(f"Run ID:                     {run_id}")
        print(f"Downloaded records:         {raw_payload.row_count}")
        print(f"Cleaned records:            {cleaning_result.cleaned_count}")
        print(f"Enriched records (A-N):     {len(enriched_records)}")
        print(f"Total RECAMOUNT:            INR {cleaning_result.total_recamount:,.2f}")
        print("-" * 70)
        print(f"Target Sheet Tab:           {target_tab}")
        print(f"Incoming records:           {cleaning_result.cleaned_count}")
        print(f"Already present:            {reconcile_res.already_present_count}")
        print(f"New:                        {reconcile_res.new_record_count}")
        print(f"Conflicts:                  {reconcile_res.conflict_count}")
        print(f"Appended rows:              {append_res.appended_count}")
        print(f"Live Google Sheets writes:   {append_res.appended_count}")
        print("-" * 70)
        print(f"Cleaned CSV saved:          {cleaned_csv_path}")
        print(f"Raw JSON saved:             {raw_saved_path}")
        print(f"Status:                     SUCCESS")
        print("=" * 70 + "\n")

        run_meta.status = RunStatus.SUCCESS
        run_meta.completed_at = datetime.now()
        run_meta.execution_time_seconds = (run_meta.completed_at - run_meta.started_at).total_seconds()
        run_meta.validation_status = "PASSED"
        run_meta.notes.append(
            f"Pipeline executed successfully. Appended {append_res.appended_count} new rows to '{target_tab}'."
        )
        run_repo.save_run(run_meta)
        return 0

    except (AuthenticationError, InnervexConnectionError, InnervexResponseError) as e:
        logger.error(f"Innervex error: {e.message}")
        run_meta.status = RunStatus.FAILED
        run_meta.completed_at = datetime.now()
        run_meta.notes.append(f"Failed with {type(e).__name__}: {e.message}")
        run_repo.save_run(run_meta)
        return 1
    except CriticalValidationError as e:
        logger.critical(f"Critical validation failure: {e.message}")
        run_meta.status = RunStatus.FAILED
        run_meta.completed_at = datetime.now()
        run_meta.notes.append(f"Failed with CriticalValidationError: {e.message}")
        run_repo.save_run(run_meta)
        return 1
    except Exception as e:
        logger.error(f"Unexpected error during pipeline run: {type(e).__name__}: {e}")
        run_meta.status = RunStatus.FAILED
        run_meta.completed_at = datetime.now()
        run_meta.notes.append(f"Unexpected error: {type(e).__name__}: {e}")
        run_repo.save_run(run_meta)
        return 1


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


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Read-only live Google Sheets reconciliation against production workbook."""
    import logging
    from app.core.exceptions import GoogleAuthenticationError, GoogleSheetsError
    from app.google_sheets.client import GoogleApiSheetsService, MockGoogleSheetsService
    from app.google_sheets.reconciliation import (
        LiveSheetReconciler,
        derive_monthly_tab_name,
        format_reconciliation_report,
    )

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

    # 1. Retrieve cleaned records for report date
    cleaned_records = repo.get_cleaned_records(report_id, target_date)
    if not cleaned_records:
        raw_payload = repo.get_raw_report(report_id, target_date)
        if raw_payload:
            cleaner = SchemeReportCleaner(report_cfg)
            res = cleaner.clean_and_validate(raw_payload.data, report_date=target_date)
            cleaned_records = res.records
        else:
            print(f"Error: No raw or cleaned report found for {target_date.isoformat()} under data/")
            return 1

    # 2. Derive target monthly tab name
    tab_prefix = getattr(report_cfg, "target_sheet_tab_prefix", "SS")
    target_tab = derive_monthly_tab_name(tab_prefix, target_date)

    # 3. Resolve spreadsheet ID & credentials
    spreadsheet_id = (
        getattr(args, "spreadsheet_id", None)
        or os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    )
    sa_path = (
        getattr(args, "service_account", None)
        or os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    )
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    if getattr(args, "mock", False):
        from app.google_sheets.discovery import EXPECTED_MONTHLY_COLUMNS
        mock_meta = {
            "sheets": [{"properties": {"title": target_tab}}]
        }
        service = MockGoogleSheetsService(mock_meta)
        sid = spreadsheet_id or "MOCK_SPREADSHEET_ID"
        service.sheets[f"{sid}:{target_tab}!A1:M"] = [EXPECTED_MONTHLY_COLUMNS[:13]]
    else:
        missing_items = []
        if not sa_path and not sa_json:
            missing_items.append("Google Service Account Key: GOOGLE_SERVICE_ACCOUNT_PATH or GOOGLE_APPLICATION_CREDENTIALS not set")
        elif sa_path and not Path(sa_path).is_file():
            missing_items.append(f"Google Service Account file not found at: '{sa_path}'")
        if not spreadsheet_id:
            missing_items.append("Spreadsheet ID: GOOGLE_SHEET_NEW_ENROLLMENT_ID not set in .env (or pass --spreadsheet-id)")

        if missing_items:
            print("Status: CONFIGURATION_MISSING")
            for item in missing_items:
                print(f"  - {item}")
            return 1

        try:
            service = GoogleApiSheetsService(
                service_account_path=sa_path,
                service_account_json=sa_json,
                read_only=True,
            )
        except GoogleAuthenticationError as e:
            print(f"Status: AUTHENTICATION_FAILED\nError: {e.message}")
            return 1
        except Exception as e:
            print(f"Status: ERROR\nUnexpected Error initializing Google Sheets: {type(e).__name__}: {e}")
            return 1

    # 4. Execute read-only reconciliation
    reconciler = LiveSheetReconciler(service)
    result = reconciler.reconcile_records(
        spreadsheet_id=spreadsheet_id,
        target_tab=target_tab,
        report_date=target_date,
        records=cleaned_records,
    )

    # 5. Output report
    print(format_reconciliation_report(result))
    print("Google Sheets writes performed: 0")

    if result.conflict_count > 0 or result.status.startswith("FAILED"):
        return 1
    return 0


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

    # Command: reconcile
    reconcile_parser = subparsers.add_parser(
        "reconcile",
        help="Read-only live Google Sheets reconciliation against production workbook",
    )
    reconcile_parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Target report date (YYYY-MM-DD)",
    )
    reconcile_parser.add_argument(
        "--report-id",
        type=str,
        default="subhiksham",
        choices=["subhiksham", "viruksham"],
        help="Report identifier to reconcile",
    )
    reconcile_parser.add_argument(
        "--spreadsheet-id",
        type=str,
        default=None,
        help="Target Google Spreadsheet ID (overrides GOOGLE_SHEET_NEW_ENROLLMENT_ID)",
    )
    reconcile_parser.add_argument(
        "--service-account",
        type=str,
        default=None,
        help="Path to Google Service Account JSON (overrides GOOGLE_SERVICE_ACCOUNT_PATH)",
    )
    reconcile_parser.add_argument(
        "--mock",
        action="store_true",
        help="Simulate reconciliation using mock service for offline testing",
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
    elif args.command == "reconcile":
        sys.exit(cmd_reconcile(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
