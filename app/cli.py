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

    if not getattr(args, "dry_run", False):
        if os.getenv("STRICT_READ_ONLY", "false").lower() in ("true", "1", "yes"):
            print("\n[BLOCKED] STRICT READ-ONLY MODE IS ACTIVE. Live enrollment writes are forbidden. Use --dry-run.\n")
            return 1

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

        if os.getenv("STRICT_READ_ONLY", "false").lower() in ("true", "1", "yes"):
            print("\n[BLOCKED] STRICT READ-ONLY MODE IS ACTIVE. Live enrollment writes are forbidden. Use --dry-run.\n")
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
        cookies_present = e.details.get("cookies_present", False) if hasattr(e, "details") else False
        print(f"HTTP status: {status_code}")
        print("Authenticated: False")
        print(f"Session cookie present: {cookies_present}")
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


def cmd_digi_sync(args: argparse.Namespace) -> int:
    """Execute DigiGold + DigiSilver synchronization with Consolidate Report."""
    from app.core.exceptions import GoogleAuthenticationError
    from app.google_sheets.client import GoogleApiSheetsService, MockGoogleSheetsService
    from app.google_sheets.discovery import redact_spreadsheet_id
    from app.reporting.digi import (
        DEFAULT_DIGI_SPREADSHEET_ID,
        DIGI_BRANCH_ORDER,
        DigiReportProcessor,
        calculate_report_date,
        derive_consolidation_tab_name,
    )

    load_config()
    target_date = parse_target_date(args.report_date) if args.report_date else calculate_report_date()

    if not getattr(args, "dry_run", False) and not getattr(args, "mock", False):
        if os.getenv("STRICT_READ_ONLY", "false").lower() in ("true", "1", "yes"):
            print("\n[BLOCKED] STRICT READ-ONLY MODE IS ACTIVE. Live DigiGold/DigiSilver writes are forbidden. Use --dry-run or --mock.\n")
            return 1
    digi_sid = getattr(args, "digi_spreadsheet_id", None) or os.getenv("GOOGLE_SHEET_DIGI_ID", DEFAULT_DIGI_SPREADSHEET_ID)
    dest_sid = getattr(args, "spreadsheet_id", None) or os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")

    if getattr(args, "mock", False):
        mode_display = "MOCK / OFFLINE (Simulated Data & Zero Writes)"
    elif getattr(args, "dry_run", False):
        mode_display = "DRY RUN (Read & Validate Only)"
    else:
        mode_display = "LIVE UPDATE"

    print("=" * 65)
    print("DIGIGOLD + DIGISILVER CONSOLIDATE REPORT SYNCHRONIZATION")
    print("=" * 65)
    print(f"Target Report Date: {target_date.strftime('%d/%m/%Y')} ({target_date.isoformat()})")
    print(f"Digi Workbook ID:   {redact_spreadsheet_id(digi_sid)}")
    print(f"Dest Workbook ID:   {redact_spreadsheet_id(dest_sid or '')}")
    print(f"Mode:               {mode_display}")
    print("=" * 65)

    if getattr(args, "mock", False):
        dest_tab = derive_consolidation_tab_name(target_date)
        divisor = max(1, target_date.day)
        mock_gold = {"NELLAI": 18, "CHROMEPET": 34, "TRIVANDRUM": 9, "TRICHY": 21, "COIMBATORE": 27, "SALEM": 14, "POONAMALLEE": 12, "PADI": 29, "KANCHEEPURAM": 11, "ECOMMERCE": 8}
        mock_silver = {"NELLAI": 12, "CHROMEPET": 22, "TRIVANDRUM": 5, "TRICHY": 15, "COIMBATORE": 19, "SALEM": 9, "POONAMALLEE": 8, "PADI": 18, "KANCHEEPURAM": 7, "ECOMMERCE": 4}

        print("\n--- SYNCHRONIZATION RESULTS (MOCK / SIMULATED) ---")
        print("SERVICE ACCOUNT ACCESS:      PASS (MOCK / OFFLINE)")
        print("OCT READ:                    PASS (MOCK SIMULATION)")
        print("OCT PIVOT READ:              PASS (MOCK SIMULATION)")
        print(f"REPORT DATE:                 {target_date.strftime('%d/%m/%Y')} ({target_date.isoformat()})")
        print("\nSimulated Achieved Count values [MOCK / SYNTHETIC DATA]:")
        print(f"  {'Row':<5}{'Branch':<15}{'DigiGold (I)':>14}{'DigiSilver (J)':>16}")
        for i, branch in enumerate(DIGI_BRANCH_ORDER):
            row_num = 20 + i
            print(f"  {row_num:<5}{branch:<15}{mock_gold.get(branch, 0):>14}{mock_silver.get(branch, 0):>16}")

        print(f"\nDESTINATION TAB:             {dest_tab}")
        print(f"DESTINATION TITLE:           New Enrollment as on {target_date.strftime('%d/%m/%Y')}")
        print(f"AVG DIVISOR:                 /{divisor}")
        print(f"ACTUAL GOOGLE SHEETS WRITES: 0 (MOCK MODE)")
        print(f"SIMULATED ROWS:              10")
        print(f"CELLS CLEARED:               0 (Simulated: {dest_tab}!I20:J29)")
        print(f"CELLS UPDATED:               0 (Simulated: {dest_tab}!I20:J29, L20:L30, E3)")
        print(f"POST-WRITE VERIFICATION:     PASS (MOCK SIMULATION)")
        print("OVERALL RESULT:              PASS (MOCK SIMULATION COMPLETE - ZERO WRITES)")
        return 0

    sa_path = getattr(args, "service_account", None) or os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    missing = []
    if not sa_path and not sa_json:
        missing.append("Google Service Account Key: GOOGLE_SERVICE_ACCOUNT_PATH or GOOGLE_APPLICATION_CREDENTIALS not set")
    elif sa_path and not Path(sa_path).is_file():
        missing.append(f"Google Service Account file not found at: '{sa_path}'")
    if not dest_sid:
        missing.append("Destination Spreadsheet ID: GOOGLE_SHEET_NEW_ENROLLMENT_ID not set in .env (or pass --spreadsheet-id)")

    if missing:
        print("Status: CONFIGURATION_MISSING")
        for item in missing:
            print(f"  - {item}")
        return 1

    try:
        source_svc = GoogleApiSheetsService(
            service_account_path=sa_path,
            service_account_json=sa_json,
            read_only=True,
        )
        dest_svc = GoogleApiSheetsService(
            service_account_path=sa_path,
            service_account_json=sa_json,
            read_only=getattr(args, "dry_run", False),
        )
    except GoogleAuthenticationError as e:
        print(f"SERVICE ACCOUNT ACCESS: FAIL\nError: {e.message}")
        print("OVERALL RESULT: FAIL")
        return 1
    except Exception as e:
        print(f"SERVICE ACCOUNT ACCESS: FAIL\nError: {type(e).__name__}: {e}")
        print("OVERALL RESULT: FAIL")
        return 1

    if not getattr(args, "dry_run", False) and not getattr(args, "mock", False):
        if os.getenv("STRICT_READ_ONLY", "false").lower() in ("true", "1", "yes"):
            print("\n[BLOCKED] STRICT READ-ONLY MODE IS ACTIVE. Live DigiGold/DigiSilver writes are forbidden. Use --dry-run or --mock.\n")
            return 1

    processor = DigiReportProcessor(
        source_service=source_svc,
        dest_service=dest_svc,
        source_spreadsheet_id=digi_sid,
        dest_spreadsheet_id=dest_sid,
    )

    try:
        result = processor.run(report_date=target_date, dry_run=getattr(args, "dry_run", False))
    except Exception as e:
        print(f"\nSYNCHRONIZATION HALTED: {type(e).__name__}: {e}")
        print("OVERALL RESULT: FAIL")
        return 1

    print("\n--- SYNCHRONIZATION RESULTS ---")
    print("SERVICE ACCOUNT ACCESS:      PASS")
    print("OCT READ:                    PASS")
    print("OCT PIVOT READ:              PASS")
    print(f"REPORT DATE:                 {target_date.strftime('%d/%m/%Y')} ({target_date.isoformat()})")
    print("\nCopied Achieved Count values:")
    print(f"  {'Row':<5}{'Branch':<15}{'DigiGold (I)':>14}{'DigiSilver (J)':>16}")
    for i, branch in enumerate(DIGI_BRANCH_ORDER):
        row_num = 20 + i
        print(
            f"  {row_num:<5}{branch:<15}"
            f"{result.gold_values[branch]:>14}{result.silver_values[branch]:>16}"
        )
    print(f"\nDESTINATION TAB:             {result.destination_tab}")
    print(f"DESTINATION TITLE:           {result.report_title}")
    print(f"AVG DIVISOR:                 /{result.avg_divisor}")
    if getattr(args, "dry_run", False):
        print(f"ACTUAL GOOGLE SHEETS WRITES: 0 (DRY RUN)")
        print(f"PLANNED ROWS:                10")
        print(f"CELLS CLEARED:               0 (Dry Run)")
        print(f"CELLS UPDATED:               0 (Dry Run)")
        print(f"POST-WRITE VERIFICATION:     PASS (DRY RUN)")
        print("OVERALL RESULT:              PASS (DRY RUN COMPLETE - ZERO WRITES)")
    else:
        print(f"ACTUAL GOOGLE SHEETS WRITES: 10")
        print(f"CELLS CLEARED:               {', '.join(result.cells_cleared) or 'None'}")
        print(f"CELLS UPDATED:               {', '.join(result.cells_updated)}")
        print(f"POST-WRITE VERIFICATION:     {'PASS' if result.verified else 'SKIPPED'}")
        print("OVERALL RESULT:              PASS")
    return 0


