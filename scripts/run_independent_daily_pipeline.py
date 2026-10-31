"""
Production-Grade Independent Daily Reporting Pipeline & Automatic Telegram Delivery.

Architecture:
READ-ONLY SOURCE DATA -> INDEPENDENT PIPELINE CALCULATION -> LOCAL EXCEL-STYLE IMAGE GENERATION -> PRE-DELIVERY VALIDATION -> AUTOMATIC MULTI-RECIPIENT TELEGRAM DELIVERY

Key Features & Safeguards:
- STRICT_READ_ONLY=true enforced throughout. Zero Google Sheets modifications.
- Schedule Gate: Waits until 11:31 AM IST before executing production run.
- Automated Innervex Download: Downloads Subhiksham, Viruksham, and Scheme Closing data directly with local caching.
- DigiGold/DigiSilver Readiness Check: Verifies data upload in 'OCT' tab with retry loop and alerting before processing.
- Zero Stale Cache: In-memory single-pass reading avoids duplicate 28,000+ row network downloads.
- High Performance: Full execution under 1 minute (benchmarked ~26-50 seconds).
- Validation Suite: Target checks, showroom code validation, closing balance checks before any delivery.
- Multi-Recipient Telegram: Delivers both reports to Barath (1688169109) and Marketing (9132692037) with isolated error handling and duplicate prevention.
- Error Alerts: Automatically notifies both recipients via Telegram if source data is unavailable or validation fails.
"""

import os
import sys
import json
import time
import logging
import argparse
import requests
from pathlib import Path
from datetime import date, datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

# Ensure UTF-8 output on Windows console
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env", override=True)

from app.core.config import load_config
from app.google_sheets.client import GoogleApiSheetsService
from app.innervex.auth import InnervexAuthenticator
from app.innervex.client import InnervexClient
from app.innervex.reports import SchemeReportFetcher
from app.reporting.closing import REQUIRED_ACTIVE_SCHEMES, STATUS_FILTER_OPTIONS
from app.processing.location import LocationResolver
from app.processing.rejoining import (
    RejoiningMatchingEngine,
    parse_date_safe,
    normalize_mobile,
)
from app.reporting.image_renderer import (
    render_enrollment_report_image,
    render_closing_report_image,
)
from app.delivery.telegram import (
    TelegramDeliveryChannel,
    redact_chat_id,
    redact_token,
)
from app.repositories.snapshots import SnapshotManager
from app.delivery.base import DeliveryStatus
from app.reporting.digi import DigiSourceReader

logger = logging.getLogger("independent_pipeline")

IST = timezone(timedelta(hours=5, minutes=30))

