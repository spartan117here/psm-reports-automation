"""
Sequential Historical Processor & 09-Nov-2026 Production Delivery Pipeline.

Chronologically processes:
1. 06-Nov-2026
2. 07-Nov-2026
3. 08-Nov-2026
4. 09-Nov-2026

Maintains exact audit trail, immutable snapshots, and independent Python calculations.
Generates and delivers ONLY the two final report images for 09-Nov-2026 to:
- Barath (1688169109)
- Marketing (9132692037)
"""

import os
import sys
import json
import time
import logging
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env", override=True)

from app.core.config import load_config
from app.google_sheets.client import GoogleApiSheetsService
from app.google_sheets.monthly_source import MonthlySourceDiscovery
from app.reporting.digi import DigiSourceReader
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
    TelegramDeliveryHistory,
    redact_chat_id,
)
from app.delivery.base import DeliveryStatus
from app.repositories.snapshots import (
    SnapshotManager,
    SourceSnapshot,
    compute_records_sha256,
)
from scripts.run_independent_daily_pipeline import (
    LOC_TO_BRANCH,
    download_innervex_data,
)

logger = logging.getLogger("sequential_pipeline")

IST = timezone(timedelta(hours=5, minutes=30))

DATES_TO_PROCESS = [
    date(2026, 11, 6),
    date(2026, 11, 7),
    date(2026, 11, 8),
    date(2026, 11, 9),
]

FINAL_REPORT_DATE = date(2026, 11, 9)