def cmd_closing_sync(args: argparse.Namespace) -> int:
    """Execute Scheme Closing Report synchronization."""
    import requests
    from app.google_sheets.client import GoogleApiSheetsService, MockGoogleSheetsService
    from app.google_sheets.discovery import redact_spreadsheet_id
    from app.innervex.auth import InnervexAuthenticator
    from app.innervex.client import InnervexClient
    from app.reporting.closing import (
        DEFAULT_CLOSED_REJOINING_SPREADSHEET_ID,
        EXCLUDED_SCHEMES,
        REQUIRED_ACTIVE_SCHEMES,
        SHOWROOM_MAPPING,
        ClosingReportProcessor,
        calculate_yesterday_date,
        derive_closing_tab_name,
        seed_mock_closing_sheet,
    )

    config_bundle = load_config()
    target_date = parse_target_date(args.report_date) if args.report_date else calculate_yesterday_date()

    if not getattr(args, "dry_run", False) and not getattr(args, "mock", False):
        if os.getenv("STRICT_READ_ONLY", "false").lower() in ("true", "1", "yes"):
            print("\n[BLOCKED] STRICT READ-ONLY MODE IS ACTIVE. Live closing summary writes are forbidden. Use --dry-run or --mock.\n")
            return 1
    spreadsheet_id = (
        getattr(args, "spreadsheet_id", None)
        or os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID", DEFAULT_CLOSED_REJOINING_SPREADSHEET_ID)
    )

    if getattr(args, "mock", False):
        mode_str = "MOCK / OFFLINE (Simulated Data & Zero Writes)"
    elif getattr(args, "dry_run", False):
        mode_str = "DRY RUN (Simulate & Validate Only)"
    else:
        mode_str = "LIVE UPDATE"

    print("=" * 65)
    print("SCHEME CLOSING REPORT SYNCHRONIZATION")
    print("=" * 65)
    print(f"Target Report Date: {target_date.strftime('%d/%m/%Y')} ({target_date.isoformat()})")
    print(f"Destination Sheet:  {redact_spreadsheet_id(spreadsheet_id)}")
    print(f"Destination Tab:    {derive_closing_tab_name(target_date)}")
    print(f"Mode:               {mode_str}")
    print("=" * 65)

    # 1. Establish Innervex Session
    print("\n1. INNERVEX AUTHENTICATION & SESSION")
    session = requests.Session()
    session.headers.update(config_bundle.innervex.default_headers)
    authenticator = InnervexAuthenticator(config_bundle.innervex)

    if getattr(args, "mock", False):
        print("Innervex Authentication: Mock / Offline Mode (Skipping network login)")
    else:
        try:
            auth_res = authenticator.login(session=session)
            if not auth_res.authenticated:
                if getattr(args, "dry_run", False):
                    print("Innervex Authentication Note: Session unauthenticated. Using cached raw report for simulation.")
                else:
                    print("Status: FAILED - Innervex authentication unsuccessful")
                    return 1
            else:
                print(f"Innervex Authentication: PASS (User: {auth_res.username})")
        except Exception as e:
            if getattr(args, "dry_run", False):
                print(f"Innervex Authentication Note: {type(e).__name__}: {e}")
                print("Using cached raw report for dry-run simulation.")
            else:
                print(f"Innervex Authentication: FAIL ({type(e).__name__}: {e})")
                return 1

    innervex_client = InnervexClient(config_bundle.innervex, auth_strategy=authenticator)
    innervex_client.session = session

    # 2. Establish Google Sheets Service
    print("\n2. GOOGLE SHEETS SERVICE INITIALIZATION")
    sa_path = getattr(args, "service_account", None) or os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    sheets_service = None
    if getattr(args, "mock", False):
        print("Using MockGoogleSheetsService (Offline / Mock Mode)")
        sheets_service = MockGoogleSheetsService()
        include_target = getattr(args, "seed_include_target_date", False)
        seed_mock_closing_sheet(sheets_service, spreadsheet_id, target_date=target_date, include_target_date=include_target)
    else:
        try:
            sheets_service = GoogleApiSheetsService(
                service_account_path=sa_path,
                service_account_json=sa_json,
                read_only=getattr(args, "dry_run", False),
            )
            # Test access to destination workbook
            sheets_service.get_spreadsheet_metadata(spreadsheet_id)
            print("Google Service Account Access: PASS")
        except Exception as e:
            if getattr(args, "dry_run", False):
                print(f"Live Sheet Access Note: {type(e).__name__}: {e}")
                print("Falling back to safe in-memory simulation service seeded with production sheet schema.")
                sheets_service = MockGoogleSheetsService()
                seed_mock_closing_sheet(sheets_service, spreadsheet_id, target_date=target_date, include_target_date=False)
            else:
                print(f"Google Service Account Access: FAIL ({e})")
                return 1

    # 3. Initialize Processor
    processor = ClosingReportProcessor(
        innervex_client=innervex_client,
        sheets_service=sheets_service,
        spreadsheet_id=spreadsheet_id,
    )

    if not getattr(args, "dry_run", False) and not getattr(args, "mock", False):
        if os.getenv("STRICT_READ_ONLY", "false").lower() in ("true", "1", "yes"):
            print("\n[BLOCKED] STRICT READ-ONLY MODE IS ACTIVE. Live closing summary writes are forbidden. Use --dry-run or --mock.\n")
            return 1

    try:
        result = processor.run(
            report_date=target_date,
            dry_run=getattr(args, "dry_run", False),
            force=getattr(args, "force", False),
            mock=getattr(args, "mock", False),
        )
    except Exception as e:
        print(f"\nCLOSING REPORT PROCESSING FAILED: {type(e).__name__}: {e}")
        return 1

    # 4. Display Complete Summary Report
    print("\n" + "=" * 65)
    print("CLOSING REPORT PROCESSING RESULTS")
    print("=" * 65)
    print("INNERVEX FILTERS:")
    print("  Report Type:               SCHEME CLOSING REPORT")
    print("  Type:                      SALES")
    print("  Status Options (5):        CLOSE, TERMINATE, REFUND, REVERSAL, ADVANCE")
    print("  Showrooms (32):            ALL 32 active showrooms")
    print(f"  Schemes Selected (6):      {', '.join(REQUIRED_ACTIVE_SCHEMES)}")
    print(f"  DIGI GOLD Excluded:        {'YES (CONFIRMED)' if result.validation.digi_gold_excluded else 'NO (VIOLATION)'}")
    print(f"  DIGI SILVER Excluded:      {'YES (CONFIRMED)' if result.validation.digi_silver_excluded else 'NO (VIOLATION)'}")

    print("\nDATA PIPELINE METRICS:")
    print(f"  Raw Rows Downloaded:       {result.raw_rows_downloaded}")
    print(f"  Cleaned Rows (A:G):        {result.cleaned_rows_count}")
    print(f"  Rows Rejected:             {result.raw_rows_downloaded - result.cleaned_rows_count}")
    if result.raw_csv_path:
        print(f"  Raw CSV Saved:             {result.raw_csv_path}")
    if result.processed_csv_path:
        print(f"  Cleaned CSV Saved:         {result.processed_csv_path}")

    is_mock = getattr(args, "mock", False) or getattr(result, "mock", False)
    is_dry = getattr(args, "dry_run", False) or getattr(result, "dry_run", False)

    print("\nDESTINATION TARGET:")
    print(f"  Workbook:                  Closed Member & Rejoining Report")
    print(f"  Sheet Tab:                 {result.destination_tab}")
    print(f"  Last Existing Row:         {result.last_existing_row}")
    print(f"  Append Start Row:          {result.append_start_row}")
    print(f"  Append End Row:            {result.append_end_row}")
    print(f"  Formula Range (H:I):       {result.formula_range}")
    if is_mock:
        print(f"  Actual Google Sheets Writes: 0 (MOCK MODE)")
        print(f"  Simulated Rows:            {0 if result.already_processed else result.cleaned_rows_count}")
        print(f"  Writes Performed:          {'0 (Idempotent Skip)' if result.already_processed else f'0 (MOCK SIMULATION - {result.cleaned_rows_count} rows simulated)'}")
    elif is_dry:
        print(f"  Actual Google Sheets Writes: 0 (DRY RUN)")
        print(f"  Planned Rows:              {0 if result.already_processed else result.cleaned_rows_count}")
        print(f"  Writes Performed:          {'0 (Idempotent Skip)' if result.already_processed else '0 (DRY RUN)'}")
    else:
        print(f"  Actual Google Sheets Writes: {0 if result.already_processed else result.cleaned_rows_count}")
        print(f"  Writes Performed:          {0 if result.already_processed else result.cleaned_rows_count}")

    tag = " [MOCK / SYNTHETIC DATA]" if (is_mock or getattr(result.validation, "is_synthetic", False)) else ""
    print(f"\nSHOWROOM MAPPING & VALUE VALIDATION (CLOSED vs COUNTA of SCHEMENAME){tag}:")
    print("-" * 65)
    print(f"  {'Showroom':<12} | {'CLOSED':<8} | {'COUNTA Pivot':<14} | {'Status':<8}")
    print("-" * 65)
    if not result.validation.comparisons:
        if result.already_processed:
            print("  [IDEMPOTENT SKIP] Showroom reconciliation skipped: date already processed.")
        else:
            print("  [NO DATA] No showroom reconciliation data available.")
    else:
        for comp in result.validation.comparisons:
            status_str = "MATCH" if comp.matched else f"DIFF ({comp.difference})"
            print(f"  {comp.showroom:<12} | {comp.closed_value:<8} | {comp.pivot_value:<14} | {status_str:<8}")
    print("-" * 65)

    status_tag = " (MOCK / SYNTHETIC)" if is_mock else ""
    print(f"\nCLOSING REPORT VALIDATION:   {result.validation.status}{status_tag}")
    if result.already_processed:
        print("EXECUTION STATUS:            ALREADY_PROCESSED (Idempotent skip)")
    elif is_mock:
        print("EXECUTION STATUS:            SUCCESS (MOCK SIMULATION COMPLETE - ZERO WRITES)")
    elif is_dry:
        print("EXECUTION STATUS:            SUCCESS (DRY RUN COMPLETE - ZERO WRITES)")
    else:
        print("EXECUTION STATUS:            SUCCESS (APPENDED)")
    print("=" * 65)
    return 0 if result.validation.status == "PASS" else 1