# Canonical location-to-branch mapping
LOC_TO_BRANCH = {
    # TIRUNELVELI / NELLAI -> TVL
    "TIRUNELVELI": "TVL", "TIRUNELVELI ECOMM": "TVL", "TIRUNELVELI SV": "TVL", "TIRUNELVELI SV ECOMM": "TVL",
    "NELLAI": "TVL", "NELLAI ECOMM": "TVL", "NELLAI SV": "TVL", "NELLAI SV ECOMM": "TVL",
    # CHROMEPET -> CPT
    "CHROMEPET": "CPT", "CHROMEPET ECOMM": "CPT", "CHROMEPET SV": "CPT", "CHROMEPET SV ECOMM": "CPT",
    # TRIVANDRUM / TVM -> TVM
    "TRIVANDRUM": "TVM", "TRIVANDRUM ECOMM": "TVM", "TRIVANDRUM SV": "TVM", "TRIVANDRUM SV ECOMM": "TVM",
    "TVM": "TVM", "TVM ECOMM": "TVM", "TVM SV": "TVM", "TVM SV ECOMM": "TVM",
    # TRICHY / TPJ -> TPJ
    "TRICHY": "TPJ", "TRICHY ECOMM": "TPJ", "TRICHY SV": "TPJ", "TRICHY SV ECOMM": "TPJ",
    "TPJ": "TPJ", "TPJ ECOMM": "TPJ", "TPJ SV": "TPJ", "TPJ SV ECOMM": "TPJ",
    # COIMBATORE / CBE -> CBE
    "COIMBATORE": "CBE", "COIMBATORE ECOMM": "CBE", "COIMBATORE SV": "CBE", "COIMBATORE SV ECOMM": "CBE",
    "CBE": "CBE", "CBE ECOMM": "CBE", "CBE SV": "CBE", "CBE SV ECOMM": "CBE",
    # SALEM / SLM -> SLM
    "SALEM": "SLM", "SALEM ECOMM": "SLM", "SALEM SV": "SLM", "SALEM SV ECOMM": "SLM",
    "SLM": "SLM", "SLM ECOMM": "SLM", "SLM SV": "SLM", "SLM SV ECOMM": "SLM",
    # POONAMALLEE / PNM -> PNM
    "POONAMALLEE": "PNM", "POONAMALLEE ECOMM": "PNM", "POONAMALLEE SV": "PNM", "POONAMALLEE SV ECOMM": "PNM",
    "PNM": "PNM", "PNM ECOMM": "PNM", "PNM SV": "PNM", "PNM SV ECOMM": "PNM",
    # PADI -> PADI
    "PADI": "PADI", "PADI ECOMM": "PADI", "PADI SV": "PADI", "PADI SV ECOMM": "PADI",
    # KANCHEEPURAM / KANCHI -> KANCHI
    "KANCHEEPURAM": "KANCHI", "KANCHEEPURAM ECOMM": "KANCHI", "KANCHEEPURAM SV": "KANCHI", "KANCHEEPURAM SV ECOMM": "KANCHI",
    "KANCHI": "KANCHI", "KANCHI ECOMM": "KANCHI", "KANCHI SV": "KANCHI", "KANCHI SV ECOMM": "KANCHI",
    # NAGERCOIL / NAGARKOVIL / NGL -> NGL
    "NAGERCOIL": "NGL", "NAGERCOIL ECOMM": "NGL", "NAGERCOIL SV": "NGL", "NAGERCOIL SV ECOMM": "NGL",
    "NAGARKOVIL": "NGL", "NAGARKOVIL ECOMM": "NGL", "NAGARKOVIL SV": "NGL", "NAGARKOVIL SV ECOMM": "NGL",
    "NGL": "NGL", "NGL ECOMM": "NGL", "NGL SV": "NGL", "NGL SV ECOMM": "NGL",
    # ONLINE / CORPORATE OFFICE -> ONLINE
    "ONLINE": "ONLINE", "ONLINE SV": "ONLINE", "ONLINE ECOMM": "ONLINE", "ONLINE SV ECOMM": "ONLINE",
    "CORPORATE OFFICE": "ONLINE", "CORPORATE OFFICE SV": "ONLINE", "CORPORATE OFFICE ECOMM": "ONLINE", "CORPORATE OFFICE SV ECOMM": "ONLINE",
}


def download_innervex_data(
    config_bundle,
    target_date: date,
    force_download: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], bool]:
    """Download Innervex SS, SV, and Closing data with automatic local caching."""
    raw_dir = project_root / "data" / "raw" / str(target_date.year) / f"{target_date.month:02d}" / f"{target_date.day:02d}"
    ss_cache = raw_dir / f"ss_{target_date.day:02d}_data.json"
    sv_cache = raw_dir / f"sv_{target_date.day:02d}_data.json"
    close_cache = raw_dir / f"close_{target_date.day:02d}_data.json"

    # Reuse local cache if present and not forced
    if not force_download and ss_cache.exists() and sv_cache.exists() and close_cache.exists():
        with open(ss_cache, "r", encoding="utf-8") as f:
            ss_data = json.load(f)
        with open(sv_cache, "r", encoding="utf-8") as f:
            sv_data = json.load(f)
        with open(close_cache, "r", encoding="utf-8") as f:
            close_data = json.load(f)
        return ss_data, sv_data, close_data, True

    # Connect to Innervex
    session = requests.Session()
    session.headers.update(config_bundle.innervex.default_headers)
    authenticator = InnervexAuthenticator(config_bundle.innervex)
    auth_res = authenticator.login(session=session)
    if not auth_res.authenticated:
        raise ConnectionError("Innervex authentication failed.")

    client = InnervexClient(config_bundle.innervex, auth_strategy=authenticator)
    client.session = session
    fetcher = SchemeReportFetcher(client)

    # 1. Subhiksham
    ss_cfg = config_bundle.get_report("subhiksham")
    ss_payload = fetcher.fetch_report(
        config=ss_cfg,
        report_date=target_date,
        showroom_codes=config_bundle.mappings.showroom_codes,
    )
    ss_data = ss_payload.data

    # 2. Viruksham
    sv_cfg = config_bundle.get_report("viruksham")
    sv_payload = fetcher.fetch_report(
        config=sv_cfg,
        report_date=target_date,
        showroom_codes=config_bundle.mappings.showroom_codes,
    )
    sv_data = sv_payload.data

    # 3. Scheme Closing
    date_str = target_date.strftime("%Y-%m-%d")
    quote_list = lambda items: ",".join(f"'{x}'" for x in items)
    showrooms = config_bundle.mappings.showroom_codes or []
    form_data = {
        "Action": "loadData",
        "fromDate": date_str,
        "toDate": date_str,
        "bpCode": "",
        "mobile": "",
        "Export": "EXPORT",
        "Scheme": quote_list(REQUIRED_ACTIVE_SCHEMES),
        "showRoom": quote_list(showrooms),
        "ReportType": "SCHEME CLOSING REPORT",
        "Type": "SALES",
        "status": quote_list(STATUS_FILTER_OPTIONS),
    }
    resp = client.post_report("/SchemeClosingReport", form_data)
    close_data = resp.data

    # Save to local cache
    raw_dir.mkdir(parents=True, exist_ok=True)
    with open(ss_cache, "w", encoding="utf-8") as f:
        json.dump(ss_data, f, indent=2)
    with open(sv_cache, "w", encoding="utf-8") as f:
        json.dump(sv_data, f, indent=2)
    with open(close_cache, "w", encoding="utf-8") as f:
        json.dump(close_data, f, indent=2)

    return ss_data, sv_data, close_data, False