def execute_sequential_pipeline(dry_run: bool = False, force_delivery: bool = False) -> int:
    pipeline_start = time.perf_counter()
    print("=" * 80)
    print("PSM CHRONOLOGICAL PIPELINE EXECUTION: 06-NOV TO 09-NOV-2026")
    print(f"Final Target Report Date: {FINAL_REPORT_DATE.strftime('%d-%b-%Y')}")
    print(f"Delivery Mode:            {'DRY RUN (Simulated)' if dry_run else 'LIVE PRODUCTION DISPATCH'}")
    print("=" * 80)

    config_bundle = load_config()
    tg_config = config_bundle.app.telegram
    channel = TelegramDeliveryChannel(config=tg_config, dry_run=dry_run, chat_ids=tg_config.chat_ids)
    snap_mgr = SnapshotManager(project_root / "data" / "snapshots")
    loc_resolver = LocationResolver(config_bundle.mappings)

    # 1. Initialize Read-Only Google Sheets Service
    sa_path = str(project_root / "secrets" / "service_account.json")
    sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")
    digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")

    # 2. Dynamic Monthly Source Discovery
    print(f"\n[Stage 1: Dynamic Monthly Discovery] Resolving November 2026 worksheets...")
    discovery = MonthlySourceDiscovery(sheets)
    source_info = discovery.discover_sources(
        report_date=FINAL_REPORT_DATE,
        enrollment_sheet_id=enr_id,
        closing_sheet_id=close_id,
        digi_sheet_id=digi_id,
        validate_headers=True,
    )
    print(f"  [OK] SS Tab:       '{source_info.ss_tab_name}'")
    print(f"  [OK] SV Tab:       '{source_info.sv_tab_name}'")
    print(f"  [OK] Closing Tab:  '{source_info.closing_tab_name}'")
    print(f"  [OK] Digi Raw Tab: '{source_info.digi_raw_tab_name}'")
    if source_info.digi_pivot_tab_name:
        print(f"  [OK] Digi Pivot:   '{source_info.digi_pivot_tab_name}'")

    # 3. Read Google Sheets Baselines
    print(f"\n[Stage 2: Sheets Baselines] Reading live November sheets...")
    sheet_ss = sheets.read_range(enr_id, f"'{source_info.ss_tab_name}'!A2:N")
    sheet_sv = sheets.read_range(enr_id, f"'{source_info.sv_tab_name}'!A2:N")
    sheet_close = sheets.read_range(close_id, f"'{source_info.closing_tab_name}'!A2:H")
    print(f"  Sheet SS Total Rows:    {len(sheet_ss):,}")
    print(f"  Sheet SV Total Rows:    {len(sheet_sv):,}")
    print(f"  Sheet Close Total Rows: {len(sheet_close):,}")

    # Ensure baseline snapshot for Nov 5
    baseline_close_records = [r for r in sheet_close if parse_date_safe(r[0] if len(r)>0 else "") and parse_date_safe(r[0]) <= date(2026, 11, 5)]
    snap_05 = SourceSnapshot(
        snapshot_id=f"snap_closing_20261105_baseline",
        category="closing",
        report_date=date(2026, 11, 5),
        source_identifier=f"{close_id}::'{source_info.closing_tab_name}'!A2:H",
        row_count=len(baseline_close_records),
        sha256=compute_records_sha256(baseline_close_records),
        records=baseline_close_records,
    )
    snap_mgr.save_snapshot(snap_05, overwrite=True)
    print(f"  Saved 05-Nov Closing Baseline Snapshot: {len(baseline_close_records):,} rows (ID: {snap_05.snapshot_id})")

    digi_reader = DigiSourceReader(sheets, digi_id)
    raw_digi_tab = source_info.digi_raw_tab_name

    # Pre-read Digi rows once (single-pass efficiency)
    print(f"  Reading Digi Raw rows from '{raw_digi_tab}'...")
    all_digi_rows = sheets.read_range(digi_id, f"'{raw_digi_tab}'!A2:R")
    print(f"  Total Digi Raw Rows loaded: {len(all_digi_rows):,}")

    # Accumulator states
    accumulated_ss: List[Dict[str, Any]] = []
    accumulated_sv: List[Dict[str, Any]] = []
    seen_ss_keys = set()
    seen_sv_keys = set()

    # Seed SS & SV with records <= 05-Nov from sheet
    for r in sheet_ss:
        dt = parse_date_safe(r[5] if len(r) > 5 else "")
        if dt and dt <= date(2026, 11, 5):
            msno = str(r[3] if len(r) > 3 else "").strip()
            key = (msno, dt.isoformat())
            if key not in seen_ss_keys:
                seen_ss_keys.add(key)
                accumulated_ss.append({
                    "date": dt,
                    "location2": (r[6] if len(r) > 6 else "").strip().upper(),
                    "amount": float((r[8] if len(r) > 8 else "0").replace(",", "").strip() or 0.0),
                    "mobile": normalize_mobile(r[9] if len(r) > 9 else ""),
                    "msno": msno,
                    "name": r[4] if len(r) > 4 else "",
                })

    for r in sheet_sv:
        dt = parse_date_safe(r[5] if len(r) > 5 else "")
        if dt and dt <= date(2026, 11, 5):
            msno = str(r[3] if len(r) > 3 else "").strip()
            key = (msno, dt.isoformat())
            if key not in seen_sv_keys:
                seen_sv_keys.add(key)
                accumulated_sv.append({
                    "date": dt,
                    "location2": (r[6] if len(r) > 6 else "").strip().upper(),
                    "amount": float((r[8] if len(r) > 8 else "0").replace(",", "").strip() or 0.0),
                    "mobile": normalize_mobile(r[9] if len(r) > 9 else ""),
                    "msno": msno,
                    "name": r[4] if len(r) > 4 else "",
                })

    print(f"  Initialized Base SS (<= 05-Nov): {len(accumulated_ss):,} records")
    print(f"  Initialized Base SV (<= 05-Nov): {len(accumulated_sv):,} records")

    # Closing state starts with baseline
    accumulated_closing = list(baseline_close_records)
    seen_close_keys = set()
    for r in accumulated_closing:
        pb = str(r[2] if len(r) > 2 else "").strip()
        dt = str(r[0] if len(r) > 0 else "").strip()
        sh = str(r[7] if len(r) > 7 else "").strip()
        if pb:
            seen_close_keys.add((pb, dt, sh))

    # Processing audit results
    processing_results: Dict[date, Dict[str, Any]] = {}

    target_branches = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KANCHI", "NGL", "ONLINE"]
    rev_targets = {
        "TVL": 40000000, "CPT": 72000000, "TVM": 27000000, "TPJ": 40000000, "CBE": 30000000,
        "SLM": 30000000, "PNM": 20000000, "PADI": 54000000, "KANCHI": 27000000, "NGL": None, "ONLINE": None
    }
    cnt_targets = {
        "TVL": 3000, "CPT": 6000, "TVM": 4000, "TPJ": 5000, "CBE": 7000,
        "SLM": 3000, "PNM": 2000, "PADI": 6000, "KANCHI": None, "NGL": None, "ONLINE": None
    }

    final_payload: Dict[str, Any] = {}

    # -------------------------------------------------------------------------
    # 4. Sequential Chronological Loop: 06-Nov -> 07-Nov -> 08-Nov -> 09-Nov
    # -------------------------------------------------------------------------
    for curr_date in DATES_TO_PROCESS:
        curr_str = curr_date.strftime("%d-%b-%Y")
        curr_compact = curr_date.strftime("%Y%m%d")
        print("\n" + "-" * 80)
        print(f"PROCESSING DATE: {curr_str} ({curr_date.isoformat()})")
        print("-" * 80)

        # A. Innervex Raw Data
        ss_day_data, sv_day_data, close_day_rows, was_cached = download_innervex_data(
            config_bundle, curr_date, force_download=False
        )
        print(f"  Innervex Raw Data: SS={len(ss_day_data):,}, SV={len(sv_day_data):,}, Close={len(close_day_rows):,} (Cached={was_cached})")

        # B. Digi Readiness Check
        is_ready, count_for_date, max_dt, _ = digi_reader.check_readiness_and_fetch(
            curr_date, raw_digi_tab
        )
        if not is_ready or count_for_date == 0:
            raise RuntimeError(f"Digi data for {curr_str} is not ready or missing (count={count_for_date}, latest={max_dt})")
        print(f"  Digi Readiness: [PASS] {count_for_date:,} records confirmed for {curr_str}")

        branch_dg, branch_ds = digi_reader.extract_raw_counts(
            raw_digi_tab, curr_date, raw_rows=all_digi_rows
        )
        print(f"  Digi Cumulative Counts: Gold Total={sum(branch_dg.values()):,}, Silver Total={sum(branch_ds.values()):,}")

        # C. Accumulate Innervex SS & SV for curr_date
        new_ss_count = 0
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
                accumulated_ss.append({
                    "date": dt,
                    "location2": loc2,
                    "amount": amt,
                    "mobile": mob,
                    "msno": msno,
                    "name": str(item.get("NAME") or ""),
                })
                new_ss_count += 1

        # Also merge any sheet_ss rows for curr_date not yet seen
        for r in sheet_ss:
            dt = parse_date_safe(r[5] if len(r) > 5 else "")
            if dt and dt == curr_date:
                msno = str(r[3] if len(r) > 3 else "").strip()
                key = (msno, dt.isoformat())
                if key not in seen_ss_keys:
                    seen_ss_keys.add(key)
                    accumulated_ss.append({
                        "date": dt,
                        "location2": (r[6] if len(r) > 6 else "").strip().upper(),
                        "amount": float((r[8] if len(r) > 8 else "0").replace(",", "").strip() or 0.0),
                        "mobile": normalize_mobile(r[9] if len(r) > 9 else ""),
                        "msno": msno,
                        "name": r[4] if len(r) > 4 else "",
                    })
                    new_ss_count += 1

        new_sv_count = 0
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
                accumulated_sv.append({
                    "date": dt,
                    "location2": loc2,
                    "amount": amt,
                    "mobile": mob,
                    "msno": msno,
                    "name": str(item.get("NAME") or ""),
                })
                new_sv_count += 1

        for r in sheet_sv:
            dt = parse_date_safe(r[5] if len(r) > 5 else "")
            if dt and dt == curr_date:
                msno = str(r[3] if len(r) > 3 else "").strip()
                key = (msno, dt.isoformat())
                if key not in seen_sv_keys:
                    seen_sv_keys.add(key)
                    accumulated_sv.append({
                        "date": dt,
                        "location2": (r[6] if len(r) > 6 else "").strip().upper(),
                        "amount": float((r[8] if len(r) > 8 else "0").replace(",", "").strip() or 0.0),
                        "mobile": normalize_mobile(r[9] if len(r) > 9 else ""),
                        "msno": msno,
                        "name": r[4] if len(r) > 4 else "",
                    })
                    new_sv_count += 1

        print(f"  Enrollment Additions for {curr_str}: SS added={new_ss_count}, SV added={new_sv_count}")
        print(f"  Accumulated Month-to-Date: SS={len(accumulated_ss):,} rows, SV={len(accumulated_sv):,} rows")

        # D. Accumulate Closing rows for curr_date
        new_close_count = 0
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
                accumulated_closing.append(row_arr)
                new_close_count += 1

        print(f"  Closing Additions for {curr_str}: Closed accounts added={new_close_count}")
        print(f"  Accumulated Closing Total: {len(accumulated_closing):,} records")

        # Save immutable snapshot for curr_date
        curr_snap = SourceSnapshot(
            snapshot_id=f"snap_closing_{curr_compact}",
            category="closing",
            report_date=curr_date,
            source_identifier=f"{close_id}::'{source_info.closing_tab_name}'!A2:H+Innervex",
            row_count=len(accumulated_closing),
            sha256=compute_records_sha256(accumulated_closing),
            records=accumulated_closing,
        )
        snap_mgr.save_snapshot(curr_snap, overwrite=True)
        print(f"  Saved Snapshot {curr_snap.snapshot_id} (SHA-256: {curr_snap.sha256[:12]}..., Rows: {curr_snap.row_count:,})")

        # E. Calculations: Enrollment Tables
        table1_rows = []
        table2_rows = []
        for b in target_branches:
            matching_locs = [loc for loc, b_code in LOC_TO_BRANCH.items() if b_code == b]
            ss_recs = [r for r in accumulated_ss if r["location2"] in matching_locs and r["date"] <= curr_date]
            sv_recs = [r for r in accumulated_sv if r["location2"] in matching_locs and r["date"] <= curr_date]
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
                "avg": round(tot_cnt / curr_date.day),
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
            "avg": round(sum(r["total"] for r in table2_rows) / curr_date.day),
        }

        # F. Rejoining Matching Engine
        rejoining_ss_input = [
            {"schdate": r["date"], "mobile": r["mobile"], "amount": r["amount"], "showroom": r["location2"], "scheme": "SS", "msno": r["msno"], "name": r["name"]}
            for r in accumulated_ss if r["date"] <= curr_date
        ]
        rejoining_sv_input = [
            {"schdate": r["date"], "mobile": r["mobile"], "amount": r["amount"], "showroom": r["location2"], "scheme": "SV", "msno": r["msno"], "name": r["name"]}
            for r in accumulated_sv if r["date"] <= curr_date
        ]

        matcher = RejoiningMatchingEngine(
            cutoff_date=curr_date,
            require_enrollment_after_closure=False,
            mode="month_to_date",
            snapshot_id=curr_snap.snapshot_id,
            snapshot_hash=curr_snap.sha256,
        )
        rejoin_res = matcher.process(
            closed_records=accumulated_closing,
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
            sh_sv = sum(cnt for sch, cnt in r.scheme_counts.items() if "VIRUKSHAM" in sch.upper() or sch.upper().startswith("SV"))
            sh_ss = sum(cnt for sch, cnt in r.scheme_counts.items() if not ("VIRUKSHAM" in sch.upper() or sch.upper().startswith("SV")))
            rejoin_scheme.append({"showroom": r.showroom, "ss": sh_ss, "sv": sh_sv})
        rejoin_scheme_total = {
            "ss": sum(r["ss"] for r in rejoin_scheme),
            "sv": sum(r["sv"] for r in rejoin_scheme),
        }

        # G. Validations for curr_date
        val_errors = []
        if tot_rev_target != 340000000:
            val_errors.append(f"Target revenue ₹{tot_rev_target:,} != ₹340,000,000")
        if rejoin_table1_total["closed"] <= 0:
            val_errors.append(f"Closed count {rejoin_table1_total['closed']} <= 0")
        if rejoin_weight_total["weight"] <= 0:
            val_errors.append(f"Closed weight {rejoin_weight_total['weight']} <= 0")
        if rejoin_scheme_total["ss"] + rejoin_scheme_total["sv"] != rejoin_table1_total["closed"]:
            val_errors.append("Scheme split mismatch")
        if rejoin_res.unresolved_physical_showrooms:
            val_errors.append(f"Unresolved physical showroom records found in closing: {rejoin_res.unresolved_physical_showrooms}")
        if val_errors:
            raise RuntimeError(f"Validation failed for {curr_str}: {'; '.join(val_errors)}")

        print(f"  Validations: [PASS] Target ₹{tot_rev_target:,}, Closed={rejoin_table1_total['closed']:,}, Weight={rejoin_weight_total['weight']:,.2f}g")
        print(f"  Summary Metrics for {curr_str}:")
        print(f"    Total Revenue Achieved: ₹{table1_total['total']:,.2f} ({table1_total['ach_pct']}%)")
        print(f"    Total Count Achieved:   {table2_total['total']:,} (SS={table2_total['ss']:,}, SV={table2_total['sv']:,}, DG={table2_total['dg']:,}, DS={table2_total['ds']:,})")
        print(f"    Scheme Rejoining:       Total Closed={rejoin_table1_total['closed']:,}, Total Rejoin={rejoin_table1_total['total_rejoin']:,} ({rejoin_table1_total['rejoin_pct']}%)")

        processing_results[curr_date] = {
            "status": "SUCCESS",
            "enr_total_rev": table1_total["total"],
            "enr_total_cnt": table2_total["total"],
            "closing_total": rejoin_table1_total["closed"],
            "rejoin_total": rejoin_table1_total["total_rejoin"],
            "rejoin_pct": rejoin_table1_total["rejoin_pct"],
        }

        if curr_date == FINAL_REPORT_DATE:
            final_payload = {
                "table1_rows": table1_rows,
                "table1_total": table1_total,
                "table2_rows": table2_rows,
                "table2_total": table2_total,
                "rejoin_table1": rejoin_table1,
                "rejoin_table1_total": rejoin_table1_total,
                "rejoin_weight": rejoin_weight,
                "rejoin_weight_total": rejoin_weight_total,
                "rejoin_scheme": rejoin_scheme,
                "rejoin_scheme_total": rejoin_scheme_total,
            }

    # -------------------------------------------------------------------------
    # 5. Generate ONLY the Two Final Report Images for 09-Nov-2026
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("STAGE 5: GENERATING FINAL EXECUTIVE REPORT IMAGES FOR 09-NOV-2026")
    print("=" * 80)

    img_dir = project_root / "data" / "screenshots"
    img_dir.mkdir(parents=True, exist_ok=True)
    out_enr_path = img_dir / "enrollment_report_20261109.png"
    out_close_path = img_dir / "closing_rejoining_report_20261109.png"

    render_enrollment_report_image(
        FINAL_REPORT_DATE,
        final_payload["table1_rows"],
        final_payload["table1_total"],
        final_payload["table2_rows"],
        final_payload["table2_total"],
        out_enr_path,
    )
    render_closing_report_image(
        FINAL_REPORT_DATE,
        final_payload["rejoin_table1"],
        final_payload["rejoin_table1_total"],
        final_payload["rejoin_weight"],
        final_payload["rejoin_weight_total"],
        final_payload["rejoin_scheme"],
        final_payload["rejoin_scheme_total"],
        out_close_path,
    )

    print(f"  [OK] Generated Final Enrollment Report: {out_enr_path.name} ({out_enr_path.stat().st_size:,} bytes)")
    print(f"  [OK] Generated Final Closing Report:    {out_close_path.name} ({out_close_path.stat().st_size:,} bytes)")

    # -------------------------------------------------------------------------
    # 6. Telegram Delivery to Both Recipients (Barath & Marketing)
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("STAGE 6: TELEGRAM DELIVERY FOR 09-NOV-2026 (TWO RECIPIENTS)")
    print("=" * 80)

    recipients = channel.configured_chat_ids
    print(f"Delivering to {len(recipients)} authorized recipients: {recipients}")

    cap_enr = "POTHYS SWARNA MAHAL\nDaily New Enrollment Report — 09-Nov-2026"
    cap_rej = "POTHYS SWARNA MAHAL\nDaily Scheme Rejoining Report — 09-Nov-2026"

    delivery_results: Dict[str, Dict[str, Any]] = {}
    for r in recipients:
        masked_r = redact_chat_id(r)
        if not force_delivery and channel._history.is_already_delivered(FINAL_REPORT_DATE, r, out_enr_path.name) and channel._history.is_already_delivered(FINAL_REPORT_DATE, r, out_close_path.name):
            print(f"  Recipient {masked_r}: [SKIPPED] Reports already delivered for 09-Nov-2026.")
            delivery_results[r] = {"status": "ALREADY_DELIVERED", "photo1": "SKIPPED", "photo2": "SKIPPED", "msg1": "ALREADY_DELIVERED", "msg2": "ALREADY_DELIVERED"}
            continue

        res1 = channel.send_photo(photo_path=out_enr_path, caption=cap_enr, recipient=r, report_date=FINAL_REPORT_DATE, force=force_delivery)
        res2 = channel.send_photo(photo_path=out_close_path, caption=cap_rej, recipient=r, report_date=FINAL_REPORT_DATE, force=force_delivery)
        delivery_results[r] = {
            "status": "SENT" if (res1.status == DeliveryStatus.SENT and res2.status == DeliveryStatus.SENT) else "FAILED",
            "photo1": res1.status.value,
            "photo2": res2.status.value,
            "msg1": res1.message_id or res1.error_message,
            "msg2": res2.message_id or res2.error_message,
        }
        print(f"  Recipient {masked_r}:")
        print(f"    Photo 1 (Enrollment): {res1.status.value} (Message ID: {res1.message_id or res1.error_message})")
        print(f"    Photo 2 (Closing):    {res2.status.value} (Message ID: {res2.message_id or res2.error_message})")

    # -------------------------------------------------------------------------
    # 7. Final Execution Summary Report
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("CHRONOLOGICAL EXECUTION & DELIVERY SUMMARY REPORT")
    print("=" * 80)
    print("1. Dates Processed:")
    for d, res in processing_results.items():
        print(f"   - {d.strftime('%d-%b-%Y')}: {res['status']} | Revenue=₹{res['enr_total_rev']:,.2f} | Count={res['enr_total_cnt']:,} | Closed={res['closing_total']:,} | Rejoin={res['rejoin_total']:,} ({res['rejoin_pct']}%)")

    print("\n2. Source Sheets & Tabs Used:")
    print(f"   - Enrollment SS Tab: '{source_info.ss_tab_name}'")
    print(f"   - Enrollment SV Tab: '{source_info.sv_tab_name}'")
    print(f"   - Closing Tab:       '{source_info.closing_tab_name}'")
    print(f"   - Digi Raw Tab:      '{source_info.digi_raw_tab_name}'")

    print("\n3. Final 09-Nov-2026 Totals:")
    print(f"   - Target Revenue:    ₹{final_payload['table1_total']['target']:,}")
    print(f"   - Achieved Revenue:  ₹{final_payload['table1_total']['total']:,.2f} ({final_payload['table1_total']['ach_pct']}%)")
    print(f"   - Subhiksham (SS):   ₹{final_payload['table1_total']['ss']:,.2f}")
    print(f"   - Viruksham (SV):    ₹{final_payload['table1_total']['sv']:,.2f}")
    print(f"   - Achieved Counts:   Total={final_payload['table2_total']['total']:,} (SS={final_payload['table2_total']['ss']:,}, SV={final_payload['table2_total']['sv']:,}, DG={final_payload['table2_total']['dg']:,}, DS={final_payload['table2_total']['ds']:,})")
    print(f"   - Scheme Closing:    Closed Accounts={final_payload['rejoin_table1_total']['closed']:,}")
    print(f"   - Closed Weight:     {final_payload['rejoin_weight_total']['weight']:,.2f}g")
    print(f"   - Total Rejoining:   {final_payload['rejoin_table1_total']['total_rejoin']:,} ({final_payload['rejoin_table1_total']['rejoin_pct']}%)")
    print(f"   - Rejoining SS:      {final_payload['rejoin_table1_total']['rejoin_ss']:,}")
    print(f"   - Rejoining SV:      {final_payload['rejoin_table1_total']['rejoin_sv']:,}")

    print("\n4. Final Generated PNGs:")
    print(f"   - {out_enr_path.name}")
    print(f"   - {out_close_path.name}")

    print("\n5. Telegram Deliveries:")
    recipient_labels = {"1688169109": "Barath", "9132692037": "Marketing"}
    for r, info in delivery_results.items():
        name = recipient_labels.get(r, "Authorized Recipient")
        print(f"   - {name} ({r}):")
        print(f"       Enrollment Report Photo: {info.get('photo1')} | Message ID: {info.get('msg1')}")
        print(f"       Closing Report Photo:    {info.get('photo2')} | Message ID: {info.get('msg2')}")
        print(f"       Overall Status:          {info.get('status')}")

    total_time = time.perf_counter() - pipeline_start
    print(f"\nTotal Execution Time: {total_time:.2f}s ({total_time/60:.2f} minutes)")
    print("=" * 80)

    all_delivered = all(r_info["status"] in ("SENT", "ALREADY_DELIVERED") for r_info in delivery_results.values())
    return 0 if all_delivered else 1


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Simulate Telegram delivery without calling API")
    parser.add_argument("--force-delivery", action="store_true", help="Force delivery even if already recorded in history")
    args = parser.parse_args()

    rc = execute_sequential_pipeline(dry_run=args.dry_run, force_delivery=args.force_delivery)
    sys.exit(rc)
