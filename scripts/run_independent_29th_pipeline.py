"""
Independent Pipeline Execution & Automatic Telegram Delivery — 29 October 2026.

Architecture:
READ-ONLY SOURCE DATA -> INDEPENDENT PIPELINE CALCULATION -> LOCAL EXCEL-STYLE IMAGE GENERATION -> VALIDATION -> AUTOMATIC TELEGRAM DELIVERY

- STRICT_READ_ONLY=true enforced throughout.
- Production Google Sheets remain 100% untouched.
- Excel-style report images generated directly from calculated results.
- Automatic Telegram delivery of Photo 1 then Photo 2.
"""

import os
import sys
import json
import csv
import argparse
from pathlib import Path
from datetime import date
from collections import defaultdict

# Ensure UTF-8 output on Windows console
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from app.core.config import load_config
from app.google_sheets.client import GoogleApiSheetsService
from app.processing.location import LocationResolver
from app.processing.rejoining import (
    RejoiningMatchingEngine,
    parse_date_safe,
    normalize_mobile,
    map_showroom_label,
)
from app.reporting.image_renderer import (
    render_enrollment_report_image,
    render_closing_report_image,
)
from app.delivery.telegram import (
    TelegramDeliveryChannel,
    format_telegram_caption,
    redact_chat_id,
)
from app.repositories.snapshots import SnapshotManager
from app.delivery.base import DeliveryStatus

# Comprehensive showroom and location alias mapping to canonical branch codes
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