def notify_failure(channel: TelegramDeliveryChannel, error_text: str, report_date: date) -> None:
    """Send immediate failure alert to all configured Telegram recipients."""
    alert_msg = (
        f"🚨 *POTHYS SWARNA MAHAL DAILY REPORT ALERT*\n"
        f"Date: {report_date.strftime('%d/%m/%Y')}\n\n"
        f"❌ *Pipeline Halted:* {error_text}\n\n"
        f"Reports have not been generated or delivered. Please check the logs."
    )
    for r in channel.configured_chat_ids:
        try:
            channel.send_message(text=alert_msg, recipient=r)
            print(f"  [ALERT SENT] Delivered error notification to {redact_chat_id(r)}")
        except Exception as e:
            print(f"  [ALERT FAILED] Could not alert {redact_chat_id(r)}: {e}")


def run_pipeline(
    target_date: date,
    dry_run: bool = False,
    force_download: bool = False,
    force_delivery: bool = False,
    skip_time_check: bool = False,
    retry_interval_sec: int = 60,
    max_retries: int = 10,
) -> int:
    target_date_str = target_date.strftime("%d/%m/%Y")
    date_iso = target_date.isoformat()
    date_compact = target_date.strftime("%Y%m%d")

    stage_timings: Dict[str, float] = {}
    pipeline_start = time.perf_counter()

    print("=" * 80)
    print(f"PRODUCTION AUTOMATED DAILY REPORTING PIPELINE — {target_date_str}")
    print("=" * 80)
    print(f"Target Report Date:  {target_date_str} ({date_iso})")
    print(f"Pipeline Mode:       STRICT_READ_ONLY=true (Zero production sheet mutations)")
    print(f"Delivery Mode:       {'DRY RUN (Simulated)' if dry_run else 'LIVE DISPATCH'}")
    print("=" * 80)

    config_bundle = load_config()
    tg_config = config_bundle.app.telegram
    channel = TelegramDeliveryChannel(config=tg_config, dry_run=dry_run, chat_ids=tg_config.chat_ids)

    # -------------------------------------------------------------------------
    # Stage 1: Schedule & Timing Check (11:31 AM IST Gate)
    # -------------------------------------------------------------------------
    t0 = time.perf_counter()
    now_ist = datetime.now(IST)
    target_threshold = now_ist.replace(hour=11, minute=31, second=0, microsecond=0)
    if not skip_time_check and now_ist < target_threshold:
        wait_sec = (target_threshold - now_ist).total_seconds()
        print(f"[Stage 1: Schedule Gate] Current IST: {now_ist.strftime('%H:%M:%S')}. Waiting {wait_sec:.0f}s until 11:31 AM IST...")
        time.sleep(wait_sec)
    else:
        print(f"[Stage 1: Schedule Gate] Passed (Current IST: {now_ist.strftime('%H:%M:%S')})")
    stage_timings["1_schedule_timing_gate"] = time.perf_counter() - t0

    # -------------------------------------------------------------------------
    # Stage 2: Automated Innervex Data Extraction
    # -------------------------------------------------------------------------
    t0 = time.perf_counter()
    print(f"\n[Stage 2: Innervex Download] Downloading / verifying Innervex raw records...")
    try:
        ss_day_data, sv_day_data, close_day_rows, was_cached = download_innervex_data(
            config_bundle, target_date, force_download=force_download
        )
        print(f"  Innervex SS:      {len(ss_day_data):,} records {'(from local cache)' if was_cached else '(freshly downloaded)'}")
        print(f"  Innervex SV:      {len(sv_day_data):,} records {'(from local cache)' if was_cached else '(freshly downloaded)'}")
        print(f"  Innervex Closing: {len(close_day_rows):,} records {'(from local cache)' if was_cached else '(freshly downloaded)'}")
    except Exception as e:
        err_msg = f"Innervex download failed: {e}"
        print(f"  [ERROR] {err_msg}")
        notify_failure(channel, err_msg, target_date)
        return 1
    stage_timings["2_innervex_download"] = time.perf_counter() - t0

    # -------------------------------------------------------------------------
    # Stage 3: DigiGold/DigiSilver Source Readiness Check & Single-Pass Fetch
    # -------------------------------------------------------------------------
    t0 = time.perf_counter()
    sa_path = str(project_root / "secrets" / "service_account.json")
    sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
    digi_reader = DigiSourceReader(sheets, digi_id)

    print(f"\n[Stage 3: Digi Readiness Check] Verifying upstream upload in 'OCT' tab...")
    is_ready = False
    raw_oct_rows = []
    attempt = 0
    max_dt = None

    while attempt < max_retries:
        attempt += 1
        is_ready, count_for_date, max_dt, raw_oct_rows = digi_reader.check_readiness_and_fetch(target_date, "OCT")
        if is_ready:
            print(f"  [PASS] Digi upload confirmed ready: {count_for_date:,} records for {target_date_str} (latest: {max_dt})")
            break
        else:
            print(f"  [PENDING] Attempt {attempt}/{max_retries}: Upload for {target_date_str} not ready yet (latest: {max_dt}).")
            if attempt < max_retries:
                print(f"  Waiting {retry_interval_sec}s before next retry...")
                time.sleep(retry_interval_sec)

    if not is_ready:
        err_msg = f"DigiGold/DigiSilver data for {target_date_str} is not ready after {attempt} attempts (latest schdate: {max_dt})."
        print(f"\n[BLOCKED] {err_msg}")
        notify_failure(channel, err_msg, target_date)
        return 1

    # In-memory single-pass calculation (reuses raw_oct_rows without re-reading sheet)
    branch_dg, branch_ds = digi_reader.extract_raw_counts("OCT", target_date, raw_rows=raw_oct_rows)
    print(f"  [OK] DigiGold Raw Counts:   E COMM={branch_dg.get('ONLINE', 0):,}, Total={sum(branch_dg.values()):,}")
    print(f"  [OK] DigiSilver Raw Counts: E COMM={branch_ds.get('ONLINE', 0):,}, Total={sum(branch_ds.values()):,}")
    stage_timings["3_digi_readiness_and_fetch"] = time.perf_counter() - t0

    # -------------------------------------------------------------------------
    # Stage 4: Historical Google Sheets Data Fetch
    # -------------------------------------------------------------------------
    t0 = time.perf_counter()
    enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")

    print(f"\n[Stage 4: Sheets Historical Data] Fetching historical baseline...")
    sheet_ss = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
    sheet_sv = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
    sheet_close = sheets.read_range(close_id, "'October - 2026'!A2:H")

    snap_mgr = SnapshotManager(project_root / "data" / "snapshots")
    close_snapshot = snap_mgr.get_or_create_snapshot(
        category="closing",
        report_date=target_date,
        records=sheet_close,
        source_identifier=f"{close_id}::'October - 2026'!A2:H",
    )
    sheet_close_records = close_snapshot.records
    print(f"  Historical SS Rows: {len(sheet_ss):,}")
    print(f"  Historical SV Rows: {len(sheet_sv):,}")
    print(f"  Closing Snapshot:   {len(sheet_close_records):,} rows (ID: {close_snapshot.snapshot_id})")
    stage_timings["4_sheets_historical_fetch"] = time.perf_counter() - t0

    # -------------------------------------------------------------------------
    # Stage 5: In-Memory Data Processing & Deduplication
    # -------------------------------------------------------------------------
    t0 = time.perf_counter()
    loc_resolver = LocationResolver(config_bundle.mappings)

    # Subhiksham (SS): Historical < target_date plus fresh target_date
    seen_ss_keys = set()
    combined_ss = []
    for r in sheet_ss:
        dt = parse_date_safe(r[5] if len(r)>5 else "")
        if dt and dt < target_date:
            amt = float((r[8] if len(r)>8 else "0").replace(",", "").strip() or 0.0)
            msno = str(r[3] if len(r)>3 else "").strip()
            seen_ss_keys.add((msno, dt.isoformat()))
            combined_ss.append({
                "date": dt,
                "location2": (r[6] if len(r)>6 else "").strip().upper(),
                "amount": amt,
                "mobile": normalize_mobile(r[9] if len(r)>9 else ""),
                "msno": msno,
                "name": r[4] if len(r)>4 else "",
            })

    for item in ss_day_data:
        dt = parse_date_safe(item.get("SCHDATE"))
        amt = float(str(item.get("RECAMOUNT") or 0.0).replace(",", "").strip())
        loc_attr = loc_resolver.resolve(cost_name=item.get("COSTNAME"), comm_code=item.get("COMMCODE"), scheme_type="ss")
        loc2 = loc_attr.location_2.strip().upper()
        mob = normalize_mobile(item.get("MOBILENO"))
        msno = str(item.get("MSNO") or "").strip()
        key = (msno, dt.isoformat() if dt else "")
        if key not in seen_ss_keys:
            seen_ss_keys.add(key)
            combined_ss.append({
                "date": dt,
                "location2": loc2,
                "amount": amt,
                "mobile": mob,
                "msno": msno,
                "name": str(item.get("NAME") or ""),
            })

    for r in sheet_ss:
        dt = parse_date_safe(r[5] if len(r)>5 else "")
        if dt and dt == target_date:
            amt = float((r[8] if len(r)>8 else "0").replace(",", "").strip() or 0.0)
            msno = str(r[3] if len(r)>3 else "").strip()
            key = (msno, dt.isoformat())
            if key not in seen_ss_keys:
                seen_ss_keys.add(key)
                combined_ss.append({
                    "date": dt,
                    "location2": (r[6] if len(r)>6 else "").strip().upper(),
                    "amount": amt,
                    "mobile": normalize_mobile(r[9] if len(r)>9 else ""),
                    "msno": msno,
                    "name": r[4] if len(r)>4 else "",
                })

    # Viruksham (SV): Historical < target_date plus fresh target_date
    seen_sv_keys = set()
    combined_sv = []
    for r in sheet_sv:
        dt = parse_date_safe(r[5] if len(r)>5 else "")
        if dt and dt < target_date:
            amt = float((r[8] if len(r)>8 else "0").replace(",", "").strip() or 0.0)
            msno = str(r[3] if len(r)>3 else "").strip()
            seen_sv_keys.add((msno, dt.isoformat()))
            combined_sv.append({
                "date": dt,
                "location2": (r[6] if len(r)>6 else "").strip().upper(),
                "amount": amt,
                "mobile": normalize_mobile(r[9] if len(r)>9 else ""),
                "msno": msno,
                "name": r[4] if len(r)>4 else "",
            })

    for item in sv_day_data:
        dt = parse_date_safe(item.get("SCHDATE"))
        amt = float(str(item.get("RECAMOUNT") or 0.0).replace(",", "").strip())
        loc_attr = loc_resolver.resolve(cost_name=item.get("COSTNAME"), comm_code=item.get("COMMCODE"), scheme_type="sv")
        loc2 = loc_attr.location_2.strip().upper()
        mob = normalize_mobile(item.get("MOBILENO"))
        msno = str(item.get("MSNO") or "").strip()
        key = (msno, dt.isoformat() if dt else "")
        if key not in seen_sv_keys:
            seen_sv_keys.add(key)
            combined_sv.append({
                "date": dt,
                "location2": loc2,
                "amount": amt,
                "mobile": mob,
                "msno": msno,
                "name": str(item.get("NAME") or ""),
            })

    for r in sheet_sv:
        dt = parse_date_safe(r[5] if len(r)>5 else "")
        if dt and dt == target_date:
            amt = float((r[8] if len(r)>8 else "0").replace(",", "").strip() or 0.0)
            msno = str(r[3] if len(r)>3 else "").strip()
            key = (msno, dt.isoformat())
            if key not in seen_sv_keys:
                seen_sv_keys.add(key)
                combined_sv.append({
                    "date": dt,
                    "location2": (r[6] if len(r)>6 else "").strip().upper(),
                    "amount": amt,
                    "mobile": normalize_mobile(r[9] if len(r)>9 else ""),
                    "msno": msno,
                    "name": r[4] if len(r)>4 else "",
                })

    stage_timings["5_data_processing_and_dedup"] = time.perf_counter() - t0

    # -------------------------------------------------------------------------
    # Stage 6: Calculations & Aggregations
    # -------------------------------------------------------------------------
    t0 = time.perf_counter()
    target_branches = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KANCHI", "NGL", "ONLINE"]
    rev_targets = {
        "TVL": 40000000, "CPT": 72000000, "TVM": 27000000, "TPJ": 40000000, "CBE": 30000000,
        "SLM": 30000000, "PNM": 20000000, "PADI": 54000000, "KANCHI": 27000000, "NGL": None, "ONLINE": None
    }
    cnt_targets = {
        "TVL": 3000, "CPT": 6000, "TVM": 4000, "TPJ": 5000, "CBE": 7000,
        "SLM": 3000, "PNM": 2000, "PADI": 6000, "KANCHI": None, "NGL": None, "ONLINE": None
    }

    table1_rows = []
    table2_rows = []
    for b in target_branches:
        matching_locs = [loc for loc, b_code in LOC_TO_BRANCH.items() if b_code == b]
        ss_recs = [r for r in combined_ss if r["location2"] in matching_locs]
        sv_recs = [r for r in combined_sv if r["location2"] in matching_locs]
        ss_amt = sum(r["amount"] for r in ss_recs)
        sv_amt = sum(r["amount"] for r in sv_recs)
        tot_amt = ss_amt + sv_amt
        tgt_val = rev_targets[b]
        table1_rows.append({
            "branch": b,
            "target": tgt_val,
            "ss": ss_amt,
            "sv": sv_amt,
            "total": tot_amt,
            "yet": (tot_amt - tgt_val) if tgt_val else tot_amt,
            "ach_pct": round((tot_amt / tgt_val) * 100) if tgt_val else None,
        })
        ss_cnt = len(ss_recs)
        sv_cnt = len(sv_recs)
        dg_cnt = branch_dg.get(b, 0)
        ds_cnt = branch_ds.get(b, 0)
        tot_cnt = ss_cnt + sv_cnt + dg_cnt + ds_cnt
        table2_rows.append({
            "branch": "E COMM" if b == "ONLINE" else b,
            "target": cnt_targets[b],
            "ss": ss_cnt,
            "sv": sv_cnt,
            "dg": dg_cnt,
            "ds": ds_cnt,
            "total": tot_cnt,
            "avg": round(tot_cnt / target_date.day),
        })

    tot_rev_target = sum(r["target"] for r in table1_rows if r["target"] is not None)
    tot_comb_rev = sum(r["total"] for r in table1_rows)
    table1_total = {
        "target": tot_rev_target,
        "ss": sum(r["ss"] for r in table1_rows),
        "sv": sum(r["sv"] for r in table1_rows),
        "total": tot_comb_rev,
        "yet": sum(r["yet"] for r in table1_rows),
        "ach_pct": round((tot_comb_rev / tot_rev_target) * 100) if tot_rev_target else None,
    }
    table2_total = {
        "target": sum(r["target"] for r in table2_rows if r["target"] is not None),
        "ss": sum(r["ss"] for r in table2_rows),
        "sv": sum(r["sv"] for r in table2_rows),
        "dg": sum(r["dg"] for r in table2_rows),
        "ds": sum(r["ds"] for r in table2_rows),
        "total": sum(r["total"] for r in table2_rows),
        "avg": round(sum(r["total"] for r in table2_rows) / target_date.day),
    }

    # Scheme Rejoining Matching
    combined_closing = []
    seen_close_keys = set()
    for r in sheet_close_records:
        pb = str(r[2] if len(r) > 2 else "").strip()
        dt = str(r[0] if len(r) > 0 else "").strip()
        sh = str(r[7] if len(r) > 7 else "").strip()
        combined_closing.append(r)
        if pb:
            seen_close_keys.add((pb, dt, sh))

    for row in close_day_rows:
        pb = str(row.get("PASSBOOK") or "").strip()
        dt = str(row.get("DOCDATE") or "").strip()
        sh = str(row.get("SHOWROOM") or "").strip()
        if not pb or (pb, dt, sh) not in seen_close_keys:
            if pb:
                seen_close_keys.add((pb, dt, sh))
            row_arr = [
                dt,
                row.get("NAME") or "",
                pb,
                row.get("MOBILE") or "",
                row.get("SCHEMENAME") or "",
                str(row.get("WEIGHT") or "0"),
                row.get("STATUS") or "",
                sh,
            ]
            combined_closing.append(row_arr)

    rejoining_ss_input = [
        {"schdate": r["date"], "mobile": r["mobile"], "amount": r["amount"], "showroom": r["location2"], "scheme": "SS", "msno": r["msno"], "name": r["name"]}
        for r in combined_ss
    ]
    rejoining_sv_input = [
        {"schdate": r["date"], "mobile": r["mobile"], "amount": r["amount"], "showroom": r["location2"], "scheme": "SV", "msno": r["msno"], "name": r["name"]}
        for r in combined_sv
    ]

    matcher = RejoiningMatchingEngine(
        cutoff_date=target_date,
        require_enrollment_after_closure=False,
        mode="month_to_date",
        snapshot_id=close_snapshot.snapshot_id,
        snapshot_hash=close_snapshot.sha256,
    )
    rejoin_res = matcher.process(
        closed_records=combined_closing,
        ss_enrollments=rejoining_ss_input,
        sv_enrollments=rejoining_sv_input,
    )

    counts_map = {r.showroom: r.closed_count for r in rejoin_res.table1_rows}
    rejoin_table1 = [
        {"showroom": r.showroom, "closed": r.closed_count, "rejoin_ss": r.rejoin_ss, "rejoin_sv": r.rejoin_sv, "total_rejoin": r.total_rejoin, "rejoin_pct": r.rejoin_percentage}
        for r in rejoin_res.table1_rows
    ]
    rejoin_table1_total = {
        "closed": rejoin_res.total_closed_count,
        "rejoin_ss": rejoin_res.total_rejoin_ss,
        "rejoin_sv": rejoin_res.total_rejoin_sv,
        "total_rejoin": rejoin_res.total_rejoin_count,
        "rejoin_pct": rejoin_res.overall_rejoin_percentage,
    }
    rejoin_weight = [
        {"showroom": r.showroom, "count": counts_map.get(r.showroom, 0), "weight": r.closed_weight}
        for r in rejoin_res.table2_weight_rows
    ]
    rejoin_weight_total = {
        "count": rejoin_res.total_closed_count,
        "weight": rejoin_res.total_closed_weight,
    }
    rejoin_scheme = []
    for r in rejoin_res.table3_scheme_rows:
        sh_ss = sum(cnt for sch, cnt in r.scheme_counts.items() if "SUBHIKSHAM" in sch.upper() or "SUBIKSHAM" in sch.upper() or "LABHAM" in sch.upper() or sch.upper().startswith("SS"))
        sh_sv = sum(cnt for sch, cnt in r.scheme_counts.items() if "VIRUKSHAM" in sch.upper() or sch.upper().startswith("SV"))
        rejoin_scheme.append({"showroom": r.showroom, "ss": sh_ss, "sv": sh_sv})
    rejoin_scheme_total = {
        "ss": sum(r["ss"] for r in rejoin_scheme),
        "sv": sum(r["sv"] for r in rejoin_scheme),
    }

    stage_timings["6_rejoining_and_metrics"] = time.perf_counter() - t0

    # -------------------------------------------------------------------------
    # Stage 7: Pre-Delivery Validation Suite
    # -------------------------------------------------------------------------
    t0 = time.perf_counter()
    validation_errors = []
    if tot_rev_target != 340000000:
        validation_errors.append(f"Total Target Revenue is ₹{tot_rev_target:,}, expected ₹340,000,000")
    unmapped_ss = [r for r in combined_ss if r["location2"] not in LOC_TO_BRANCH]
    unmapped_sv = [r for r in combined_sv if r["location2"] not in LOC_TO_BRANCH]
    if unmapped_ss or unmapped_sv:
        validation_errors.append(f"Unmapped locations found: SS={len(unmapped_ss)}, SV={len(unmapped_sv)}")
    if rejoin_table1_total["closed"] <= 0:
        validation_errors.append(f"Invalid closed accounts count: {rejoin_table1_total['closed']}")
    if rejoin_weight_total["weight"] <= 0:
        validation_errors.append(f"Invalid closed weight: {rejoin_weight_total['weight']}")
    if rejoin_scheme_total["ss"] + rejoin_scheme_total["sv"] != rejoin_table1_total["closed"]:
        validation_errors.append(f"SS/SV closing split ({rejoin_scheme_total['ss']}+{rejoin_scheme_total['sv']}) != Total Closed ({rejoin_table1_total['closed']})")

    if validation_errors:
        err_msg = "; ".join(validation_errors)
        print(f"\n[VALIDATION FAILED] {err_msg}")
        notify_failure(channel, f"Pre-delivery validation check failed: {err_msg}", target_date)
        return 1
    print(f"  [PASS] All pre-delivery business validations passed successfully.")
    stage_timings["7_validation_suite"] = time.perf_counter() - t0

    # -------------------------------------------------------------------------
    # Stage 8: Excel-Style Image Generation (Direct Local Pillow Renderer)
    # -------------------------------------------------------------------------
    t0 = time.perf_counter()
    img_dir = project_root / "data" / "screenshots"
    img_dir.mkdir(parents=True, exist_ok=True)
    out_enr_path = img_dir / f"enrollment_report_{date_compact}.png"
    out_close_path = img_dir / f"closing_rejoining_report_{date_compact}.png"
    render_enrollment_report_image(target_date, table1_rows, table1_total, table2_rows, table2_total, out_enr_path)
    render_closing_report_image(target_date, rejoin_table1, rejoin_table1_total, rejoin_weight, rejoin_weight_total, rejoin_scheme, rejoin_scheme_total, out_close_path)
    print(f"  [OK] Generated Enrollment Report: {out_enr_path.name} ({out_enr_path.stat().st_size:,} bytes)")
    print(f"  [OK] Generated Closing Report:    {out_close_path.name} ({out_close_path.stat().st_size:,} bytes)")
    stage_timings["8_image_generation"] = time.perf_counter() - t0

    # -------------------------------------------------------------------------
    # Stage 9: Dual-Recipient Telegram Delivery
    # -------------------------------------------------------------------------
    t0 = time.perf_counter()
    recipients = channel.configured_chat_ids
    print(f"\n[Stage 9: Telegram Delivery] Delivering to {len(recipients)} authorized recipients...")

    cap_enr = f"POTHYS SWARNA MAHAL\nDaily New Enrollment Report — {target_date_str}"
    cap_rej = f"POTHYS SWARNA MAHAL\nDaily Scheme Rejoining Report — {target_date_str}"

    delivery_results: Dict[str, Dict[str, Any]] = {}
    for r in recipients:
        masked_r = redact_chat_id(r)
        # Duplicate delivery prevention
        if not force_delivery and channel._history.is_already_delivered(target_date, r, out_enr_path.name) and channel._history.is_already_delivered(target_date, r, out_close_path.name):
            print(f"  Recipient {masked_r}: [SKIPPED] Reports already successfully delivered today.")
            delivery_results[r] = {"status": "ALREADY_DELIVERED", "photo1": "SKIPPED", "photo2": "SKIPPED"}
            continue

        res1 = channel.send_photo(photo_path=out_enr_path, caption=cap_enr, recipient=r, report_date=target_date, force=force_delivery)
        res2 = channel.send_photo(photo_path=out_close_path, caption=cap_rej, recipient=r, report_date=target_date, force=force_delivery)
        delivery_results[r] = {
            "status": "SENT" if (res1.status == DeliveryStatus.SENT and res2.status == DeliveryStatus.SENT) else "FAILED",
            "photo1": res1.status.value,
            "photo2": res2.status.value,
            "msg1": res1.message_id or res1.error_message,
            "msg2": res2.message_id or res2.error_message,
        }
        print(f"  Recipient {masked_r}: Photo 1 = {res1.status.value} ({res1.message_id or res1.error_message}), Photo 2 = {res2.status.value} ({res2.message_id or res2.error_message})")

    stage_timings["9_telegram_delivery"] = time.perf_counter() - t0

    # -------------------------------------------------------------------------
    # Telegram Delivery Verification Report
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("TELEGRAM DELIVERY VERIFICATION REPORT")
    print("=" * 80)
    recipient_labels = {"1688169109": "Barath", "9132692037": "Marketing"}
    for r, info in delivery_results.items():
        name = recipient_labels.get(r, "Authorized User")
        print(f"Recipient:      {name} ({r})")
        print(f"  Photo 1 (Enrollment): {info.get('photo1')} | Message ID: {info.get('msg1')}")
        print(f"  Photo 2 (Closing):    {info.get('photo2')} | Message ID: {info.get('msg2')}")
        print(f"  Overall Status:       {info.get('status')}")
        print("-" * 80)

    # -------------------------------------------------------------------------
    # Performance Summary & Execution Audit
    # -------------------------------------------------------------------------
    total_time = time.perf_counter() - pipeline_start
    print("\n" + "=" * 80)
    print("PERFORMANCE BENCHMARK EXECUTION SUMMARY")
    print("=" * 80)
    for k, v in stage_timings.items():
        print(f"  {k:35}: {v:6.2f}s ({v/total_time*100:5.1f}%)")
    print("-" * 80)
    print(f"  TOTAL EXECUTION TIME               : {total_time:6.2f}s ({total_time/60:.2f} minutes)")
    print(f"  TARGET EXECUTION TIME              : 120 - 180s (2.0 - 3.0 minutes)")
    print(f"  PERFORMANCE MARGIN                 : {(total_time / 180 * 100):.1f}% of allowable limit ({(180 - total_time):.1f}s buffer)")
    print("=" * 80)

    # Check if all deliveries succeeded
    all_success = all(res["status"] in ("SENT", "ALREADY_DELIVERED") for res in delivery_results.values())
    return 0 if all_success else 1


