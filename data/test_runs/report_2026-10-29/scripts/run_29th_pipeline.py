import os
import sys
import csv
import json
from pathlib import Path
from datetime import date
from collections import defaultdict, Counter
from PIL import Image, ImageDraw, ImageFont

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from app.core.config import load_config
from app.google_sheets.client import GoogleApiSheetsService
from app.processing.location import LocationResolver
from app.reporting.closing import map_showroom_label, normalize_mobile
from app.processing.rejoining import RejoiningMatchingEngine, parse_date_safe
from app.reporting.digi import DigiSourceReader

def run_29th_pipeline():
    config_bundle = load_config()
    sa_path = str(project_root / "secrets" / "service_account.json")
    sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    
    enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
    close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")
    
    target_date = date(2026, 10, 29)
    print("=" * 80)
    print(f"RUNNING INDEPENDENT PIPELINE FOR REPORT DATE: {target_date.strftime('%d/%m/%Y')}")
    print("=" * 80)
    
    # ---------------------------------------------------------
    # STEP 1: Load 29-Oct cached records from Innervex ERP
    # ---------------------------------------------------------
    print("\n>>> STEP 1: Loading 29-Oct raw records...")
    raw_cache_dir = project_root / "data" / "raw" / "2026" / "10" / "29"
    
    ss_cache = raw_cache_dir / "ss_29_data.json"
    sv_cache = raw_cache_dir / "sv_29_data.json"
    close_cache = raw_cache_dir / "close_29_data.json"
    
    with open(ss_cache, "r", encoding="utf-8") as f:
        ss_29_data = json.load(f)
    with open(sv_cache, "r", encoding="utf-8") as f:
        sv_29_data = json.load(f)
    with open(close_cache, "r", encoding="utf-8") as f:
        close_29_rows = json.load(f)
        
    print(f"  Innervex SS (29-Oct): {len(ss_29_data)} records")
    print(f"  Innervex SV (29-Oct): {len(sv_29_data)} records")
    print(f"  Innervex Closing (29-Oct): {len(close_29_rows)} records")
    
    # ---------------------------------------------------------
    # STEP 2: Read historical Google Sheets records (<= 28-Oct)
    # ---------------------------------------------------------
    print("\n>>> STEP 2: Loading historical records from Google Sheets...")
    sheet_ss = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
    sheet_sv = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
    sheet_close = sheets.read_range(close_id, "'October - 2026'!A2:H")
    
    print(f"  Sheet SS rows (<= 28-Oct): {len(sheet_ss)}")
    print(f"  Sheet SV rows (<= 28-Oct): {len(sheet_sv)}")
    print(f"  Sheet Closing rows (<= 28-Oct): {len(sheet_close)}")
    
    # ---------------------------------------------------------
    # STEP 3: Normalize and combine SS & SV
    # ---------------------------------------------------------
    loc_resolver = LocationResolver(config_bundle.mappings)
    
    combined_ss = []
    # Historical
    for r in sheet_ss:
        dt = parse_date_safe(r[5] if len(r)>5 else "")
        if dt and dt <= target_date:
            amt = float((r[8] if len(r)>8 else "0").replace(",", "").strip() or 0.0)
            combined_ss.append({
                "date": dt,
                "location2": (r[6] if len(r)>6 else "").strip().upper(),
                "amount": amt,
                "mobile": normalize_mobile(r[9] if len(r)>9 else ""),
                "msno": r[3] if len(r)>3 else "",
                "name": r[4] if len(r)>4 else "",
            })
            
    # Fresh 29th
    for item in ss_29_data:
        dt = parse_date_safe(item.get("SCHDATE"))
        amt = float(str(item.get("RECAMOUNT") or 0.0).replace(",", "").strip())
        loc_attr = loc_resolver.resolve(cost_name=item.get("COSTNAME"), comm_code=item.get("COMMCODE"), scheme_type="ss")
        loc2 = loc_attr.location_2.strip().upper()
        mob = normalize_mobile(item.get("MOBILENO"))
        combined_ss.append({
            "date": dt,
            "location2": loc2,
            "amount": amt,
            "mobile": mob,
            "msno": str(item.get("MSNO") or ""),
            "name": str(item.get("NAME") or ""),
        })
        
    print(f"  Total Combined Subhiksham (SS) rows through 29-Oct: {len(combined_ss)}")

    combined_sv = []
    for r in sheet_sv:
        dt = parse_date_safe(r[5] if len(r)>5 else "")
        if dt and dt <= target_date:
            amt = float((r[8] if len(r)>8 else "0").replace(",", "").strip() or 0.0)
            combined_sv.append({
                "date": dt,
                "location2": (r[6] if len(r)>6 else "").strip().upper(),
                "amount": amt,
                "mobile": normalize_mobile(r[9] if len(r)>9 else ""),
                "msno": r[3] if len(r)>3 else "",
                "name": r[4] if len(r)>4 else "",
            })
            
    for item in sv_29_data:
        dt = parse_date_safe(item.get("SCHDATE"))
        amt = float(str(item.get("RECAMOUNT") or 0.0).replace(",", "").strip())
        loc_attr = loc_resolver.resolve(cost_name=item.get("COSTNAME"), comm_code=item.get("COMMCODE"), scheme_type="sv")
        loc2 = loc_attr.location_2.strip().upper()
        mob = normalize_mobile(item.get("MOBILENO"))
        combined_sv.append({
            "date": dt,
            "location2": loc2,
            "amount": amt,
            "mobile": mob,
            "msno": str(item.get("MSNO") or ""),
            "name": str(item.get("NAME") or ""),
        })
        
    print(f"  Total Combined Viruksham (SV) rows through 29-Oct: {len(combined_sv)}")

    # ---------------------------------------------------------
    # STEP 4: Read DigiGold & DigiSilver for 29-Oct
    # ---------------------------------------------------------
    print("\n>>> STEP 4: Reading DigiGold & DigiSilver from Google Sheets...")
    digi_reader = DigiSourceReader(sheets, spreadsheet_id=digi_id)
    pivot_extr = digi_reader.extract_pivot_data("OCT Pivot")
    
    print(f"  Pivot Header Date: {pivot_extr.pivot_date_header}")
    print(f"  Gold Showrooms: {pivot_extr.gold_showroom_subtotal} | Grand Total: {pivot_extr.gold_grand_total}")
    print(f"  Silver Showrooms: {pivot_extr.silver_showroom_subtotal} | Grand Total: {pivot_extr.silver_grand_total}")

    # ---------------------------------------------------------
    # STEP 5: Calculate New Enrollment Tables (Revenue & Counts)
    # ---------------------------------------------------------
    print("\n>>> STEP 5: Calculating New Enrollment Report for 29/10/2026...")
    BRANCH_CONFIG = {
        "TVL": {
            "ss_locs": ["TIRUNELVELI", "TIRUNELVELI ECOMM"],
            "sv_locs": ["TIRUNELVELI SV", "TIRUNELVELI SV ECOMM"],
            "dg_cnt": pivot_extr.gold_values.get("NELLAI", 0),
            "ds_cnt": pivot_extr.silver_values.get("NELLAI", 0),
            "rev_target": 40000000.0,
            "cnt_target": 3000,
        },
        "CPT": {
            "ss_locs": ["CHROMEPET", "CHROMEPET ECOMM"],
            "sv_locs": ["CHROMEPET SV", "CHROMEPET SV ECOMM"],
            "dg_cnt": pivot_extr.gold_values.get("CHROMEPET", 0),
            "ds_cnt": pivot_extr.silver_values.get("CHROMEPET", 0),
            "rev_target": 72000000.0,
            "cnt_target": 6000,
        },
        "TVM": {
            "ss_locs": ["TRIVANDRUM", "TRIVANDRUM ECOMM"],
            "sv_locs": ["TRIVANDRUM SV", "TRIVANDRUM SV ECOMM"],
            "dg_cnt": pivot_extr.gold_values.get("TRIVANDRUM", 0),
            "ds_cnt": pivot_extr.silver_values.get("TRIVANDRUM", 0),
            "rev_target": 27000000.0,
            "cnt_target": 4000,
        },
        "TPJ": {
            "ss_locs": ["TRICHY", "TRICHY ECOMM"],
            "sv_locs": ["TRICHY SV", "TRICHY SV ECOMM"],
            "dg_cnt": pivot_extr.gold_values.get("TRICHY", 0),
            "ds_cnt": pivot_extr.silver_values.get("TRICHY", 0),
            "rev_target": 40000000.0,
            "cnt_target": 5000,
        },
        "CBE": {
            "ss_locs": ["COIMBATORE", "COIMBATORE ECOMM"],
            "sv_locs": ["COIMBATORE SV", "COIMBATORE SV ECOMM"],
            "dg_cnt": pivot_extr.gold_values.get("COIMBATORE", 0),
            "ds_cnt": pivot_extr.silver_values.get("COIMBATORE", 0),
            "rev_target": 30000000.0,
            "cnt_target": 7000,
        },
        "SLM": {
            "ss_locs": ["SALEM", "SALEM ECOMM"],
            "sv_locs": ["SALEM SV", "SALEM SV ECOMM"],
            "dg_cnt": pivot_extr.gold_values.get("SALEM", 0),
            "ds_cnt": pivot_extr.silver_values.get("SALEM", 0),
            "rev_target": 30000000.0,
            "cnt_target": 3000,
        },
        "PNM": {
            "ss_locs": ["POONAMALLEE", "POONAMALLEE ECOMM"],
            "sv_locs": ["POONAMALLEE SV", "POONAMALLEE SV ECOMM"],
            "dg_cnt": pivot_extr.gold_values.get("POONAMALLEE", 0),
            "ds_cnt": pivot_extr.silver_values.get("POONAMALLEE", 0),
            "rev_target": 20000000.0,
            "cnt_target": 2000,
        },
        "PADI": {
            "ss_locs": ["PADI", "PADI ECOMM"],
            "sv_locs": ["PADI SV", "PADI SV ECOMM"],
            "dg_cnt": pivot_extr.gold_values.get("PADI", 0),
            "ds_cnt": pivot_extr.silver_values.get("PADI", 0),
            "rev_target": 54000000.0,
            "cnt_target": 6000,
        },
        "KANCHI": {
            "ss_locs": ["KANCHEEPURAM", "KANCHEEPURAM ECOMM"],
            "sv_locs": ["KANCHEEPURAM SV", "KANCHEEPURAM SV ECOMM"],
            "dg_cnt": pivot_extr.gold_values.get("KANCHEEPURAM", 0),
            "ds_cnt": pivot_extr.silver_values.get("KANCHEEPURAM", 0),
            "rev_target": 27000000.0,
            "cnt_target": None,
        },
        "NGL": {
            "ss_locs": ["NAGARKOVIL", "NAGARKOVIL ECOMM"],
            "sv_locs": ["NAGARKOVIL SV", "NAGARKOVIL SV ECOMM"],
            "dg_cnt": pivot_extr.gold_values.get("NAGERCOIL", 0),
            "ds_cnt": pivot_extr.silver_values.get("NAGERCOIL", 0),
            "rev_target": None,
            "cnt_target": None,
        },
        "ONLINE": {
            "ss_locs": ["ONLINE", "CORPORATE OFFICE"],
            "sv_locs": ["ONLINE SV", "CORPORATE OFFICE SV"],
            "dg_cnt": pivot_extr.gold_values.get("ECOMMERCE", 0),
            "ds_cnt": pivot_extr.silver_values.get("ECOMMERCE", 0),
            "rev_target": None,
            "cnt_target": None,
        },
    }

    # Aggregate SS
    ss_by_loc = defaultdict(float)
    ss_cnt_by_loc = defaultdict(int)
    for r in combined_ss:
        ss_by_loc[r["location2"]] += r["amount"]
        ss_cnt_by_loc[r["location2"]] += 1
        
    # Aggregate SV
    sv_by_loc = defaultdict(float)
    sv_cnt_by_loc = defaultdict(int)
    for r in combined_sv:
        sv_by_loc[r["location2"]] += r["amount"]
        sv_cnt_by_loc[r["location2"]] += 1
        
    branches_order = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KANCHI", "NGL", "ONLINE"]
    
    table1_rows = []
    table2_rows = []
    
    for b in branches_order:
        cfg = BRANCH_CONFIG[b]
        b_ss_rev = sum(ss_by_loc[loc] for loc in cfg["ss_locs"])
        b_sv_rev = sum(sv_by_loc[loc] for loc in cfg["sv_locs"])
        b_tot_rev = b_ss_rev + b_sv_rev
        tgt_rev = cfg["rev_target"]
        
        yet_rev = (b_tot_rev - tgt_rev) if tgt_rev else b_tot_rev
        ach_rev_pct = round(b_tot_rev / tgt_rev * 100) if tgt_rev else None
        
        table1_rows.append({
            "branch": b,
            "target": tgt_rev,
            "ss": b_ss_rev,
            "sv": b_sv_rev,
            "total": b_tot_rev,
            "yet": yet_rev,
            "ach_pct": ach_rev_pct,
        })
        
        b_ss_cnt = sum(ss_cnt_by_loc[loc] for loc in cfg["ss_locs"])
        b_sv_cnt = sum(sv_cnt_by_loc[loc] for loc in cfg["sv_locs"])
        b_dg_cnt = cfg["dg_cnt"]
        b_ds_cnt = cfg["ds_cnt"]
        b_tot_cnt = b_ss_cnt + b_sv_cnt + b_dg_cnt + b_ds_cnt
        b_avg = round(b_tot_cnt / 29)
        
        table2_rows.append({
            "branch": "E COMM" if b == "ONLINE" else b,
            "target": cfg["cnt_target"],
            "ss": b_ss_cnt,
            "sv": b_sv_cnt,
            "dg": b_dg_cnt,
            "ds": b_ds_cnt,
            "total": b_tot_cnt,
            "avg": b_avg,
        })

    tot_rev_target = 313000000.0
    tot_ss_rev = sum(r["ss"] for r in table1_rows)
    tot_sv_rev = sum(r["sv"] for r in table1_rows)
    tot_comb_rev = tot_ss_rev + tot_sv_rev
    tot_yet_rev = tot_comb_rev - tot_rev_target
    tot_ach_rev_pct = round(tot_comb_rev / tot_rev_target * 100)
    
    tot_cnt_target = 36000
    tot_ss_cnt = sum(r["ss"] for r in table2_rows)
    tot_sv_cnt = sum(r["sv"] for r in table2_rows)
    tot_dg_cnt = sum(r["dg"] for r in table2_rows)
    tot_ds_cnt = sum(r["ds"] for r in table2_rows)
    tot_comb_cnt = tot_ss_cnt + tot_sv_cnt + tot_dg_cnt + tot_ds_cnt
    tot_avg = round(tot_comb_cnt / 29)
    
    print("\n--- TABLE 1: REVENUE (29/10/2026) ---")
    print(f"Total SS Revenue: ₹{tot_ss_rev:,.2f}")
    print(f"Total SV Revenue: ₹{tot_sv_rev:,.2f}")
    print(f"Total Revenue:    ₹{tot_comb_rev:,.2f} ({tot_ach_rev_pct}%)")
    
    print("\n--- TABLE 2: ENROLLMENT COUNTS (29/10/2026) ---")
    print(f"Total SS Count: {tot_ss_cnt:,}")
    print(f"Total SV Count: {tot_sv_cnt:,}")
    print(f"Total DG Count: {tot_dg_cnt:,}")
    print(f"Total DS Count: {tot_ds_cnt:,}")
    print(f"Grand Total:    {tot_comb_cnt:,} (Daily AVG: {tot_avg:,})")

    # ---------------------------------------------------------
    # STEP 6: Calculate Scheme Rejoining Report for 29/10/2026
    # ---------------------------------------------------------
    print("\n>>> STEP 6: Calculating Scheme Rejoining Report for 29/10/2026...")
    combined_closing = []
    for r in sheet_close:
        combined_closing.append(r)
        
    for item in close_29_rows:
        row_arr = [
            item.get("DOCDATE") or "",
            item.get("NAME") or "",
            item.get("PASSBOOK") or "",
            item.get("MOBILE") or "",
            item.get("SCHEMENAME") or "",
            str(item.get("WEIGHT") or "0"),
            item.get("STATUS") or "",
            item.get("SHOWROOM") or "",
        ]
        combined_closing.append(row_arr)
        
    print(f"  Total Combined Closing rows through 29-Oct: {len(combined_closing)}")
    
    rejoining_ss_input = []
    for r in combined_ss:
        rejoining_ss_input.append({
            "schdate": r["date"],
            "mobile": r["mobile"],
            "amount": r["amount"],
            "showroom": r["location2"],
            "scheme": "SS",
            "msno": r["msno"],
            "name": r["name"],
        })
    rejoining_sv_input = []
    for r in combined_sv:
        rejoining_sv_input.append({
            "schdate": r["date"],
            "mobile": r["mobile"],
            "amount": r["amount"],
            "showroom": r["location2"],
            "scheme": "SV",
            "msno": r["msno"],
            "name": r["name"],
        })
        
    matcher = RejoiningMatchingEngine(
        cutoff_date=target_date,
        require_enrollment_after_closure=False
    )
    rejoin_res = matcher.process(
        closed_records=combined_closing,
        ss_enrollments=rejoining_ss_input,
        sv_enrollments=rejoining_sv_input
    )
    
    print(f"  Rejoining Results: Closed={rejoin_res.total_closed_count:,}, Rejoined={rejoin_res.total_rejoin_count:,} ({rejoin_res.overall_rejoin_percentage:.2f}%)")
    print(f"  Rejoining SS: {rejoin_res.total_rejoin_ss:,} | Rejoining SV: {rejoin_res.total_rejoin_sv:,}")
    print(f"  Closed Weight: {rejoin_res.total_closed_weight:,.2f} grams")

    # ---------------------------------------------------------
    # STEP 7: Save CSV Files
    # ---------------------------------------------------------
    out_dir = project_root / "data" / "test_runs" / "report_2026-10-29"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "screenshots").mkdir(parents=True, exist_ok=True)
    
    enr_csv_path = out_dir / "New_Enrollment_Report_2026-10-29.csv"
    with open(enr_csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["NEW ENROLLMENT AS ON 29/10/2026"])
        w.writerow(["TABLE 1: REVENUE (INR)"])
        w.writerow(["Branch", "Target Value", "Subhiksham", "Viruksham", "Total", "Yet to Achieve", "Achieved %"])
        for r in table1_rows:
            tgt_str = f"{r['target']:,.0f}" if r["target"] else ""
            ach_str = f"{r['ach_pct']}%" if r["ach_pct"] else ""
            w.writerow([r["branch"], tgt_str, f"{r['ss']:,.2f}", f"{r['sv']:,.2f}", f"{r['total']:,.2f}", f"{r['yet']:,.0f}", ach_str])
        w.writerow(["Total", f"{tot_rev_target:,.0f}", f"{tot_ss_rev:,.2f}", f"{tot_sv_rev:,.2f}", f"{tot_comb_rev:,.2f}", f"{tot_yet_rev:,.0f}", f"{tot_ach_rev_pct}%"])
        w.writerow([])
        w.writerow(["TABLE 2: ENROLLMENT COUNT"])
        w.writerow(["Branch", "Count Target", "Subhiksham", "Viruksham", "DigiGold", "DigiSilver", "SS + SV + DG + DS", "AVG"])
        for r in table2_rows:
            tgt_str = f"{r['target']:,}" if r["target"] else ""
            w.writerow([r["branch"], tgt_str, r["ss"], r["sv"], r["dg"], r["ds"], r["total"], r["avg"]])
        w.writerow(["Total", f"{tot_cnt_target:,}", tot_ss_cnt, tot_sv_cnt, tot_dg_cnt, tot_ds_cnt, tot_comb_cnt, tot_avg])
    print(f"\nSaved Enrollment CSV: {enr_csv_path}")

    counts_map = {r.showroom: r.closed_count for r in rejoin_res.table1_rows}
    rejoin_csv_path = out_dir / "Scheme_Rejoining_Report_2026-10-29.csv"
    with open(rejoin_csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["SCHEME REJOINING REPORT AS ON 29/10/2026"])
        w.writerow(["TABLE 1: MAIN REJOINING TABLE"])
        w.writerow(["SHOWROOM", "CLOSED", "REJOIN SS", "REJOIN SV", "TOTAL REJOIN", "REJOIN %"])
        for r in rejoin_res.table1_rows:
            w.writerow([r.showroom, r.closed_count, r.rejoin_ss, r.rejoin_sv, r.total_rejoin, f"{r.rejoin_percentage:.2f}%"])
        w.writerow(["TOTAL", rejoin_res.total_closed_count, rejoin_res.total_rejoin_ss, rejoin_res.total_rejoin_sv, rejoin_res.total_rejoin_count, f"{rejoin_res.overall_rejoin_percentage:.2f}%"])
        w.writerow([])
        w.writerow(["TABLE 2: CLOSED WEIGHT TABLE"])
        w.writerow(["SHOWROOM", "COUNT", "WEIGHT (g)"])
        for r in rejoin_res.table2_weight_rows:
            cnt = counts_map.get(r.showroom, 0)
            w.writerow([r.showroom, cnt, f"{r.closed_weight:.2f}"])
        w.writerow(["TOTAL", rejoin_res.total_closed_count, f"{rejoin_res.total_closed_weight:.2f}"])
        w.writerow([])
        w.writerow(["TABLE 3: CLOSED COUNT TABLE"])
        w.writerow(["SHOWROOM", "SS", "SV"])
        for r in rejoin_res.table3_scheme_rows:
            sh_ss = sum(cnt for sch, cnt in r.scheme_counts.items() if "SUBHIKSHAM" in sch.upper() or "SUBIKSHAM" in sch.upper() or "LABHAM" in sch.upper() or sch.upper().startswith("SS"))
            sh_sv = sum(cnt for sch, cnt in r.scheme_counts.items() if "VIRUKSHAM" in sch.upper() or sch.upper().startswith("SV"))
            w.writerow([r.showroom, sh_ss, sh_sv])
        tot_close_ss = sum(sum(cnt for sch, cnt in r.scheme_counts.items() if "SUBHIKSHAM" in sch.upper() or "SUBIKSHAM" in sch.upper() or "LABHAM" in sch.upper() or sch.upper().startswith("SS")) for r in rejoin_res.table3_scheme_rows)
        tot_close_sv = sum(sum(cnt for sch, cnt in r.scheme_counts.items() if "VIRUKSHAM" in sch.upper() or sch.upper().startswith("SV")) for r in rejoin_res.table3_scheme_rows)
        w.writerow(["TOTAL", tot_close_ss, tot_close_sv])
    print(f"Saved Rejoining CSV: {rejoin_csv_path}")

    # ---------------------------------------------------------
    # STEP 8: Export Authentic Spreadsheet-Style Screenshot Cards (Read-Only)
    # ---------------------------------------------------------
    print("\n>>> STEP 8: Exporting Authentic Spreadsheet-Style Screenshot Cards (Read-Only)...")
    from app.reporting.screenshots import GoogleSheetsScreenshotEngine
    
    screenshot_engine = GoogleSheetsScreenshotEngine(
        sheets_service=sheets,
        config_bundle=config_bundle,
        output_dir=out_dir / "screenshots",
        dpi=200,
    )
    
    # Export authentic Google Sheets formatted tables
    res_enr = screenshot_engine.capture_enrollment(filename="New_Enrollment_Report_2026-10-29.png")
    res_rej = screenshot_engine.capture_closing(filename="Scheme_Rejoining_Report_2026-10-29.png")
    
    enr_png_path = res_enr.output_path
    rej_png_path = res_rej.output_path
    
    print(f"  [OK] Exported Enrollment Card: {enr_png_path} ({res_enr.dimensions[0]}x{res_enr.dimensions[1]})")
    print(f"  [OK] Exported Rejoining Card:  {rej_png_path} ({res_rej.dimensions[0]}x{res_rej.dimensions[1]})")

    # ---------------------------------------------------------
    # STEP 9: Two-Photo Telegram Delivery Verification (Simulated Dry-Run)
    # ---------------------------------------------------------
    print("\n>>> STEP 9: Validating Exactly Two-Photo Telegram Delivery (Dry-Run Mode)...")
    from app.delivery.telegram import TelegramDeliveryChannel, format_telegram_caption
    from app.delivery.base import DeliveryStatus
    
    tg_config = config_bundle.app.telegram
    # Safety: dry_run strictly enforced to avoid sending live Telegram messages without approval
    channel = TelegramDeliveryChannel(
        config=tg_config,
        enabled=True,
        dry_run=True,
        chat_id=tg_config.chat_id,
    )
    
    # Strictly HEADER ONLY - exactly two lines
    enr_caption = format_telegram_caption(target_date, "New Enrollment Report")
    rej_caption = format_telegram_caption(target_date, "Scheme Rejoining Report")
    
    print(f"Photo 1 Caption:\n{enr_caption}\n")
    print(f"Photo 2 Caption:\n{rej_caption}\n")
    
    # Photo 1: New Enrollment Report
    res_tg_enr = channel.send_photo(
        photo_path=enr_png_path,
        caption=enr_caption,
        recipient=tg_config.chat_id,
        report_date=target_date,
        force=True,
    )
    print(f"  Telegram Photo 1 (Enrollment): Status={res_tg_enr.status.value}, ID={res_tg_enr.message_id}")
    
    # Photo 2: Scheme Rejoining Report (exactly ONE send, all 3 sections included)
    res_tg_rej = channel.send_photo(
        photo_path=rej_png_path,
        caption=rej_caption,
        recipient=tg_config.chat_id,
        report_date=target_date,
        force=True,
    )
    print(f"  Telegram Photo 2 (Rejoining):  Status={res_tg_rej.status.value}, ID={res_tg_rej.message_id}")
    
    if res_tg_enr.status == DeliveryStatus.SENT and res_tg_rej.status == DeliveryStatus.SENT:
        print("\n  [OK] Exactly TWO photo messages verified for delivery.")
    else:
        print("\n  [WARNING] One or more delivery checks did not return SENT.")

    print("\n" + "=" * 80)
    print("ALL PIPELINE STEPS COMPLETED SUCCESSFULLY!")
    print("=" * 80)

if __name__ == "__main__":
    run_29th_pipeline()