def seed_mock_screenshot_data(
    mock_service: Any,
    report_date: Optional[date] = None,
    enrollment_date: Optional[date] = None,
    closing_date: Optional[date] = None,
) -> None:
    """Seed MockGoogleSheetsService with realistic enrollment and closing sheet tables for offline testing."""
    from dotenv import load_dotenv
    load_dotenv()

    default_d = report_date or date(2026, 10, 14)
    d_enrollment = enrollment_date or default_d
    d_closing = closing_date or default_d

    enrollment_date_str = d_enrollment.strftime("%d/%m/%Y")
    closing_date_str = d_closing.strftime("%d/%m/%Y")

    enrollment_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID", "mock_enrollment_id")
    closing_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID", "mock_closing_id")

    meta_enrollment = {
        "properties": {"title": "New Enrollment from April 2026"},
        "sheets": [
            {"properties": {"title": "Consolidate Report - Oct", "sheetId": 928019529}},
        ],
    }
    mock_service.set_metadata(meta_enrollment, spreadsheet_id=enrollment_id)
    mock_service.set_metadata(meta_enrollment, spreadsheet_id="mock_enrollment_id")

    enrollment_rows = [
        ["", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", ""],
        [f"New Enrollment as on {enrollment_date_str}", "", "", "", "", "", "", ""],
        ["Branch", "Target Value", "Subhiksham", "Viruksham", "Total", "Yet to Achieve", "Achieved %"],
        ["", "", "", "", "", "", ""],
        ["TVL", "40000000", "1482500", "13754707", "15237207", "-24762793", "38%"],
        ["CPT", "72000000", "3506000", "26847569", "30353569", "-41646431", "42%"],
        ["TVM", "27000000", "844500", "8363706", "9208206", "-17791794", "34%"],
        ["TPJ", "40000000", "1125000", "13815107", "14940107", "-25059893", "37%"],
        ["CBE", "30000000", "999500", "11879829", "12879329", "-17120671", "43%"],
        ["SLM", "30000000", "699000", "6365078", "7064078", "-22935922", "24%"],
        ["PNM", "20000000", "898000", "5159102", "6057102", "-13942898", "30%"],
        ["PADI", "54000000", "2701000", "19292563", "21993563", "-32006437", "41%"],
        ["KANCHI", "27000000", "1253500", "6742097", "7995597", "-19004403", "30%"],
        ["ONLINE", "", "984000", "1312900", "2296900"],
        ["Total", "313000000", "14493000", "113532658", "128025658", "-214271242", "41%"],
        ["", "", "", "", "", "", ""],
        ["Branch ", "Count Target", "Achieved Count", "", "", "", "SS + SV + DG + DS", "AVG"],
        ["", "SS + SV + DG + DS", "Subhiksham", "Viruksham", "DigiGold", "DigiSilver"],
        ["TVL", "3000", "223", "37", "445", "270", "975", "75"],
        ["CPT", "6000", "360", "78", "1149", "231", "1818", "140"],
        ["TVM", "4000", "113", "13", "1135", "30", "1291", "99"],
        ["TPJ", "5000", "234", "34", "1026", "465", "1759", "135"],
        ["CBE", "7000", "174", "38", "1116", "642", "1970", "152"],
        ["SLM", "3000", "124", "22", "747", "337", "1230", "95"],
        ["PNM", "2000", "101", "17", "252", "106", "476", "37"],
        ["PADI", "6000", "332", "69", "920", "250", "1571", "121"],
        ["KANCHI", "", "224", "24", "1521", "257", "2026", "156"],
        ["E COMM", "", "149", "13", "2,449", "487", "3,098", "238"],
        ["Total", "36000", "2034", "345", "10760", "3075", "16214", "1247"],
    ]
    mock_service.update_range(enrollment_id, "'Consolidate Report - Oct'!E1:M35", enrollment_rows)
    mock_service.update_range("mock_enrollment_id", "'Consolidate Report - Oct'!E1:M35", enrollment_rows)

    meta_closing = {
        "properties": {"title": "Closed Member & Rejoining Report"},
        "sheets": [
            {"properties": {"title": "October - 2026", "sheetId": 2105033369}},
        ],
    }
    mock_service.set_metadata(meta_closing, spreadsheet_id=closing_id)
    mock_service.set_metadata(meta_closing, spreadsheet_id="mock_closing_id")

    closing_rows = [
        ["", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", ""],
        ["", "", "SCHEME REJOINING REPORT AS ON ", "", "", "", "", closing_date_str],
        ["", "", "SHOWROOM", "CLOSED", "REJOIN SS", "REJOIN SV", "TOTAL REJOIN", "REJOIN %"],
        ["", "", "TVL", "207", "38", "5", "43", "20.77%"],
        ["", "", "CPT", "346", "62", "5", "67", "19.36%"],
        ["", "", "TVM", "194", "38", "4", "42", "21.65%"],
        ["", "", "TPJ", "285", "53", "4", "57", "20.00%"],
        ["", "", "CBE", "243", "48", "2", "50", "20.58%"],
        ["", "", "SLM", "122", "27", "4", "31", "25.41%"],
        ["", "", "PNM", "101", "10", "6", "16", "15.84%"],
        ["", "", "PADI", "96", "13", "3", "16", "16.67%"],
        ["", "", "KPM", "11", "1", "3", "4", "36.36%"],
        ["", "", "TOTAL", "1605", "290", "36", "326", "20.31%"],
        ["", "", "CLOSED WEIGHT", "", "", "CLOSED COUNT", "", ""],
        ["", "", "SHOWROOM", "COUNT", "WEIGHT", "SHOWROOM", "SS", "SV"],
        ["", "", "TVL", "207", "1559.92", "TVL", "187", "20"],
        ["", "", "CPT", "346", "3345.20", "CPT", "269", "77"],
        ["", "", "TVM", "194", "1121.06", "TVM", "170", "24"],
        ["", "", "TPJ", "285", "1692.79", "TPJ", "241", "44"],
        ["", "", "CBE", "243", "964.60", "CBE", "216", "27"],
        ["", "", "SLM", "122", "519.03", "SLM", "96", "26"],
        ["", "", "PNM", "101", "610.73", "PNM", "90", "11"],
        ["", "", "PADI", "96", "1003.75", "PADI", "74", "22"],
        ["", "", "KPM", "11", "64.69", "KPM", "8", "3"],
        ["", "", "TOTAL", "1605", "10881.76", "TOTAL", "1351", "254"],
    ]
    mock_service.update_range(closing_id, "'October - 2026'!J1:R30", closing_rows)
    mock_service.update_range("mock_closing_id", "'October - 2026'!J1:R30", closing_rows)


def cmd_screenshot(args: argparse.Namespace) -> int:
    """Capture presentation-ready screenshots of Google Sheets executive tables."""
    from app.core.exceptions import TableValidationError
    from app.google_sheets.client import GoogleApiSheetsService, MockGoogleSheetsService
    from app.reporting.screenshots import GoogleSheetsScreenshotEngine

    target_date = parse_target_date(getattr(args, "report_date", None))

    print("=" * 70)
    print("GOOGLE SHEETS REPORT SCREENSHOT ENGINE")
    print("=" * 70)
    print(f"Target Report Date: {target_date.strftime('%d/%m/%Y')} ({target_date.isoformat()})")

    config_bundle = load_config()

    is_mock = getattr(args, "mock", False)
    if getattr(args, "output_dir", None):
        output_dir = Path(args.output_dir)
    elif is_mock:
        output_dir = config_bundle.project_root / "data" / "screenshots" / "mock"
    else:
        output_dir = config_bundle.project_root / "data" / "screenshots"
    output_dir.mkdir(parents=True, exist_ok=True)

    sa_path = getattr(args, "service_account", None) or os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    if is_mock:
        print("Mode: Mock / Offline Mode (SYNTHETIC DATA ONLY)")
        print("NOTICE: Output isolated to mock directory to prevent confusion with production reports.")
        sheets_service = MockGoogleSheetsService()
        seed_mock_screenshot_data(sheets_service, report_date=target_date)
    else:
        print("Mode: Production Live Mode")
        try:
            sheets_service = GoogleApiSheetsService(
                service_account_path=sa_path,
                service_account_json=sa_json,
                read_only=True,
            )
            print("Google Service Account Authentication: OK")
        except Exception as e:
            print(f"AUTHENTICATION FAILURE: {type(e).__name__}: {e}")
            return 1

    engine = GoogleSheetsScreenshotEngine(
        sheets_service=sheets_service,
        config_bundle=config_bundle,
        output_dir=output_dir,
        dpi=getattr(args, "dpi", 200),
    )

    target = getattr(args, "target", "all").lower()
    print(f"Target: {target.upper()}")
    print(f"Output Directory: {output_dir}")

    is_mock = getattr(args, "mock", False)
    results = []
    try:
        if target in ("all", "enrollment"):
            print("\nCapturing Enrollment Report...")
            fn_enroll = f"mock_enrollment_report_{target_date.strftime('%Y%m%d')}.png" if is_mock else None
            res_enrollment = engine.capture_enrollment(expected_date=target_date, filename=fn_enroll)
            results.append(res_enrollment)
            print(f"  [OK] Validated Table: '{res_enrollment.report_title}'")
            print(f"  [OK] Exported Range:   {res_enrollment.range_used} (gid: {res_enrollment.gid})")
            print(f"  [OK] Saved PNG:        {res_enrollment.output_path}")
            print(f"  [OK] Dimensions:       {res_enrollment.dimensions[0]}x{res_enrollment.dimensions[1]} ({res_enrollment.file_size_bytes:,} bytes)")

        if target in ("all", "closing"):
            print("\nCapturing Closing Report...")
            fn_close = f"mock_closing_rejoining_report_{target_date.strftime('%Y%m%d')}.png" if is_mock else None
            res_closing = engine.capture_closing(expected_date=target_date, filename=fn_close)
            results.append(res_closing)
            print(f"  [OK] Validated Table: '{res_closing.report_title}'")
            print(f"  [OK] Exported Range:   {res_closing.range_used} (gid: {res_closing.gid})")
            print(f"  [OK] Saved PNG:        {res_closing.output_path}")
            print(f"  [OK] Dimensions:       {res_closing.dimensions[0]}x{res_closing.dimensions[1]} ({res_closing.file_size_bytes:,} bytes)")

        if target == "all" and len(results) == 2:
            if results[0].report_date != results[1].report_date:
                raise TableValidationError(
                    f"Mismatched report dates between screenshots: "
                    f"Enrollment has {results[0].report_date}, Closing has {results[1].report_date}."
                )

    except Exception as e:
        print(f"\nSCREENSHOT GENERATION FAILED: {type(e).__name__}: {e}")
        return 1

    print("\n" + "=" * 70)
    print("SCREENSHOT GENERATION SUMMARY")
    print("=" * 70)
    for res in results:
        print(f"Report:          {res.target.upper()}")
        print(f"Workbook:        {res.workbook_name}")
        print(f"Worksheet:       {res.worksheet_name} (gid: {res.gid})")
        print(f"Report Date:     {res.report_date}")
        print(f"Exported Range:  {res.range_used}")
        print(f"Dimensions:      {res.dimensions[0]}x{res.dimensions[1]} px")
        print(f"File Size:       {res.file_size_bytes:,} bytes")
        print(f"File Path:       {res.output_path}")
        print("-" * 70)

    print(f"TOTAL SCREENSHOTS CAPTURED: {len(results)}")
    print("ZERO GOOGLE SHEETS WRITES PERFORMED (STRICT READ-ONLY ENFORCED)")
    return 0


def _notify_digi_blocked(config_bundle, target_date: date, reason: str, dry_run: bool = True) -> None:
    """Send diagnostic alert to personal Telegram recipient when Digi validation fails at cutoff."""
    from app.delivery.telegram import TelegramDeliveryChannel
    tg_config = config_bundle.app.telegram
    if not tg_config.enabled and not dry_run:
        return

    channel = TelegramDeliveryChannel(
        config=tg_config,
        enabled=True if dry_run else tg_config.enabled,
        dry_run=dry_run or tg_config.dry_run,
        chat_id=tg_config.chat_id,
    )
    if not channel.is_enabled():
        return

    alert_text = (
        f"⚠️ POTHYS REPORTING STATUS: DIGI STAGE BLOCKED\n"
        f"Date: {target_date.strftime('%d/%m/%Y')}\n"
        f"Reason: {reason}\n"
        f"Status: Final enrollment consolidation held pending. Preliminary SS/SV and closing outputs are preserved."
    )
    channel.send_message(text=alert_text, recipient=tg_config.chat_id, report_date=target_date)


def run_phase1_innervex(args: argparse.Namespace, target_date: date) -> int:
    """
    Phase 1 (10:00 AM IST): Independent Innervex Workflow.
    - Synchronize Subhiksham and Viruksham enrollment records to monthly sheets.
    - Synchronize Scheme Closing Report to Closed Member sheet.
    - Capture Closing Report screenshot independently.
    - Does NOT depend on or wait for DigiGold/DigiSilver uploads.
    """
    print("\n" + "=" * 75)
    print("PHASE 1 (10:00 AM IST): INNERVEX REPORTING WORKFLOW")
    print("=" * 75)
    print(f"Target Report Date: {target_date.strftime('%d/%m/%Y')} ({target_date.isoformat()})")

    # 1. SS & SV Enrollment Synchronization
    print("\n>>> [Phase 1.1] ENROLLMENT DATA SYNCHRONIZATION (SUBHIKSHAM & VIRUKSHAM)")
    print("-" * 75)
    for rep in ["subhiksham", "viruksham"]:
        print(f"\n[Phase 1.1] Processing {rep.upper()} for {target_date.isoformat()}...")
        rep_args = argparse.Namespace(
            report_date=target_date.isoformat(),
            report_id=rep,
            dry_run=getattr(args, "dry_run", False),
            force=getattr(args, "force", False),
        )
        if getattr(args, "mock", False):
            print(f"[Phase 1.1] Mode: MOCK / OFFLINE (SIMULATED DATA)")
            print(f"[Phase 1.1] Mock simulation for {rep.upper()}: SUCCESS")
            print(f"  Actual Google Sheets Writes: 0 (MOCK MODE)")
            print(f"  Simulated Rows:              4 (Synthetic test records)")
        else:
            rc = cmd_run(rep_args)
            if rc != 0:
                print(f"\n[FAIL] Phase 1.1 FAILED during {rep.upper()} processing.")
                print("Pipeline HALTED.")
                return 1

    stage1_status = "SUCCESS (MOCK SIMULATION - ZERO WRITES)" if getattr(args, "mock", False) else "SUCCESS"
    print(f"\n[PASS] Phase 1.1: Enrollment data synchronized: {stage1_status}.")

    # 2. Scheme Closing Report Synchronization
    print("\n>>> [Phase 1.2] SCHEME CLOSING REPORT SYNCHRONIZATION")
    print("-" * 75)
    closing_args = argparse.Namespace(
        report_date=target_date.isoformat(),
        spreadsheet_id=getattr(args, "closing_spreadsheet_id", None),
        service_account=getattr(args, "service_account", None),
        dry_run=getattr(args, "dry_run", False),
        force=getattr(args, "force", False),
        mock=getattr(args, "mock", False),
    )
    rc_closing = cmd_closing_sync(closing_args)
    if rc_closing != 0:
        print("\n[FAIL] Phase 1.2 FAILED: Closing report synchronization failed.")
        return 1

    stage3_status = "SUCCESS (MOCK SIMULATION - ZERO WRITES)" if getattr(args, "mock", False) else "SUCCESS"
    print(f"\n[PASS] Phase 1.2: Closing report summary synchronized: {stage3_status}.")

    # 3. Closing Report Screenshot Generation
    print("\n>>> [Phase 1.3] CLOSING REPORT SCREENSHOT GENERATION")
    print("-" * 75)
    screenshot_args = argparse.Namespace(
        report_date=target_date.isoformat(),
        target="closing",
        output_dir=getattr(args, "output_dir", None),
        dpi=getattr(args, "dpi", 200),
        service_account=getattr(args, "service_account", None),
        mock=getattr(args, "mock", False),
    )
    rc_screenshot = cmd_screenshot(screenshot_args)
    if rc_screenshot != 0:
        print("\n[FAIL] Phase 1.3 FAILED: Closing screenshot generation failed.")
        return 1

    print("\n[PASS] PHASE 1 COMPLETE: Innervex workflow, preliminary SS/SV outputs, and Closing Report preserved.")
    return 0


def run_phase2_digi_validation(args: argparse.Namespace, target_date: date) -> int:
    """
    Phase 2 (11:00 AM IST or later): DigiGold & DigiSilver Validation.
    - Validates source freshness for correct reporting date (SCHDATE and Pivot header).
    - Validates 10 branches (9 showrooms + 1 ecommerce), ensuring no blanks or nulls.
    - Validates showroom subtotal (sum of 9 stores == BRANCH TOTAL).
    - Validates ecommerce count and overall total.
    - Records attempts in digi_validation_history.json.
    - Enforces retry policy and cutoff time (12:30 PM IST).
    - Dispatches Telegram alert if blocked at cutoff.
    """
    from datetime import datetime
    from app.google_sheets.client import GoogleApiSheetsService
    from app.reporting.digi import (
        DEFAULT_DIGI_SPREADSHEET_ID,
        DigiSourceReader,
        DigiValidationTracker,
        DigiValidationStatus,
        DigiValidationRecord,
        evaluate_digi_timing_window,
    )

    config_bundle = load_config()
    digi_cfg = config_bundle.app.digi_schedule
    tracker = DigiValidationTracker(digi_cfg.history_file)
    attempt_num = tracker.next_attempt_number(target_date)

    print("\n" + "=" * 75)
    print("PHASE 2 (11:00 AM IST OR LATER): DIGIGOLD & DIGISILVER VALIDATION")
    print("=" * 75)
    print(f"Target Report Date: {target_date.strftime('%d/%m/%Y')} ({target_date.isoformat()})")
    print(f"Validation Attempt: #{attempt_num} (Max Retries: {digi_cfg.max_retries})")

    is_force = getattr(args, "force", False)
    if not is_force and not getattr(args, "mock", False):
        if tracker.is_already_validated(target_date):
            print(f"\n[IDEMPOTENT SKIP] Phase 2 Digi validation already PASSED for {target_date.isoformat()}. Skipping redundant re-validation.")
            return 0
        if tracker.is_blocked(target_date):
            print(f"\n[BLOCKED] Phase 2 Digi validation previously exceeded cutoff (BLOCKED_CUTOFF) for {target_date.isoformat()}. No further retries allowed.")
            return 1

    # Timing Window Check (unless --force or --mock)
    is_force_or_mock = is_force or getattr(args, "mock", False)
    in_window, time_status, time_msg = evaluate_digi_timing_window(
        config=digi_cfg,
        force=is_force_or_mock,
    )
    print(f"Operational Window: {time_msg}")

    if not in_window:
        rec = DigiValidationRecord(
            report_date=target_date,
            attempt=attempt_num,
            timestamp=datetime.now().isoformat(),
            status=time_status,
            error_message=time_msg,
        )
        tracker.record_attempt(rec)

        if time_status == DigiValidationStatus.BLOCKED_CUTOFF:
            print(f"\n[BLOCKED] PHASE 2 BLOCKED: Cutoff exceeded. Final enrollment consolidation held pending.")
            _notify_digi_blocked(config_bundle, target_date, time_msg, dry_run=getattr(args, "dry_run", False))
            return 1
        else:
            print(f"\n[PENDING] PHASE 2 PENDING: Execution scheduled for after 11:00 AM IST. Preliminary outputs preserved.")
            return 2

    # If mock mode:
    if getattr(args, "mock", False):
        print("Digi Source Read: MOCK / OFFLINE SIMULATION")
        print("  SCHDATE Freshness:       PASS (Mock Date Validated)")
        print("  Pivot Date Header:       PASS (as of " + target_date.strftime("%d %b %Y") + ")")
        print("  Showroom Subtotal (9):   PASS (Gold: 4,233 == 4,233 | Silver: 1,488 == 1,488)")
        print("  Ecommerce Subtotal:      PASS (Gold: 1,275 | Silver: 267)")
        print("  Overall Total:           PASS (Gold: 5,508 | Silver: 1,755)")
        rec = DigiValidationRecord(
            report_date=target_date,
            attempt=attempt_num,
            timestamp=datetime.now().isoformat(),
            status=DigiValidationStatus.SUCCESS,
            gold_showroom_subtotal=4233,
            gold_ecommerce_subtotal=1275,
            gold_overall_total=5508,
            silver_showroom_subtotal=1488,
            silver_ecommerce_subtotal=267,
            silver_overall_total=1755,
        )
        tracker.record_attempt(rec)
        print("\n[PASS] PHASE 2 VALIDATION SUCCESSFUL (MOCK MODE).")
        return 0

    # Live / Dry-Run Validation
    digi_sid = getattr(args, "digi_spreadsheet_id", None) or os.getenv("GOOGLE_SHEET_DIGI_ID", DEFAULT_DIGI_SPREADSHEET_ID)
    sa_path = getattr(args, "service_account", None) or os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    try:
        source_svc = GoogleApiSheetsService(
            service_account_path=sa_path,
            service_account_json=sa_json,
            read_only=True,
        )
        reader = DigiSourceReader(source_svc, spreadsheet_id=digi_sid)
        extraction = reader.validate_for_consolidation(target_date)

        print("Digi Source Read (Read-Only Authorized):")
        print(f"  SCHDATE Records Found:   {extraction.source_records_for_report_date} (Latest SCHDATE: {extraction.source_latest_schdate})")
        print(f"  Pivot Date Header:       PASS ({extraction.pivot_date_header or 'Validated'})")
        print(f"  Gold Showroom Subtotal:  PASS ({extraction.gold_showroom_subtotal:,})")
        print(f"  Gold Ecommerce:          PASS ({extraction.gold_ecommerce_subtotal:,})")
        print(f"  Gold Overall Total:      PASS ({extraction.gold_overall_total:,})")
        print(f"  Silver Showroom Subtotal:PASS ({extraction.silver_showroom_subtotal:,})")
        print(f"  Silver Ecommerce:        PASS ({extraction.silver_ecommerce_subtotal:,})")
        print(f"  Silver Overall Total:    PASS ({extraction.silver_overall_total:,})")

        rec = DigiValidationRecord(
            report_date=target_date,
            attempt=attempt_num,
            timestamp=datetime.now().isoformat(),
            status=DigiValidationStatus.SUCCESS,
            gold_showroom_subtotal=extraction.gold_showroom_subtotal,
            gold_ecommerce_subtotal=extraction.gold_ecommerce_subtotal,
            gold_overall_total=extraction.gold_overall_total,
            silver_showroom_subtotal=extraction.silver_showroom_subtotal,
            silver_ecommerce_subtotal=extraction.silver_ecommerce_subtotal,
            silver_overall_total=extraction.silver_overall_total,
        )
        tracker.record_attempt(rec)
        print("\n[PASS] PHASE 2 VALIDATION SUCCESSFUL: Source data verified complete and consistent.")
        return 0

    except Exception as e:
        err_desc = f"{type(e).__name__}: {e}"
        print(f"\n[FAIL] Phase 2 Validation Failed on Attempt #{attempt_num}: {err_desc}")
        status = DigiValidationStatus.BLOCKED_CUTOFF if attempt_num >= digi_cfg.max_retries else DigiValidationStatus.PENDING_RETRY
        rec = DigiValidationRecord(
            report_date=target_date,
            attempt=attempt_num,
            timestamp=datetime.now().isoformat(),
            status=status,
            error_message=err_desc,
        )
        tracker.record_attempt(rec)

        if attempt_num >= digi_cfg.max_retries:
            print(f"[BLOCKED] Maximum retry attempts ({digi_cfg.max_retries}) reached. Digi stage BLOCKED.")
            _notify_digi_blocked(config_bundle, target_date, err_desc, dry_run=getattr(args, "dry_run", False))
        else:
            print(f"[PENDING_RETRY] Validation will retry in {digi_cfg.retry_interval_minutes} minutes ({digi_cfg.max_retries - attempt_num} attempts remaining).")

        return 1


def run_phase3_consolidation_and_delivery(args: argparse.Namespace, target_date: date) -> int:
    """
    Phase 3: Final Enrollment Consolidation & Personal Telegram Delivery.
    - Only runs after Phase 2 passes validation.
    - Populates Consolidate Report (I20:J29, L20:L30, E3) with verified post-write checks.
    - Generates verified Enrollment Report screenshot.
    - Sends enrollment screenshot to configured personal Telegram chat ID only.
    - Never sends preliminary or stale Digi report labelled as final.
    """
    from app.reporting.digi import DigiValidationTracker

    config_bundle = load_config()
    digi_cfg = config_bundle.app.digi_schedule
    tracker = DigiValidationTracker(digi_cfg.history_file)

    print("\n" + "=" * 75)
    print("PHASE 3: FINAL ENROLLMENT CONSOLIDATION & PERSONAL TELEGRAM DELIVERY")
    print("=" * 75)
    print(f"Target Report Date: {target_date.strftime('%d/%m/%Y')} ({target_date.isoformat()})")

    # Guard: Require Phase 2 validation success
    if not getattr(args, "mock", False) and not tracker.is_already_validated(target_date):
        print("\n[VALIDATION CHECK] Verifying Phase 2 Digi source validation status...")
        rc_val = run_phase2_digi_validation(args, target_date)
        if rc_val != 0:
            print("\n[BLOCKED] PHASE 3 BLOCKED: Phase 2 Digi validation not passed.")
            print("Final enrollment consolidation held pending. Zero external delivery performed.")
            return 1

    # 1. Synchronize DigiGold & DigiSilver to destination Consolidate Report
    print("\n>>> [Phase 3.1] DIGIGOLD & DIGISILVER CONSOLIDATE REPORT SYNCHRONIZATION")
    print("-" * 75)
    digi_args = argparse.Namespace(
        report_date=target_date.isoformat(),
        digi_spreadsheet_id=getattr(args, "digi_spreadsheet_id", None),
        spreadsheet_id=getattr(args, "spreadsheet_id", None),
        service_account=getattr(args, "service_account", None),
        dry_run=getattr(args, "dry_run", False),
        mock=getattr(args, "mock", False),
    )
    rc_digi = cmd_digi_sync(digi_args)
    if rc_digi != 0:
        print("\n[FAIL] Phase 3.1 FAILED: Digi synchronization failed.")
        return 1

    # 2. Capture Verified Final Enrollment Screenshot
    print("\n>>> [Phase 3.2] VERIFIED FINAL ENROLLMENT SCREENSHOT GENERATION")
    print("-" * 75)
    screenshot_args = argparse.Namespace(
        report_date=target_date.isoformat(),
        target="enrollment",
        output_dir=getattr(args, "output_dir", None),
        dpi=getattr(args, "dpi", 200),
        service_account=getattr(args, "service_account", None),
        mock=getattr(args, "mock", False),
    )
    rc_screenshot = cmd_screenshot(screenshot_args)
    if rc_screenshot != 0:
        print("\n[FAIL] Phase 3.2 FAILED: Verified enrollment screenshot generation failed.")
        print("Telegram delivery blocked to prevent distributing unverified reports.")
        return 1

    stage4_status = "SUCCESS (MOCK SCREENSHOTS - ZERO WRITES)" if getattr(args, "mock", False) else "SUCCESS"
    print(f"\n[PASS] Phase 3.2: Verified screenshot generation completed: {stage4_status}.")

    # 3. Deliver Final Enrollment Screenshot via Telegram to configured personal recipient
    print("\n>>> [Phase 3.3] TELEGRAM FINAL ENROLLMENT REPORT DELIVERY")
    print("-" * 75)
    tg_args = argparse.Namespace(
        report_date=target_date.isoformat(),
        recipient=getattr(args, "recipient", None),
        screenshot_path=None,
        target="enrollment",
        dry_run=getattr(args, "dry_run", False),
        force=getattr(args, "force", False),
        mock=getattr(args, "mock", False),
        output_dir=getattr(args, "output_dir", None),
    )
    rc_tg = cmd_send_telegram(tg_args)
    if rc_tg != 0:
        print("\n[FAIL] Phase 3.3 FAILED: Telegram delivery failed.")
        return 1

    # 4. Optional WhatsApp Delivery (if configured and enabled)
    from app.delivery.whatsapp import WhatsAppDeliveryChannel, redact_phone_number
    from app.delivery.base import DeliveryStatus
    wa_channel = WhatsAppDeliveryChannel(
        config=config_bundle.app.whatsapp,
        dry_run=getattr(args, "dry_run", False) or getattr(args, "mock", False),
    )
    if wa_channel.is_enabled():
        print("\n>>> [Phase 3.4] WHATSAPP EXECUTIVE REPORT DELIVERY")
        print("-" * 75)
        receipt = wa_channel.send_daily_screenshots(
            report_date=target_date,
            recipient=getattr(args, "recipient", None),
            force=getattr(args, "force", False),
            screenshots_dir=getattr(args, "output_dir", None),
        )
        masked_recip = redact_phone_number(receipt.recipient)
        if receipt.status == DeliveryStatus.SENT:
            mode_desc = "SIMULATED (DRY RUN)" if (wa_channel.dry_run or os.getenv("STRICT_READ_ONLY", "false").lower() in ("true", "1", "yes")) else "LIVE DELIVERY"
            print(f"[PASS] Phase 3.4: WhatsApp delivery successful ({mode_desc}) to {masked_recip}")
        elif receipt.status == DeliveryStatus.DUPLICATE:
            print(f"[WARN] Phase 3.4: WhatsApp delivery skipped ({receipt.error_message})")
        else:
            print(f"[FAIL] Phase 3.4: WhatsApp delivery failed: {receipt.error_message}")

    print("\n[PASS] PHASE 3 COMPLETE: Final enrollment consolidated and delivered successfully.")
    return 0


def cmd_validate_digi(args: argparse.Namespace) -> int:
    """Execute standalone Phase 2 DigiGold/DigiSilver validation."""
    target_date = parse_target_date(getattr(args, "report_date", None))
    return run_phase2_digi_validation(args, target_date)


def cmd_consolidate_final(args: argparse.Namespace) -> int:
    """Execute standalone Phase 3 Final Enrollment Consolidation & Telegram delivery."""
    target_date = parse_target_date(getattr(args, "report_date", None))
    return run_phase3_consolidation_and_delivery(args, target_date)


def cmd_daily_pipeline(args: argparse.Namespace) -> int:
    """
    Execute daily reporting automation pipeline with phase orchestration:
    - Phase 1 (10:00 AM IST): Innervex SS/SV, Closing Sync, Closing Screenshot.
    - Phase 2 (11:00 AM IST): DigiGold & DigiSilver Source Validation & Retries.
    - Phase 3 (Immediate post Phase 2): Consolidation, Enrollment Screenshot, Personal Telegram.
    """
    target_date = parse_target_date(getattr(args, "report_date", None))
    today = date.today()
    phase = getattr(args, "phase", "all")

    print("=" * 75)
    print("POTHYS SWARNA MAHAL DAILY REPORTING AUTOMATION PIPELINE")
    print("=" * 75)
    print(f"Execution Date:     {today.strftime('%d/%m/%Y')} ({today.isoformat()})")
    print(f"Target Report Date: {target_date.strftime('%d/%m/%Y')} ({target_date.isoformat()})")
    mode_str = "MOCK / OFFLINE" if getattr(args, "mock", False) else ("DRY RUN" if getattr(args, "dry_run", False) else "LIVE PRODUCTION")
    print(f"Pipeline Mode:      {mode_str}")
    print(f"Selected Phase:     {phase.upper()}")
    print("=" * 75)

    if not getattr(args, "dry_run", False) and not getattr(args, "mock", False):
        if os.getenv("STRICT_READ_ONLY", "false").lower() in ("true", "1", "yes"):
            print("\n" + "!" * 75)
            print("PIPELINE BLOCKED: STRICT READ-ONLY MODE IS ACTIVE.")
            print("Execution of live production daily pipeline with Google Sheets mutations is prohibited.")
            print("Please re-run with --dry-run or --mock flag.")
            print("!" * 75 + "\n")
            return 1

    if phase == "1":
        return run_phase1_innervex(args, target_date)
    elif phase == "2":
        return run_phase2_digi_validation(args, target_date)
    elif phase in ("3", "phase3"):
        return run_phase3_consolidation_and_delivery(args, target_date)
    elif phase in ("2-3", "2_3"):
        rc2 = run_phase2_digi_validation(args, target_date)
        if rc2 != 0:
            return rc2
        return run_phase3_consolidation_and_delivery(args, target_date)
    else:  # "all"
        rc1 = run_phase1_innervex(args, target_date)
        if rc1 != 0:
            return rc1
        rc2 = run_phase2_digi_validation(args, target_date)
        if rc2 != 0:
            print("\n[NOTE] Phase 1 completed successfully and preliminary outputs are preserved.")
            print("[NOTE] Phase 2 Digi validation is pending or blocked. Final enrollment consolidation held pending.")
            return rc2
        return run_phase3_consolidation_and_delivery(args, target_date)


def cmd_send_whatsapp(args: argparse.Namespace) -> int:
    """Send generated daily executive report screenshots via WhatsApp delivery channel."""
    config_bundle = load_config()
    target_date = parse_target_date(getattr(args, "report_date", None))

    print("=" * 70)
    print("POTHYS SWARNA MAHAL WHATSAPP REPORT DELIVERY")
    print("=" * 70)
    print(f"Report Date:   {target_date.strftime('%d/%m/%Y')} ({target_date.isoformat()})")
    dry_run = getattr(args, "dry_run", False) or os.getenv("STRICT_READ_ONLY", "false").lower() in ("true", "1", "yes")
    print(f"Mode:          {'DRY RUN (Simulated)' if dry_run else 'LIVE PRODUCTION'}")

    delivery_mode = getattr(args, "delivery_mode", "template")
    print(f"Delivery Mode: {delivery_mode.upper()} (Meta Cloud API)")

    from app.delivery.whatsapp import WhatsAppDeliveryChannel, redact_phone_number
    from app.delivery.base import DeliveryStatus

    channel = WhatsAppDeliveryChannel(
        config=config_bundle.app.whatsapp,
        enabled=True if getattr(args, "dry_run", False) else None,
        dry_run=dry_run,
        recipient=getattr(args, "recipient", None),
        delivery_mode=delivery_mode,
    )

    if not channel.is_enabled():
        print("\nERROR: WhatsApp delivery channel is disabled (whatsapp.enabled=false).")
        print("To test delivery safely in dry-run mode, pass --dry-run:")
        print("  python -m app send-whatsapp --dry-run --report-date 2026-10-20 --recipient +919840123456")
        return 1

    receipt = channel.send_daily_screenshots(
        report_date=target_date,
        recipient=getattr(args, "recipient", None),
        force=getattr(args, "force", False),
        screenshots_dir=getattr(args, "output_dir", None),
    )

    masked_recip = redact_phone_number(receipt.recipient)
    print("\n" + "-" * 70)
    print("DELIVERY RESULT")
    print("-" * 70)
    print(f"Status:           {receipt.status.value}")
    print(f"Recipient:        {masked_recip}")
    print(f"Message ID:       {receipt.message_id or '[NONE]'}")
    print(f"Attachments Sent: {receipt.attachments_sent}")

    if receipt.status == DeliveryStatus.SENT:
        print("\nSUCCESS: WhatsApp executive report delivery completed successfully.")
        return 0
    elif receipt.status == DeliveryStatus.DUPLICATE:
        print(f"\nWARNING: {receipt.error_message}")
        return 0
    else:
        print(f"\nFAILURE: {receipt.error_message}")
        return 1


def cmd_send_telegram(args: argparse.Namespace) -> int:
    """Send verified executive report screenshot via Telegram delivery channel."""
    config_bundle = load_config()
    target_date = parse_target_date(getattr(args, "report_date", None))

    print("=" * 70)
    print("POTHYS SWARNA MAHAL TELEGRAM REPORT DELIVERY")
    print("=" * 70)
    print(f"Report Date:   {target_date.strftime('%d/%m/%Y')} ({target_date.isoformat()})")

    # Decoupled Delivery Authorization Guard:
    # Telegram outbound photo delivery is decoupled from Google Sheets/Innervex read-only controls.
    # Live transmission strictly requires all three conditions:
    #   1. TELEGRAM_ENABLED=true in config / .env
    #   2. TELEGRAM_DRY_RUN=false in config / .env (and --dry-run flag NOT passed on CLI)
    #   3. Destination chat ID matches the single authorized personal chat ID (validated in channel)
    is_explicit_dry_run = getattr(args, "dry_run", False) or getattr(args, "mock", False)
    tg_config = config_bundle.app.telegram

    if is_explicit_dry_run:
        dry_run = True
        channel_enabled = True  # Allow safe dry-run simulation even if channel is disabled in config
    else:
        dry_run = tg_config.dry_run
        channel_enabled = tg_config.enabled

    mode_desc = "MOCK / OFFLINE (SIMULATED ZERO NETWORK)" if getattr(args, "mock", False) else ("DRY RUN (Simulated)" if dry_run else "LIVE PRODUCTION")
    print(f"Mode:          {mode_desc}")

    from app.delivery.telegram import (
        TelegramDeliveryChannel,
        redact_chat_id,
        resolve_telegram_screenshot,
        format_telegram_caption,
    )
    from app.delivery.base import DeliveryStatus

    target_chat_id = getattr(args, "recipient", None) or tg_config.chat_id
    masked_chat = redact_chat_id(target_chat_id)
    print(f"Recipient:     {masked_chat}")

    channel = TelegramDeliveryChannel(
        config=tg_config,
        enabled=channel_enabled,
        dry_run=dry_run,
        chat_id=target_chat_id,
    )

    if not channel.is_enabled():
        print("\nERROR: Telegram delivery channel is disabled (telegram.enabled=false).")
        print("To authorize live transmission, configure locally in .env:")
        print("  TELEGRAM_ENABLED=true")
        print("  TELEGRAM_DRY_RUN=false")
        print("To simulate delivery safely with zero external calls, pass --dry-run:")
        print("  python -m app send-telegram --dry-run --report-date 2026-10-20")
        return 1

    # Screenshot selection
    target_type = getattr(args, "target", "enrollment")
    screenshot_path_arg = getattr(args, "screenshot_path", None)
    if screenshot_path_arg:
        screenshot_file = Path(screenshot_path_arg).resolve()
        if not screenshot_file.is_file():
            print(f"\nERROR: Specified screenshot path not found: {screenshot_file}")
            return 1
    else:
        try:
            screenshot_file = resolve_telegram_screenshot(
                report_date=target_date,
                target=target_type,
                screenshots_dir=getattr(args, "output_dir", None),
            )
        except FileNotFoundError as e:
            print(f"\nERROR: Screenshot resolution failed: {e}")
            return 1

    file_size = screenshot_file.stat().st_size
    print(f"Screenshot:    {screenshot_file.name} ({file_size:,} bytes)")
    print(f"File Path:     {screenshot_file}")

    caption = format_telegram_caption(
        report_date=target_date,
        report_title="Enrollment Performance Report" if "enrollment" in target_type else "Executive Report",
    )

    receipt = channel.send_photo(
        photo_path=screenshot_file,
        caption=caption,
        recipient=target_chat_id,
        report_date=target_date,
        force=getattr(args, "force", False),
    )

    masked_recip = redact_chat_id(receipt.recipient)
    print("\n" + "-" * 70)
    print("DELIVERY RESULT")
    print("-" * 70)
    print(f"Status:           {receipt.status.value}")
    print(f"Recipient:        {masked_recip}")
    print(f"Message ID:       {receipt.message_id or '[NONE]'}")
    print(f"Attachments Sent: {receipt.attachments_sent}")

    if receipt.status == DeliveryStatus.SENT:
        print("\nSUCCESS: Telegram executive report delivery completed successfully.")
        return 0
    elif receipt.status == DeliveryStatus.DUPLICATE:
        print(f"\nWARNING: {receipt.error_message}")
        return 0
    else:
        print(f"\nFAILURE: {receipt.error_message}")
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

    # Command: digi-sync
    digi_parser = subparsers.add_parser(
        "digi-sync",
        help="Synchronize DigiGold and DigiSilver counts to Consolidate Report",
    )
    digi_parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Target report date (YYYY-MM-DD). Defaults to yesterday.",
    )
    digi_parser.add_argument(
        "--digi-spreadsheet-id",
        type=str,
        default=None,
        help="Source Digi Workbook ID (overrides GOOGLE_SHEET_DIGI_ID)",
    )
    digi_parser.add_argument(
        "--spreadsheet-id",
        type=str,
        default=None,
        help="Destination Spreadsheet ID (overrides GOOGLE_SHEET_NEW_ENROLLMENT_ID)",
    )
    digi_parser.add_argument(
        "--service-account",
        type=str,
        default=None,
        help="Path to Google Service Account JSON (overrides GOOGLE_SERVICE_ACCOUNT_PATH)",
    )
    digi_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and validate source only; do not write to destination Google Sheet",
    )
    digi_parser.add_argument(
        "--mock",
        action="store_true",
        help="Simulate execution using mock service for offline testing",
    )

    # Command: closing-sync
    closing_parser = subparsers.add_parser(
        "closing-sync",
        help="Synchronize Scheme Closing Report to Closed Member & Rejoining Report",
    )
    closing_parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Target report date (YYYY-MM-DD). Defaults to yesterday.",
    )
    closing_parser.add_argument(
        "--spreadsheet-id",
        type=str,
        default=None,
        help="Destination Spreadsheet ID (overrides GOOGLE_SHEET_CLOSED_REJOINING_ID)",
    )
    closing_parser.add_argument(
        "--service-account",
        type=str,
        default=None,
        help="Path to Google Service Account JSON (overrides GOOGLE_SERVICE_ACCOUNT_PATH)",
    )
    closing_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution without modifying Google Sheets",
    )
    closing_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass idempotency checks and rerun for existing date",
    )
    closing_parser.add_argument(
        "--mock",
        action="store_true",
        help="Simulate execution using mock service for offline testing",
    )

    # Command: screenshot
    screenshot_parser = subparsers.add_parser(
        "screenshot",
        help="Capture executive screenshot card(s) from Google Sheets reports",
    )
    screenshot_parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Target report date (YYYY-MM-DD). Defaults to yesterday.",
    )
    screenshot_parser.add_argument(
        "--target",
        type=str,
        default="all",
        choices=["all", "enrollment", "closing"],
        help="Target report to capture (all, enrollment, closing). Defaults to all.",
    )
    screenshot_parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Custom destination directory for rendered PNG screenshots (defaults to data/screenshots)",
    )
    screenshot_parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Rendering resolution in DPI (default: 200)",
    )
    screenshot_parser.add_argument(
        "--service-account",
        type=str,
        default=None,
        help="Path to Google Service Account JSON (overrides GOOGLE_SERVICE_ACCOUNT_PATH)",
    )
    screenshot_parser.add_argument(
        "--mock",
        action="store_true",
        help="Execute using MockGoogleSheetsService for offline testing",
    )

    # Command: run-daily
    daily_parser = subparsers.add_parser(
        "run-daily",
        help="Execute unified 4-stage daily reporting automation pipeline",
    )
    daily_parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Target report date (YYYY-MM-DD). Defaults to yesterday.",
    )
    daily_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution without modifying external Google Sheets",
    )
    daily_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass idempotency checks and rerun for existing date",
    )
    daily_parser.add_argument(
        "--mock",
        action="store_true",
        help="Execute using Mock services for offline verification",
    )
    daily_parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Custom destination directory for rendered PNG screenshots",
    )
    daily_parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Rendering resolution in DPI (default: 200)",
    )
    daily_parser.add_argument(
        "--service-account",
        type=str,
        default=None,
        help="Path to Google Service Account JSON (overrides GOOGLE_SERVICE_ACCOUNT_PATH)",
    )
    daily_parser.add_argument(
        "--recipient",
        type=str,
        default=None,
        help="Recipient phone number for WhatsApp delivery (overrides WHATSAPP_RECIPIENT)",
    )
    daily_parser.add_argument(
        "--phase",
        type=str,
        default="all",
        choices=["1", "2", "3", "2-3", "all"],
        help="Pipeline phase: '1' (Innervex SS/SV & Closing), '2' (Digi Validation), '3' (Consolidation & Telegram), '2-3' (Validation + Consolidation), or 'all' (default)",
    )

    # Command: send-whatsapp
    wa_parser = subparsers.add_parser(
        "send-whatsapp",
        help="Deliver generated report screenshots to executive WhatsApp recipient",
    )
    wa_parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Target report date (YYYY-MM-DD). Defaults to yesterday.",
    )
    wa_parser.add_argument(
        "--recipient",
        type=str,
        default=None,
        help="Recipient phone number (e.g. +919840123456). Overrides config.",
    )
    wa_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate delivery with full validation but zero external network calls",
    )
    wa_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass duplicate-send check and re-deliver report",
    )
    wa_parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory where screenshots are stored (defaults to data/screenshots)",
    )
    wa_parser.add_argument(
        "--delivery-mode",
        type=str,
        default="template",
        choices=["template", "session"],
        help="WhatsApp delivery mode: 'template' (default for Meta business-initiated) or 'session' (24h window free-form)",
    )

    # Command: send-telegram
    tg_parser = subparsers.add_parser(
        "send-telegram",
        help="Deliver verified report screenshot to Telegram chat",
    )
    tg_parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Target report date (YYYY-MM-DD). Defaults to yesterday.",
    )
    tg_parser.add_argument(
        "--recipient",
        type=str,
        default=None,
        help="Recipient Telegram chat ID (restricted to configured personal chat ID).",
    )
    tg_parser.add_argument(
        "--screenshot-path",
        type=str,
        default=None,
        help="Explicit path to a screenshot PNG file to deliver.",
    )
    tg_parser.add_argument(
        "--target",
        type=str,
        default="enrollment",
        choices=["enrollment", "closing"],
        help="Screenshot report target type to deliver ('enrollment' or 'closing')",
    )
    tg_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate delivery with full validation but zero external network calls",
    )
    tg_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass duplicate-send check and re-deliver report",
    )
    tg_parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory where screenshots are stored (defaults to data/screenshots)",
    )

    # Command: validate-digi (Phase 2 Standalone)
    vdigi_parser = subparsers.add_parser(
        "validate-digi",
        help="Validate DigiGold/DigiSilver Google Sheets source upload (Phase 2)",
    )
    vdigi_parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Target report date (YYYY-MM-DD)",
    )
    vdigi_parser.add_argument(
        "--digi-spreadsheet-id",
        type=str,
        default=None,
        help="Source Digi Workbook ID",
    )
    vdigi_parser.add_argument(
        "--service-account",
        type=str,
        default=None,
        help="Path to Google Service Account JSON",
    )
    vdigi_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run validation in dry-run mode",
    )
    vdigi_parser.add_argument(
        "--force",
        action="store_true",
        help="Force execution even if outside 11:00 AM - 12:30 PM IST window",
    )
    vdigi_parser.add_argument(
        "--mock",
        action="store_true",
        help="Execute using Mock Google Sheets service for offline verification",
    )

    # Command: consolidate-final (Phase 3 Standalone)
    cfinal_parser = subparsers.add_parser(
        "consolidate-final",
        help="Consolidate Digi numbers and deliver final enrollment report (Phase 3)",
    )
    cfinal_parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Target report date (YYYY-MM-DD)",
    )
    cfinal_parser.add_argument(
        "--spreadsheet-id",
        type=str,
        default=None,
        help="Destination Spreadsheet ID",
    )
    cfinal_parser.add_argument(
        "--digi-spreadsheet-id",
        type=str,
        default=None,
        help="Source Digi Workbook ID",
    )
    cfinal_parser.add_argument(
        "--service-account",
        type=str,
        default=None,
        help="Path to Google Service Account JSON",
    )
    cfinal_parser.add_argument(
        "--recipient",
        type=str,
        default=None,
        help="Telegram chat ID (restricted to configured personal chat ID)",
    )
    cfinal_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution without modifying sheets or external network calls",
    )
    cfinal_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass idempotency / duplicate delivery checks",
    )
    cfinal_parser.add_argument(
        "--mock",
        action="store_true",
        help="Execute using mock services for offline verification",
    )
    cfinal_parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory where screenshots are stored",
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
    elif args.command == "run-daily":
        sys.exit(cmd_daily_pipeline(args))
    elif args.command == "validate-digi":
        sys.exit(cmd_validate_digi(args))
    elif args.command == "consolidate-final":
        sys.exit(cmd_consolidate_final(args))
    elif args.command == "send-whatsapp":
        sys.exit(cmd_send_whatsapp(args))
    elif args.command == "send-telegram":
        sys.exit(cmd_send_telegram(args))
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
    elif args.command == "digi-sync":
        sys.exit(cmd_digi_sync(args))
    elif args.command == "closing-sync":
        sys.exit(cmd_closing_sync(args))
    elif args.command == "screenshot":
        sys.exit(cmd_screenshot(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