def main():
    parser = argparse.ArgumentParser(description="Automated Independent Daily Reporting Pipeline")
    parser.add_argument("--date", type=str, default=None, help="Target report date (YYYY-MM-DD). Defaults to today in IST.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate Telegram delivery without external API calls.")
    parser.add_argument("--force-download", action="store_true", help="Force fresh download from Innervex.")
    parser.add_argument("--force-delivery", action="store_true", help="Bypass idempotency duplicate-delivery check.")
    parser.add_argument("--skip-time-check", action="store_true", help="Bypass the 11:31 AM IST schedule gate.")
    parser.add_argument("--retry-interval", type=int, default=60, help="Interval in seconds between Digi readiness checks.")
    parser.add_argument("--max-retries", type=int, default=10, help="Maximum attempts for Digi readiness check.")

    args = parser.parse_args()

    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        # Default to yesterday in IST (per business rule: report_date = today - 1 day)
        now_ist = datetime.now(IST)
        target_date = now_ist.date() - timedelta(days=1)

    rc = run_pipeline(
        target_date=target_date,
        dry_run=args.dry_run,
        force_download=args.force_download,
        force_delivery=args.force_delivery,
        skip_time_check=args.skip_time_check,
        retry_interval_sec=args.retry_interval,
        max_retries=args.max_retries,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