def run_independent_29th_pipeline(dry_run: bool = False):
    target_date = date(2026, 10, 29)
    target_date_str = target_date.strftime("%d/%m/%Y")
    
    print("=" * 75)
    print("INDEPENDENT DAILY REPORTING PIPELINE — 29 OCTOBER 2026")
    print("=" * 75)
    print(f"Target Report Date: {target_date_str} ({target_date.isoformat()})")
    print("Pipeline Mode:      STRICT_READ_ONLY=true (Zero production sheet mutations)")
    print("=" * 75)

    # 1. Source Data Loading (Read-Only)
    print("\n>>> STEP 1: Reading raw source records...")
    raw_cache_dir = project_root / "data" / "raw" / "2026" / "10" / "29"
    with open(raw_cache_dir / "ss_29_data.json", "r", encoding="utf-8") as f:
        ss_29_data = json.load(f)
    with open(raw_cache_dir / "sv_29_data.json", "r", encoding="utf-8") as f:
        sv_29_data = json.load(f)
    with open(raw_cache_dir / "close_29_data.json", "r", encoding="utf-8") as f:
        close_29_rows = json.load(f)

    print(f"  Innervex SS (29-Oct):       {len(ss_29_data)} records")
    print(f"  Innervex SV (29-Oct):       {len(sv_29_data)} records")
    print(f"  Innervex Closing (29-Oct):  {len(close_29_rows)} records")

    config_bundle = load_config()
    sa_path = str(project_root / "secrets" / "service_account.json")
    sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")
    digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")

    sheet_ss = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
    sheet_sv = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
    sheet_close = sheets.read_range(close_id, "'October - 2026'!A2:H")

    # Snapshot management for immutable source reproduction
    snap_mgr = SnapshotManager(project_root / "data" / "snapshots")
    close_snapshot = snap_mgr.get_or_create_snapshot(
        category="closing",
        report_date=target_date,
        records=sheet_close,
        source_identifier=f"{close_id}::'October - 2026'!A2:H",
    )
    sheet_close_records = close_snapshot.records

    print(f"  Source SS Rows:             {len(sheet_ss)} rows")
    print(f"  Source SV Rows:             {len(sheet_sv)} rows")
    print(f"  Closing Snapshot Rows:      {len(sheet_close_records)} rows (ID: {close_snapshot.snapshot_id}, SHA: {close_snapshot.sha256[:10]}...)")

    # 2. Location Resolver Setup
    loc_resolver = LocationResolver(config_bundle.mappings)

    # Combine Subhiksham (SS): Take historical strictly before target_date, plus fresh 29th
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

    for item in ss_29_data:
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

    # Combine Viruksham (SV): Take historical strictly before target_date, plus fresh 29th
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

    for item in sv_29_data:
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

    print(f"\n  [OK] Total Combined SS Rows: {len(combined_ss)}")
    print(f"  [OK] Total Combined SV Rows: {len(combined_sv)}")

    # 3. DigiGold & DigiSilver: Independently calculated directly from raw transaction records in 'OCT'
    print("\n>>> STEP 3: Independently calculating Digi counts from raw 'OCT' records...")
    from app.reporting.digi import DigiSourceReader
    digi_reader = DigiSourceReader(sheets, digi_id)
    branch_dg, branch_ds = digi_reader.extract_raw_counts("OCT", target_date)
    print(f"  [OK] DigiGold Raw Calculations:   E COMM={branch_dg.get('ONLINE', 0):,}, Total={sum(branch_dg.values()):,}")
    print(f"  [OK] DigiSilver Raw Calculations: E COMM={branch_ds.get('ONLINE', 0):,}, Total={sum(branch_ds.values()):,}")

    # 4. Independent Aggregations for Enrollment Report
    target_branches = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KANCHI", "NGL", "ONLINE"]
    rev_targets = {
        "TVL": 40000000, "CPT": 72000000, "TVM": 27000000, "TPJ": 40000000,
        "CBE": 30000000, "SLM": 30000000, "PNM": 20000000, "PADI": 54000000,
        "KANCHI": 27000000, "NGL": None, "ONLINE": None,
    }
    cnt_targets = {
        "TVL": 3000, "CPT": 6000, "TVM": 4000, "TPJ": 5000,
        "CBE": 7000, "SLM": 3000, "PNM": 2000, "PADI": 6000,
        "KANCHI": None, "NGL": None, "ONLINE": None,
    }

    loc_to_branch = LOC_TO_BRANCH

    # Validation: Ensure zero unmapped records are dropped
    unmapped_ss = [r for r in combined_ss if r["location2"] not in loc_to_branch]
    unmapped_sv = [r for r in combined_sv if r["location2"] not in loc_to_branch]
    if unmapped_ss:
        raise ValueError(f"Unmapped Subhiksham locations detected: {[r['location2'] for r in unmapped_ss[:5]]}")
    if unmapped_sv:
        raise ValueError(f"Unmapped Viruksham locations detected: {[r['location2'] for r in unmapped_sv[:5]]}")

    table1_rows = []
    table2_rows = []

    for b in target_branches:
        matching_locs = [loc for loc, b_code in loc_to_branch.items() if b_code == b]
        ss_recs = [r for r in combined_ss if r["location2"] in matching_locs]
        sv_recs = [r for r in combined_sv if r["location2"] in matching_locs]

        ss_amt = sum(r["amount"] for r in ss_recs)
        sv_amt = sum(r["amount"] for r in sv_recs)
        tot_amt = ss_amt + sv_amt

        tgt_val = rev_targets[b]
        yet_val = (tot_amt - tgt_val) if tgt_val else tot_amt
        ach_pct = round((tot_amt / tgt_val) * 100) if tgt_val else None

        table1_rows.append({
            "branch": b,
            "target": tgt_val,
            "ss": ss_amt,
            "sv": sv_amt,
            "total": tot_amt,
            "yet": yet_val,
            "ach_pct": ach_pct,
        })

        ss_cnt = len(ss_recs)
        sv_cnt = len(sv_recs)
        dg_cnt = branch_dg.get(b, 0)
        ds_cnt = branch_ds.get(b, 0)
        tot_cnt = ss_cnt + sv_cnt + dg_cnt + ds_cnt
        avg_cnt = round(tot_cnt / 29)

        table2_rows.append({
            "branch": "E COMM" if b == "ONLINE" else b,
            "target": cnt_targets[b],
            "ss": ss_cnt,
            "sv": sv_cnt,
            "dg": dg_cnt,
            "ds": ds_cnt,
            "total": tot_cnt,
            "avg": avg_cnt,
        })

    tot_rev_target = 313000000
    tot_ss_rev = sum(r["ss"] for r in table1_rows)
    tot_sv_rev = sum(r["sv"] for r in table1_rows)
    tot_comb_rev = tot_ss_rev + tot_sv_rev
    # Grand total Yet to Achieve is calculated by summing branch-level Yet to Achieve values.
    # This properly accounts for KANCHI's target of ₹27M (-₹10,166,916), negative yet values, and non-targeted branches.
    tot_yet_rev = sum(r["yet"] for r in table1_rows)
    tot_ach_rev_pct = round((tot_comb_rev / tot_rev_target) * 100)

    table1_total = {
        "target": tot_rev_target,
        "ss": tot_ss_rev,
        "sv": tot_sv_rev,
        "total": tot_comb_rev,
        "yet": tot_yet_rev,
        "ach_pct": tot_ach_rev_pct,
    }

    tot_cnt_target = 36000
    tot_ss_cnt = sum(r["ss"] for r in table2_rows)
    tot_sv_cnt = sum(r["sv"] for r in table2_rows)
    tot_dg_cnt = sum(r["dg"] for r in table2_rows)
    tot_ds_cnt = sum(r["ds"] for r in table2_rows)
    tot_comb_cnt = tot_ss_cnt + tot_sv_cnt + tot_dg_cnt + tot_ds_cnt
    tot_avg = round(tot_comb_cnt / 29)

    table2_total = {
        "target": tot_cnt_target,
        "ss": tot_ss_cnt,
        "sv": tot_sv_cnt,
        "dg": tot_dg_cnt,
        "ds": tot_ds_cnt,
        "total": tot_comb_cnt,
        "avg": tot_avg,
    }

    print("\n--- TABLE 1: REVENUE (29/10/2026) ---")
    print(f"Total SS Revenue: ₹{tot_ss_rev:,.2f}")
    print(f"Total SV Revenue: ₹{tot_sv_rev:,.2f}")
    print(f"Total Revenue:    ₹{tot_comb_rev:,.2f} ({tot_ach_rev_pct}%)")

    print("\n--- TABLE 2: ENROLLMENT COUNTS (29/10/2026) ---")
    print(f"Total SS Count:   {tot_ss_cnt:,}")
    print(f"Total SV Count:   {tot_sv_cnt:,}")
    print(f"Total DG Count:   {tot_dg_cnt:,}")
    print(f"Total DS Count:   {tot_ds_cnt:,}")
    print(f"Grand Total:      {tot_comb_cnt:,} (Daily AVG: {tot_avg:,})")

    # 5. Independent Scheme Rejoining Calculation
    print("\n>>> STEP 5: Calculating Scheme Rejoining Report for 29/10/2026...")
    print(f"  Using immutable source snapshot: {close_snapshot.snapshot_id} (SHA: {close_snapshot.sha256[:12]}...)")
    combined_closing = []
    seen_close_keys = set()
    for r in sheet_close_records:
        pb = str(r[2] if len(r) > 2 else "").strip()
        dt = str(r[0] if len(r) > 0 else "").strip()
        sh = str(r[7] if len(r) > 7 else "").strip()
        combined_closing.append(r)
        if pb:
            seen_close_keys.add((pb, dt, sh))

    for row in close_29_rows:
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

    rejoining_ss_input = [{
        "schdate": r["date"],
        "mobile": r["mobile"],
        "amount": r["amount"],
        "showroom": r["location2"],
        "scheme": "SS",
        "msno": r["msno"],
        "name": r["name"],
    } for r in combined_ss]

    rejoining_sv_input = [{
        "schdate": r["date"],
        "mobile": r["mobile"],
        "amount": r["amount"],
        "showroom": r["location2"],
        "scheme": "SV",
        "msno": r["msno"],
        "name": r["name"],
    } for r in combined_sv]

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

    rejoin_table1 = []
    counts_map = {}
    for r in rejoin_res.table1_rows:
        rejoin_table1.append({
            "showroom": r.showroom,
            "closed": r.closed_count,
            "rejoin_ss": r.rejoin_ss,
            "rejoin_sv": r.rejoin_sv,
            "total_rejoin": r.total_rejoin,
            "rejoin_pct": r.rejoin_percentage,
        })
        counts_map[r.showroom] = r.closed_count

    rejoin_table1_total = {
        "closed": rejoin_res.total_closed_count,
        "rejoin_ss": rejoin_res.total_rejoin_ss,
        "rejoin_sv": rejoin_res.total_rejoin_sv,
        "total_rejoin": rejoin_res.total_rejoin_count,
        "rejoin_pct": rejoin_res.overall_rejoin_percentage,
    }

    rejoin_weight = []
    for r in rejoin_res.table2_weight_rows:
        rejoin_weight.append({
            "showroom": r.showroom,
            "count": counts_map.get(r.showroom, 0),
            "weight": r.closed_weight,
        })

    rejoin_weight_total = {
        "count": rejoin_res.total_closed_count,
        "weight": rejoin_res.total_closed_weight,
    }

    rejoin_scheme = []
    for r in rejoin_res.table3_scheme_rows:
        sh_ss = sum(cnt for sch, cnt in r.scheme_counts.items() if "SUBHIKSHAM" in sch.upper() or "SUBIKSHAM" in sch.upper() or "LABHAM" in sch.upper() or sch.upper().startswith("SS"))
        sh_sv = sum(cnt for sch, cnt in r.scheme_counts.items() if "VIRUKSHAM" in sch.upper() or sch.upper().startswith("SV"))
        rejoin_scheme.append({
            "showroom": r.showroom,
            "ss": sh_ss,
            "sv": sh_sv,
        })

    tot_close_ss = sum(r["ss"] for r in rejoin_scheme)
    tot_close_sv = sum(r["sv"] for r in rejoin_scheme)
    rejoin_scheme_total = {
        "ss": tot_close_ss,
        "sv": tot_close_sv,
    }

    print(f"  Closed Accounts (Physical Showrooms): {rejoin_table1_total['closed']:,}")
    print(f"  Rejoined Members:                     {rejoin_table1_total['total_rejoin']:,} ({rejoin_table1_total['rejoin_pct']:.2f}%)")
    print(f"  Closed Weight:                         {rejoin_weight_total['weight']:,.2f} grams")
    print(f"  Closed SS / SV Split:                 SS={rejoin_scheme_total['ss']:,} | SV={rejoin_scheme_total['sv']:,}")

    # 6. Render Excel-Style Report Images
    print("\n>>> STEP 6: Generating Excel-Style Report Images directly from calculated results...")
    img_dir = project_root / "data" / "screenshots"
    img_dir.mkdir(parents=True, exist_ok=True)
    out_enr_path = img_dir / "enrollment_report_20261029.png"
    out_close_path = img_dir / "closing_rejoining_report_20261029.png"

    render_enrollment_report_image(target_date, table1_rows, table1_total, table2_rows, table2_total, out_enr_path)
    render_closing_report_image(target_date, rejoin_table1, rejoin_table1_total, rejoin_weight, rejoin_weight_total, rejoin_scheme, rejoin_scheme_total, out_close_path)

    # Copy to brain and test_runs directories for archiving
    brain_dir = Path(r"C:\Users\spart\.gemini\antigravity\brain\cc6f0772-2bef-483f-8b4b-98f76ce1a3e7")
    import shutil
    shutil.copy2(out_enr_path, brain_dir / "New_Enrollment_Report_2026-10-29.png")
    shutil.copy2(out_close_path, brain_dir / "Scheme_Rejoining_Report_2026-10-29.png")

    test_runs_dir = project_root / "data" / "test_runs" / "report_2026-10-29" / "screenshots"
    test_runs_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out_enr_path, test_runs_dir / "New_Enrollment_Report_2026-10-29.png")
    shutil.copy2(out_close_path, test_runs_dir / "Scheme_Rejoining_Report_2026-10-29.png")

    print(f"  [OK] Generated Enrollment Report Image: {out_enr_path} (Size: {out_enr_path.stat().st_size:,} bytes)")
    print(f"  [OK] Generated Rejoining Report Image:  {out_close_path} (Size: {out_close_path.stat().st_size:,} bytes)")

    # 7. Automatic Telegram Delivery (Photo 1 then Photo 2)
    print(f"\n>>> STEP 7: Executing Automatic Telegram Delivery (dry_run={dry_run})...")
    tg_config = config_bundle.app.telegram
    channel = TelegramDeliveryChannel(
        config=tg_config,
        enabled=True,
        dry_run=dry_run,
        chat_id=tg_config.chat_id,
        chat_ids=tg_config.chat_ids,
    )

    cap_enr = format_telegram_caption(target_date, "New Enrollment Report")
    cap_rej = format_telegram_caption(target_date, "Scheme Rejoining Report")

    target_recipients = channel.configured_chat_ids if channel.configured_chat_ids else ([tg_config.chat_id] if tg_config.chat_id else [])
    print(f"Target Recipients: {[redact_chat_id(r) for r in target_recipients]}")

    results = {}
    total_delivered = 0
    total_expected = len(target_recipients) * 2

    for recip in target_recipients:
        masked_r = redact_chat_id(recip)
        print(f"\n--- DELIVERING TO RECIPIENT: {masked_r} ---")

        print(f"Photo 1 (New Enrollment) -> {masked_r}")
        res_enr = channel.send_photo(
            photo_path=out_enr_path,
            caption=cap_enr,
            recipient=recip,
            report_date=target_date,
            force=True,
        )
        print(f"  Result: Status={res_enr.status.value}, Message ID={res_enr.message_id}")
        if res_enr.status == DeliveryStatus.SENT:
            total_delivered += 1

        print(f"Photo 2 (Scheme Rejoining) -> {masked_r}")
        res_rej = channel.send_photo(
            photo_path=out_close_path,
            caption=cap_rej,
            recipient=recip,
            report_date=target_date,
            force=True,
        )
        print(f"  Result: Status={res_rej.status.value}, Message ID={res_rej.message_id}")
        if res_rej.status == DeliveryStatus.SENT:
            total_delivered += 1

        results[recip] = {"enrollment": res_enr, "closing": res_rej}

    if total_delivered == total_expected and total_expected > 0:
        print("\n" + "=" * 75)
        print("SUCCESS: Both 29/10/2026 report images delivered automatically to all recipients!")
        for recip, recs in results.items():
            print(f"  Recipient {redact_chat_id(recip)}: Photo 1 = {recs['enrollment'].message_id}, Photo 2 = {recs['closing'].message_id}")
        print("=" * 75)
        return 0
    else:
        print(f"\nWARNING: Delivered {total_delivered}/{total_expected} photos. Some recipient deliveries may have failed.")
        return 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run independent 29th October pipeline and deliver to Telegram.")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Simulate telegram delivery")
    args = parser.parse_args()
    rc = run_independent_29th_pipeline(dry_run=args.dry_run)
    sys.exit(rc)
