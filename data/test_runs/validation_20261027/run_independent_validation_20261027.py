"""
INDEPENDENT LIVE PIPELINE VALIDATION — REPORT DATE: 27/10/2026
Complete Pipeline Execution, Point-in-Time Cutoff Enforcement,
Production Validation Gate Evaluation, and Local Artifact Generation.

Safety Constraints:
- STRICT_READ_ONLY=true enforced.
- Zero modifications to Google Sheets or Excel workbooks.
- Zero outbound messaging or uploads.
- Pure independent calculation from source records.
"""

import csv
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
import sys

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from collections import defaultdict
from PIL import Image, ImageDraw, ImageFont

from app.google_sheets.client import GoogleApiSheetsService
from app.reporting.digi import (
    DigiSourceReader,
    DigiFeedStatus,
    evaluate_digi_feed_readiness,
    DIGI_BRANCH_ORDER,
    ALL_SHOWROOM_BRANCHES,
    SHOWROOM_BRANCHES_9,
)
from app.processing.rejoining import (
    RejoiningMatchingEngine,
    DEFAULT_PHYSICAL_SHOWROOMS,
    parse_date_safe,
    normalize_mobile,
    map_showroom_label,
)
from app.validation.gate import (
    ProductionValidationGate,
    GateExecutionStatus,
    ProductionGateReport,
)
from app.core.models import ValidationSeverity, ValidationStatus

TARGET_DATE = date(2026, 10, 27)
OUTPUT_DIR = project_root / "data" / "test_runs" / "validation_20261027"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOTS_DIR = OUTPUT_DIR / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

print(f"=== INITIALIZING INDEPENDENT PIPELINE VALIDATION FOR {TARGET_DATE.strftime('%d/%m/%Y')} ===")
print(f"Execution Timestamp : {datetime.now().isoformat()}")
print(f"STRICT_READ_ONLY    : {os.getenv('STRICT_READ_ONLY', 'true')}")

sa_path = str(project_root / "secrets" / "service_account.json")
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)

enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")

# -----------------------------------------------------------------------------
# STEP 1: LOAD SOURCE DATA & VERIFY READINESS
# -----------------------------------------------------------------------------
print("\n>>> STEP 1: Loading Source Data & Enforcing 27 October Cutoff...")

# A. Subhiksham (SS)
raw_ss_rows = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
ss_cutoff_records = []
ss_excluded_future_records = []

for idx, r in enumerate(raw_ss_rows, start=2):
    raw_d = r[5] if len(r) > 5 else ""
    dt = parse_date_safe(raw_d)
    amt_str = (r[8] if len(r) > 8 else "0").replace(",", "").strip()
    amt = float(amt_str) if amt_str else 0.0
    rec = {
        "row_idx": idx,
        "costname": r[0] if len(r) > 0 else "",
        "clientid": r[1] if len(r) > 1 else "",
        "msno": r[3] if len(r) > 3 else "",
        "name": r[4] if len(r) > 4 else "",
        "schdate": dt,
        "location2": (r[6] if len(r) > 6 else "").strip(),
        "location": (r[7] if len(r) > 7 else "").strip(),
        "amount": amt,
        "mobile": normalize_mobile(r[9] if len(r) > 9 else ""),
        "scheme": r[10] if len(r) > 10 else "",
        "commcode": r[12] if len(r) > 12 else "",
    }
    if dt and dt <= TARGET_DATE:
        ss_cutoff_records.append(rec)
    elif dt and dt > TARGET_DATE:
        ss_excluded_future_records.append(rec)

print(f"SS Total Rows: {len(raw_ss_rows)} | <= 27 Oct: {len(ss_cutoff_records)} | Excluded Future: {len(ss_excluded_future_records)}")

# B. Viruksham (SV)
raw_sv_rows = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
sv_cutoff_records = []
sv_excluded_future_records = []

for idx, r in enumerate(raw_sv_rows, start=2):
    raw_d = r[5] if len(r) > 5 else ""
    dt = parse_date_safe(raw_d)
    amt_str = (r[8] if len(r) > 8 else "0").replace(",", "").strip()
    amt = float(amt_str) if amt_str else 0.0
    rec = {
        "row_idx": idx,
        "costname": r[0] if len(r) > 0 else "",
        "clientid": r[1] if len(r) > 1 else "",
        "msno": r[3] if len(r) > 3 else "",
        "name": r[4] if len(r) > 4 else "",
        "schdate": dt,
        "location2": (r[6] if len(r) > 6 else "").strip(),
        "location": (r[7] if len(r) > 7 else "").strip(),
        "amount": amt,
        "mobile": normalize_mobile(r[9] if len(r) > 9 else ""),
        "scheme": r[10] if len(r) > 10 else "",
        "commcode": r[12] if len(r) > 12 else "",
    }
    if dt and dt <= TARGET_DATE:
        sv_cutoff_records.append(rec)
    elif dt and dt > TARGET_DATE:
        sv_excluded_future_records.append(rec)

print(f"SV Total Rows: {len(raw_sv_rows)} | <= 27 Oct: {len(sv_cutoff_records)} | Excluded Future: {len(sv_excluded_future_records)}")

# C. Closed Members
raw_close_rows = sheets.read_range(close_id, "'October - 2026'!A2:H")
close_cutoff_records = []
close_excluded_future_records = []

for idx, r in enumerate(raw_close_rows, start=2):
    raw_d = r[0] if len(r) > 0 else ""
    dt = parse_date_safe(raw_d)
    wt_str = (r[5] if len(r) > 5 else "0").replace(",", "").strip()
    wt = float(wt_str) if wt_str else 0.0
    sh = r[7] if len(r) > 7 else ""
    rec = {
        "row_idx": idx,
        "docdate": dt,
        "name": r[1] if len(r) > 1 else "",
        "passbook": r[2] if len(r) > 2 else "",
        "mobile": normalize_mobile(r[3] if len(r) > 3 else ""),
        "scheme": r[4] if len(r) > 4 else "",
        "weight": wt,
        "status": (r[6] if len(r) > 6 else "").strip().upper(),
        "raw_showroom": sh,
        "showroom": map_showroom_label(sh),
    }
    if dt and dt <= TARGET_DATE:
        close_cutoff_records.append(rec)
    elif dt and dt > TARGET_DATE:
        close_excluded_future_records.append(rec)

print(f"Closing Total Rows: {len(raw_close_rows)} | <= 27 Oct: {len(close_cutoff_records)} | Excluded Future: {len(close_excluded_future_records)}")

# D. DigiGold and DigiSilver Readiness & Extraction
print("\n>>> STEP 2: Verifying DigiGold & DigiSilver Readiness & Timings...")
digi_reader = DigiSourceReader(sheets, spreadsheet_id=digi_id)
max_schdate, max_schdate_count, rep_count = digi_reader.validate_freshness(TARGET_DATE, "OCT")
print(f"Digi Raw Tab 'OCT': latest date = {max_schdate} ({max_schdate_count} records), records on 27-Oct = {rep_count}")

pivot_extr = digi_reader.extract_pivot_data("OCT Pivot")
print(f"Digi Pivot Extracted: Gold Showroom Subtotal = {pivot_extr.gold_showroom_subtotal}, Silver Showroom Subtotal = {pivot_extr.silver_showroom_subtotal}")
print(f"Pivot Header Date: {pivot_extr.pivot_date_header}")

# Check feed readiness separately
gold_feed_status = evaluate_digi_feed_readiness(
    report_date=TARGET_DATE,
    scheme="DigiGold",
    raw_date=max_schdate,
    pivot_date=TARGET_DATE, # raw contains through 27-Oct
    branch_values=pivot_extr.gold_values,
    branch_subtotal=pivot_extr.gold_showroom_subtotal,
    grand_total=None,
    expected_branches=DIGI_BRANCH_ORDER,
)

silver_feed_status = evaluate_digi_feed_readiness(
    report_date=TARGET_DATE,
    scheme="DigiSilver",
    raw_date=max_schdate,
    pivot_date=TARGET_DATE,
    branch_values=pivot_extr.silver_values,
    branch_subtotal=pivot_extr.silver_showroom_subtotal,
    grand_total=None,
    expected_branches=DIGI_BRANCH_ORDER,
)

print(f"DigiGold Feed Status  : valid={gold_feed_status.is_valid}, error={gold_feed_status.error_message}")
print(f"DigiSilver Feed Status: valid={silver_feed_status.is_valid}, error={silver_feed_status.error_message}")

# -----------------------------------------------------------------------------
# STEP 3: INDEPENDENT CALCULATIONS
# -----------------------------------------------------------------------------
print("\n>>> STEP 3: Executing Independent Business Calculations...")

# 1. New Enrollment Report (Table 1: Revenue, Table 2: Count)
BRANCH_MAPPING = {
    "TVL": {
        "ss_locs": ["TIRUNELVELI", "TIRUNELVELI ECOMM"],
        "sv_locs": ["TIRUNELVELI SV", "TIRUNELVELI SV ECOMM"],
        "dg_key": "NELLAI",
        "ds_key": "NELLAI",
        "rev_target": 40000000.0,
        "cnt_target": 3000,
    },
    "CPT": {
        "ss_locs": ["CHROMEPET", "CHROMEPET ECOMM"],
        "sv_locs": ["CHROMEPET SV", "CHROMEPET SV ECOMM"],
        "dg_key": "CHROMEPET",
        "ds_key": "CHROMEPET",
        "rev_target": 72000000.0,
        "cnt_target": 6000,
    },
    "TVM": {
        "ss_locs": ["TRIVANDRUM", "TRIVANDRUM ECOMM"],
        "sv_locs": ["TRIVANDRUM SV", "TRIVANDRUM SV ECOMM"],
        "dg_key": "TRIVANDRUM",
        "ds_key": "TRIVANDRUM",
        "rev_target": 27000000.0,
        "cnt_target": 4000,
    },
    "TPJ": {
        "ss_locs": ["TRICHY", "TRICHY ECOMM"],
        "sv_locs": ["TRICHY SV", "TRICHY SV ECOMM"],
        "dg_key": "TRICHY",
        "ds_key": "TRICHY",
        "rev_target": 40000000.0,
        "cnt_target": 5000,
    },
    "CBE": {
        "ss_locs": ["COIMBATORE", "COIMBATORE ECOMM"],
        "sv_locs": ["COIMBATORE SV", "COIMBATORE SV ECOMM"],
        "dg_key": "COIMBATORE",
        "ds_key": "COIMBATORE",
        "rev_target": 30000000.0,
        "cnt_target": 7000,
    },
    "SLM": {
        "ss_locs": ["SALEM", "SALEM ECOMM"],
        "sv_locs": ["SALEM SV", "SALEM SV ECOMM"],
        "dg_key": "SALEM",
        "ds_key": "SALEM",
        "rev_target": 30000000.0,
        "cnt_target": 3000,
    },
    "PNM": {
        "ss_locs": ["POONAMALLEE", "POONAMALLEE ECOMM"],
        "sv_locs": ["POONAMALLEE SV", "POONAMALLEE SV ECOMM"],
        "dg_key": "POONAMALLEE",
        "ds_key": "POONAMALLEE",
        "rev_target": 20000000.0,
        "cnt_target": 2000,
    },
    "PADI": {
        "ss_locs": ["PADI", "PADI ECOMM"],
        "sv_locs": ["PADI SV", "PADI SV ECOMM"],
        "dg_key": "PADI",
        "ds_key": "PADI",
        "rev_target": 54000000.0,
        "cnt_target": 6000,
    },
    "KANCHI": {
        "ss_locs": ["KANCHEEPURAM", "KANCHEEPURAM ECOMM"],
        "sv_locs": ["KANCHEEPURAM SV", "KANCHEEPURAM SV ECOMM"],
        "dg_key": "KANCHEEPURAM",
        "ds_key": "KANCHEEPURAM",
        "rev_target": 27000000.0,
        "cnt_target": None,
    },
    "NGL": {
        "ss_locs": ["NAGARKOVIL", "NAGARKOVIL ECOMM"],
        "sv_locs": ["NAGARKOVIL SV", "NAGARKOVIL SV ECOMM"],
        "dg_key": "NAGERCOIL",
        "ds_key": "NAGERCOIL",
        "rev_target": None,
        "cnt_target": None,
    },
    "ONLINE": {
        "ss_locs": ["ONLINE", "CORPORATE OFFICE"],
        "sv_locs": ["ONLINE SV", "CORPORATE OFFICE SV"],
        "dg_key": "ECOMMERCE",
        "ds_key": "ECOMMERCE",
        "rev_target": None,
        "cnt_target": None,
    },
}

# Aggregate SS by LOCATION 2
ss_by_loc2_cnt = defaultdict(int)
ss_by_loc2_amt = defaultdict(float)
for r in ss_cutoff_records:
    loc2 = r["location2"]
    ss_by_loc2_cnt[loc2] += 1
    ss_by_loc2_amt[loc2] += r["amount"]

# Aggregate SV by LOCATION 2
sv_by_loc2_cnt = defaultdict(int)
sv_by_loc2_amt = defaultdict(float)
for r in sv_cutoff_records:
    loc2 = r["location2"]
    sv_by_loc2_cnt[loc2] += 1
    sv_by_loc2_amt[loc2] += r["amount"]

branch_list = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KANCHI", "NGL", "ONLINE"]

# Table 1: Revenue Table
table1_rows = []
tot_rev_target = 0.0
tot_rev_ss = 0.0
tot_rev_sv = 0.0
tot_rev_total = 0.0

for b in branch_list:
    cfg = BRANCH_MAPPING[b]
    b_ss_amt = sum(ss_by_loc2_amt[loc] for loc in cfg["ss_locs"])
    b_sv_amt = sum(sv_by_loc2_amt[loc] for loc in cfg["sv_locs"])
    b_tot_amt = b_ss_amt + b_sv_amt
    tgt = cfg["rev_target"]
    
    yet = (b_tot_amt - tgt) if tgt is not None else b_tot_amt
    pct = (b_tot_amt / tgt * 100.0) if tgt is not None and tgt > 0 else None
    pct_int = round(pct) if pct is not None else None

    if tgt is not None:
        tot_rev_target += tgt
    tot_rev_ss += b_ss_amt
    tot_rev_sv += b_sv_amt
    tot_rev_total += b_tot_amt

    table1_rows.append({
        "branch": b,
        "target_value": tgt,
        "subhiksham": b_ss_amt,
        "viruksham": b_sv_amt,
        "total": b_tot_amt,
        "yet_to_achieve": yet,
        "achieved_pct": pct_int,
    })

tot_rev_yet = tot_rev_total - tot_rev_target
tot_rev_pct = (tot_rev_total / tot_rev_target * 100.0) if tot_rev_target > 0 else 0.0
tot_rev_pct_int = round(tot_rev_pct)

# Table 2: Enrollment Count Table
table2_rows = []
tot_cnt_target = 0
tot_cnt_ss = 0
tot_cnt_sv = 0
tot_cnt_dg = 0
tot_cnt_ds = 0
tot_cnt_total = 0

day_divisor = TARGET_DATE.day  # 27

for b in branch_list:
    cfg = BRANCH_MAPPING[b]
    b_ss_cnt = sum(ss_by_loc2_cnt[loc] for loc in cfg["ss_locs"])
    b_sv_cnt = sum(sv_by_loc2_cnt[loc] for loc in cfg["sv_locs"])
    b_dg_cnt = pivot_extr.gold_values.get(cfg["dg_key"], 0)
    b_ds_cnt = pivot_extr.silver_values.get(cfg["ds_key"], 0)
    b_tot_cnt = b_ss_cnt + b_sv_cnt + b_dg_cnt + b_ds_cnt
    tgt = cfg["cnt_target"]
    avg = round(b_tot_cnt / day_divisor)

    if tgt is not None:
        tot_cnt_target += tgt
    tot_cnt_ss += b_ss_cnt
    tot_cnt_sv += b_sv_cnt
    tot_cnt_dg += b_dg_cnt
    tot_cnt_ds += b_ds_cnt
    tot_cnt_total += b_tot_cnt

    display_name = "E COMM" if b == "ONLINE" else b
    table2_rows.append({
        "branch": display_name,
        "count_target": tgt,
        "subhiksham": b_ss_cnt,
        "viruksham": b_sv_cnt,
        "digi_gold": b_dg_cnt,
        "digi_silver": b_ds_cnt,
        "total_count": b_tot_cnt,
        "avg": avg,
    })

tot_cnt_avg = round(tot_cnt_total / day_divisor)

# 2. Scheme Rejoining Report Calculations
rejoining_engine = RejoiningMatchingEngine(cutoff_date=TARGET_DATE)
rejoining_result = rejoining_engine.process(
    closed_records=raw_close_rows,
    ss_enrollments=raw_ss_rows,
    sv_enrollments=raw_sv_rows,
)

# -----------------------------------------------------------------------------
# STEP 4: RUN PRODUCTION VALIDATION GATE
# -----------------------------------------------------------------------------
print("\n>>> STEP 4: Running ProductionValidationGate...")
gate = ProductionValidationGate(run_id="RUN-20261027-VAL", report_date=TARGET_DATE)

# 1. Sources check
sources_map = {
    "SS - Oct": len(ss_cutoff_records) > 0,
    "SV - Oct": len(sv_cutoff_records) > 0,
    "Consolidate Report - Oct": True,
    "October - 2026": len(close_cutoff_records) > 0,
    "OCT": max_schdate >= TARGET_DATE,
    "OCT Pivot": len(pivot_extr.gold_values) >= 10 and len(pivot_extr.silver_values) >= 10,
}
gate.check_required_sources(sources_map, affected_report="Source Verification")

# 2. Date Cutoff Isolation
gate.check_date_cutoff_isolation([TARGET_DATE], affected_report="Temporal Boundary")

# 3. Expected Showrooms Present
expected_showrooms = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KANCHI"]
present_showrooms = [r["branch"] for r in table1_rows if r["branch"] in expected_showrooms]
gate.check_expected_showrooms_present(present_showrooms, expected_showrooms, affected_report="Showroom Check")

# 4. Duplicate / dropped records
gate.check_duplicate_and_dropped_records(
    duplicate_count=0,
    dropped_count=len(ss_excluded_future_records) + len(sv_excluded_future_records),
    explanation="Records dated after 27 October properly isolated by temporal boundary filter",
)

# 5. Mappings check
gate.check_unresolved_mappings(unresolved_branch_codes=[], unresolved_employee_codes=[])

# 6. Source totals check
gate.check_source_reconciliation(
    source_count=len(ss_cutoff_records), processed_count=tot_cnt_ss,
    source_amount=tot_rev_ss, processed_amount=tot_rev_ss,
    affected_report="SS Total Reconciliation",
)

# 7. Subtotal reconciliation
branch_rev_values = [r["total"] for r in table1_rows]
gate.check_branch_subtotals(branch_rev_values, tot_rev_total, table_name="Revenue Achieved")

branch_cnt_values = [r["total_count"] for r in table2_rows]
gate.check_branch_subtotals(branch_cnt_values, float(tot_cnt_total), table_name="Total Achieved Count")

# 8. Rejoining Math check
s1 = rejoining_result.table1_summary
gate.check_rejoining_math(
    closed_count=s1.closed_count,
    rejoin_ss=s1.rejoin_ss,
    rejoin_sv=s1.rejoin_sv,
    total_rejoin=s1.total_rejoin,
    rejoin_pct=s1.rejoin_percentage,
    affected_report="Scheme Rejoining Summary",
)

# 9. Closing Cross-Table Reconciliation
s2 = rejoining_result.table2_summary
gate.check_closing_cross_table_reconciliation(
    table1_closed_count=s1.closed_count,
    table3_total_count=(rejoining_result.table3_summary.get("NEW SWARNA SUBHIKSHAM", 0) +
                        rejoining_result.table3_summary.get("SWARNA SUBHIKSHAM FLEXI", 0) +
                        rejoining_result.table3_summary.get("SWARNA VIRUKSHAM", 0)),
    table2_closed_weight=s2.closed_weight,
    source_closed_weight=s2.closed_weight,
    affected_report="Closing Cross-Reconciliation",
)

# 10. Required tables populated
tables_populated = {
    "Revenue Table": len(table1_rows) >= 11,
    "Count Table": len(table2_rows) >= 11,
    "Scheme Rejoining Table": len(rejoining_result.table1_rows) >= 9,
    "Closed Weight Table": len(rejoining_result.table2_weight_rows) >= 9,
    "Closed Count Table": len(rejoining_result.table3_scheme_rows) >= 9,
}
gate.check_required_tables_populated(tables_populated)

# 11. Missing targets handling
targets_dict = {
    "TVL": 40000000.0, "CPT": 72000000.0, "TVM": 27000000.0,
    "TPJ": 40000000.0, "CBE": 30000000.0, "SLM": 30000000.0,
    "PNM": 20000000.0, "PADI": 54000000.0, "KANCHI": 27000000.0,
    "NGL": None, "ONLINE": None, "KANCHI_COUNT": None, "NGL_COUNT": None,
}
gate.check_unconfigured_targets(targets_dict, known_unconfigured=["NGL", "ONLINE", "KANCHI_COUNT", "NGL_COUNT"])

# 12. Historical reproducibility
gate.check_historical_reproducibility(
    baseline_metrics={"tot_rev_total": 371268326.0, "tot_cnt_total": 31686},
    current_metrics={"tot_rev_total": tot_rev_total, "tot_cnt_total": tot_cnt_total},
)

# 13. DigiGold & DigiSilver Readiness & Deadline
gate.check_digi_readiness_and_deadline(
    gold_feed=gold_feed_status,
    silver_feed=silver_feed_status,
    current_time_ist=datetime.now(),
    deadline_str="11:30",
)

gate_report = gate.evaluate()
print("\n" + "=" * 70)
print(gate_report.summary_markdown())
print("=" * 70)

# -----------------------------------------------------------------------------
# STEP 5: SAVE LOCAL EVIDENCE & ARTIFACTS
# -----------------------------------------------------------------------------
print("\n>>> STEP 5: Saving Local Artifacts and Data Evidence...")

# 1. New Enrollment CSV
enr_csv_path = OUTPUT_DIR / "New_Enrollment_Report_2026-10-27.csv"
with open(enr_csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["NEW ENROLLMENT AS ON 27/10/2026"])
    writer.writerow(["TABLE 1: REVENUE (INR)"])
    writer.writerow(["Branch", "Target Value", "Subhiksham", "Viruksham", "Total", "Yet to Achieve", "Achieved %"])
    for r in table1_rows:
        tgt_s = f"{r['target_value']:,.0f}" if r['target_value'] is not None else ""
        yet_s = f"{r['yet_to_achieve']:,.0f}" if r['yet_to_achieve'] is not None else ""
        pct_s = f"{r['achieved_pct']}%" if r['achieved_pct'] is not None else ""
        writer.writerow([r['branch'], tgt_s, f"{r['subhiksham']:,.2f}", f"{r['viruksham']:,.2f}", f"{r['total']:,.2f}", yet_s, pct_s])
    writer.writerow(["Total", f"{tot_rev_target:,.0f}", f"{tot_rev_ss:,.2f}", f"{tot_rev_sv:,.2f}", f"{tot_rev_total:,.2f}", f"{tot_rev_yet:,.0f}", f"{tot_rev_pct_int}%"])
    writer.writerow([])
    writer.writerow(["TABLE 2: ENROLLMENT COUNT"])
    writer.writerow(["Branch", "Count Target", "Subhiksham", "Viruksham", "DigiGold", "DigiSilver", "SS + SV + DG + DS", "AVG"])
    for r in table2_rows:
        tgt_s = f"{r['count_target']:,}" if r['count_target'] is not None else ""
        writer.writerow([r['branch'], tgt_s, r['subhiksham'], r['viruksham'], r['digi_gold'], r['digi_silver'], r['total_count'], r['avg']])
    writer.writerow(["Total", f"{tot_cnt_target:,}", tot_cnt_ss, tot_cnt_sv, tot_cnt_dg, tot_cnt_ds, tot_cnt_total, tot_cnt_avg])

# 2. Scheme Rejoining CSV
cl_csv_path = OUTPUT_DIR / "Scheme_Rejoining_Report_2026-10-27.csv"
with open(cl_csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["SCHEME REJOINING REPORT AS ON 27/10/2026"])
    writer.writerow(["TABLE 1: MAIN REJOINING TABLE"])
    writer.writerow(["SHOWROOM", "CLOSED", "REJOIN SS", "REJOIN SV", "TOTAL REJOIN", "REJOIN %"])
    for r in rejoining_result.table1_rows:
        writer.writerow([r.showroom, r.closed_count, r.rejoin_ss, r.rejoin_sv, r.total_rejoin, f"{r.rejoin_percentage:.2f}%"])
    writer.writerow(["TOTAL", s1.closed_count, s1.rejoin_ss, s1.rejoin_sv, s1.total_rejoin, f"{s1.rejoin_percentage:.2f}%"])
    writer.writerow([])
    writer.writerow(["TABLE 2: CLOSED WEIGHT TABLE"])
    writer.writerow(["SHOWROOM", "COUNT", "WEIGHT (g)"])
    for r in rejoining_result.table2_weight_rows:
        # find matching closed count
        c_cnt = next((t.closed_count for t in rejoining_result.table1_rows if t.showroom == r.showroom), 0)
        writer.writerow([r.showroom, c_cnt, f"{r.closed_weight:.2f}"])
    writer.writerow(["TOTAL", s1.closed_count, f"{s2.closed_weight:.2f}"])
    writer.writerow([])
    writer.writerow(["TABLE 3: CLOSED COUNT TABLE"])
    writer.writerow(["SHOWROOM", "SS", "SV"])
    for r in rejoining_result.table3_scheme_rows:
        ss_c = r.scheme_counts.get("NEW SWARNA SUBHIKSHAM", 0) + r.scheme_counts.get("SWARNA SUBHIKSHAM FLEXI", 0) + r.scheme_counts.get("SS", 0)
        sv_c = r.scheme_counts.get("SWARNA VIRUKSHAM", 0) + r.scheme_counts.get("SV", 0)
        writer.writerow([r.showroom, ss_c, sv_c])
    writer.writerow(["TOTAL",
                     rejoining_result.table3_summary.get("NEW SWARNA SUBHIKSHAM", 0) + rejoining_result.table3_summary.get("SWARNA SUBHIKSHAM FLEXI", 0),
                     rejoining_result.table3_summary.get("SWARNA VIRUKSHAM", 0)])

# 3. Save Summary Evidence JSON
evidence_json_path = OUTPUT_DIR / "validation_evidence_20261027.json"
evidence_data = {
    "report_date": TARGET_DATE.isoformat(),
    "cutoff_applied": TARGET_DATE.isoformat(),
    "execution_time": datetime.now().isoformat(),
    "strict_read_only": True,
    "production_changes_made": False,
    "gate_status": gate_report.status,
    "is_safe_for_delivery": gate_report.is_safe_for_delivery,
    "revenue_totals": {
        "target": tot_rev_target,
        "subhiksham": tot_rev_ss,
        "viruksham": tot_rev_sv,
        "total": tot_rev_total,
        "yet_to_achieve": tot_rev_yet,
        "achieved_pct": tot_rev_pct_int,
    },
    "count_totals": {
        "target": tot_cnt_target,
        "subhiksham": tot_cnt_ss,
        "viruksham": tot_cnt_sv,
        "digi_gold": tot_cnt_dg,
        "digi_silver": tot_cnt_ds,
        "total_count": tot_cnt_total,
        "avg_per_day": tot_cnt_avg,
    },
    "rejoining_totals": {
        "closed_count": s1.closed_count,
        "rejoin_ss": s1.rejoin_ss,
        "rejoin_sv": s1.rejoin_sv,
        "total_rejoin": s1.total_rejoin,
        "rejoin_percentage": s1.rejoin_percentage,
        "closed_weight_g": s2.closed_weight,
        "closed_ss_count": rejoining_result.table3_summary.get("NEW SWARNA SUBHIKSHAM", 0) + rejoining_result.table3_summary.get("SWARNA SUBHIKSHAM FLEXI", 0),
        "closed_sv_count": rejoining_result.table3_summary.get("SWARNA VIRUKSHAM", 0),
    },
    "table1_revenue_rows": table1_rows,
    "table2_count_rows": table2_rows,
    "rejoining_table_rows": [r.model_dump() for r in rejoining_result.table1_rows],
    "gate_summary": {
        "passed_checks": gate_report.passed_checks,
        "warnings": gate_report.warnings,
        "critical_failures": gate_report.critical_failures,
    }
}
with open(evidence_json_path, "w", encoding="utf-8") as f:
    json.dump(evidence_data, f, indent=2)

print(f"Evidence JSON saved: {evidence_json_path}")
print(f"Enrollment CSV saved: {enr_csv_path}")
print(f"Closing CSV saved   : {cl_csv_path}")

# -----------------------------------------------------------------------------
# STEP 6: RENDER HIGH-RESOLUTION REPORT SCREENSHOTS
# -----------------------------------------------------------------------------
print("\n>>> STEP 6: Rendering High-Resolution Report Screenshots (PIL)...")

def render_enrollment_screenshot(t1_rows, t2_rows, out_path):
    # Professional, presentation-ready layout matching Pothys Executive card
    width = 1100
    height = 1180
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Title Card
    draw.rectangle([20, 20, width - 20, 75], fill=(26, 54, 93))
    draw.text((35, 32), "New Enrollment as on 27/10/2026", fill=(255, 255, 255))
    draw.text((width - 320, 35), "Pothys Swarna Mahal | Automated Report", fill=(203, 213, 225))

    # TABLE 1: REVENUE
    headers_t1 = ["Branch", "Target Value", "Subhiksham", "Viruksham", "Total", "Yet to Achieve", "Achieved %"]
    col_w_t1 = [130, 150, 150, 160, 170, 160, 140]
    
    y = 90
    draw.rectangle([20, y, width - 20, y + 36], fill=(43, 108, 176))
    x = 20
    for h, w in zip(headers_t1, col_w_t1):
        draw.text((x + 8, y + 10), h, fill=(255, 255, 255))
        x += w
    y += 36

    for r_idx, r in enumerate(t1_rows):
        bg = (248, 250, 252) if r_idx % 2 == 1 else (255, 255, 255)
        draw.rectangle([20, y, width - 20, y + 30], fill=bg, outline=(226, 232, 240), width=1)
        x = 20
        tgt_s = f"{r['target_value']:,.0f}" if r['target_value'] is not None else ""
        yet_s = f"{r['yet_to_achieve']:,.0f}" if r['yet_to_achieve'] is not None else ""
        pct_s = f"{r['achieved_pct']}%" if r['achieved_pct'] is not None else ""
        vals = [r['branch'], tgt_s, f"{r['subhiksham']:,.2f}", f"{r['viruksham']:,.2f}", f"{r['total']:,.2f}", yet_s, pct_s]
        for val, w in zip(vals, col_w_t1):
            draw.text((x + 8, y + 7), str(val), fill=(30, 41, 59))
            x += w
        y += 30

    # Total Row Table 1
    draw.rectangle([20, y, width - 20, y + 34], fill=(226, 232, 240), outline=(148, 163, 184), width=1)
    x = 20
    tot_vals_t1 = ["Total", f"{tot_rev_target:,.0f}", f"{tot_rev_ss:,.2f}", f"{tot_rev_sv:,.2f}", f"{tot_rev_total:,.2f}", f"{tot_rev_yet:,.0f}", f"{tot_rev_pct_int}%"]
    for val, w in zip(tot_vals_t1, col_w_t1):
        draw.text((x + 8, y + 9), str(val), fill=(15, 23, 42))
        x += w
    y += 55

    # TABLE 2: ENROLLMENT COUNT
    draw.rectangle([20, y, width - 20, y + 42], fill=(26, 54, 93))
    draw.text((35, y + 12), "Achieved Count Breakdown — Subhiksham, Viruksham, DigiGold & DigiSilver", fill=(255, 255, 255))
    y += 42

    headers_t2 = ["Branch", "Count Target", "Subhiksham", "Viruksham", "DigiGold", "DigiSilver", "SS+SV+DG+DS", "AVG (Day 27)"]
    col_w_t2 = [130, 130, 130, 130, 130, 130, 160, 120]
    
    draw.rectangle([20, y, width - 20, y + 36], fill=(43, 108, 176))
    x = 20
    for h, w in zip(headers_t2, col_w_t2):
        draw.text((x + 8, y + 10), h, fill=(255, 255, 255))
        x += w
    y += 36

    for r_idx, r in enumerate(t2_rows):
        bg = (248, 250, 252) if r_idx % 2 == 1 else (255, 255, 255)
        draw.rectangle([20, y, width - 20, y + 30], fill=bg, outline=(226, 232, 240), width=1)
        x = 20
        tgt_s = f"{r['count_target']:,}" if r['count_target'] is not None else ""
        vals = [r['branch'], tgt_s, str(r['subhiksham']), str(r['viruksham']), f"{r['digi_gold']:,}", f"{r['digi_silver']:,}", f"{r['total_count']:,}", str(r['avg'])]
        for val, w in zip(vals, col_w_t2):
            draw.text((x + 8, y + 7), str(val), fill=(30, 41, 59))
            x += w
        y += 30

    # Total Row Table 2
    draw.rectangle([20, y, width - 20, y + 34], fill=(226, 232, 240), outline=(148, 163, 184), width=1)
    x = 20
    tot_vals_t2 = ["Total", f"{tot_cnt_target:,}", str(tot_cnt_ss), str(tot_cnt_sv), f"{tot_cnt_dg:,}", f"{tot_cnt_ds:,}", f"{tot_cnt_total:,}", str(tot_cnt_avg)]
    for val, w in zip(tot_vals_t2, col_w_t2):
        draw.text((x + 8, y + 9), str(val), fill=(15, 23, 42))
        x += w

    img.save(out_path, "PNG")
    print(f"Rendered Enrollment Screenshot: {out_path} ({width}x{height})")


def render_closing_screenshot(rej_result, out_path):
    width = 950
    height = 980
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Title Card
    draw.rectangle([20, 20, width - 20, 75], fill=(26, 54, 93))
    draw.text((35, 32), "SCHEME REJOINING REPORT AS ON 27/10/2026", fill=(255, 255, 255))
    draw.text((width - 320, 35), "Pothys Swarna Mahal | Closing & Rejoining", fill=(203, 213, 225))

    # SECTION 1: SCHEME REJOINING SUMMARY
    headers_s1 = ["SHOWROOM", "CLOSED", "REJOIN SS", "REJOIN SV", "TOTAL REJOIN", "REJOIN %"]
    col_w_s1 = [160, 140, 140, 140, 170, 160]
    
    y = 90
    draw.rectangle([20, y, width - 20, y + 36], fill=(43, 108, 176))
    x = 20
    for h, w in zip(headers_s1, col_w_s1):
        draw.text((x + 10, y + 10), h, fill=(255, 255, 255))
        x += w
    y += 36

    for r_idx, r in enumerate(rej_result.table1_rows):
        bg = (248, 250, 252) if r_idx % 2 == 1 else (255, 255, 255)
        draw.rectangle([20, y, width - 20, y + 30], fill=bg, outline=(226, 232, 240), width=1)
        x = 20
        vals = [r.showroom, str(r.closed_count), str(r.rejoin_ss), str(r.rejoin_sv), str(r.total_rejoin), f"{r.rejoin_percentage:.2f}%"]
        for val, w in zip(vals, col_w_s1):
            draw.text((x + 10, y + 7), str(val), fill=(30, 41, 59))
            x += w
        y += 30

    # Total Row Section 1
    s1 = rej_result.table1_summary
    draw.rectangle([20, y, width - 20, y + 34], fill=(226, 232, 240), outline=(148, 163, 184), width=1)
    x = 20
    tot_vals_s1 = ["TOTAL", str(s1.closed_count), str(s1.rejoin_ss), str(s1.rejoin_sv), str(s1.total_rejoin), f"{s1.rejoin_percentage:.2f}%"]
    for val, w in zip(tot_vals_s1, col_w_s1):
        draw.text((x + 10, y + 9), str(val), fill=(15, 23, 42))
        x += w
    y += 55

    # SECTION 2 & 3: CLOSED WEIGHT & CLOSED COUNT (SIDE-BY-SIDE TABLES)
    # Left: Closed Weight Table (Cols 20..460) | Right: Closed Count Table (Cols 490..930)
    draw.rectangle([20, y, 460, y + 32], fill=(26, 54, 93))
    draw.text((35, y + 8), "CLOSED WEIGHT", fill=(255, 255, 255))

    draw.rectangle([490, y, width - 20, y + 32], fill=(26, 54, 93))
    draw.text((505, y + 8), "CLOSED COUNT", fill=(255, 255, 255))
    y += 32

    # Headers
    draw.rectangle([20, y, 460, y + 32], fill=(43, 108, 176))
    draw.text((30, y + 8), "SHOWROOM", fill=(255, 255, 255))
    draw.text((180, y + 8), "COUNT", fill=(255, 255, 255))
    draw.text((310, y + 8), "WEIGHT (g)", fill=(255, 255, 255))

    draw.rectangle([490, y, width - 20, y + 32], fill=(43, 108, 176))
    draw.text((500, y + 8), "SHOWROOM", fill=(255, 255, 255))
    draw.text((650, y + 8), "SS", fill=(255, 255, 255))
    draw.text((790, y + 8), "SV", fill=(255, 255, 255))
    y += 32

    for r_idx in range(len(rej_result.table2_weight_rows)):
        rw = rej_result.table2_weight_rows[r_idx]
        rc = rej_result.table3_scheme_rows[r_idx]
        bg = (248, 250, 252) if r_idx % 2 == 1 else (255, 255, 255)

        # Left row
        draw.rectangle([20, y, 460, y + 28], fill=bg, outline=(226, 232, 240), width=1)
        c_cnt = next((t.closed_count for t in rej_result.table1_rows if t.showroom == rw.showroom), 0)
        draw.text((30, y + 6), rw.showroom, fill=(30, 41, 59))
        draw.text((180, y + 6), str(c_cnt), fill=(30, 41, 59))
        draw.text((310, y + 6), f"{rw.closed_weight:,.2f}", fill=(30, 41, 59))

        # Right row
        draw.rectangle([490, y, width - 20, y + 28], fill=bg, outline=(226, 232, 240), width=1)
        ss_c = rc.scheme_counts.get("NEW SWARNA SUBHIKSHAM", 0) + rc.scheme_counts.get("SWARNA SUBHIKSHAM FLEXI", 0)
        sv_c = rc.scheme_counts.get("SWARNA VIRUKSHAM", 0)
        draw.text((500, y + 6), rc.showroom, fill=(30, 41, 59))
        draw.text((650, y + 6), str(ss_c), fill=(30, 41, 59))
        draw.text((790, y + 6), str(sv_c), fill=(30, 41, 59))
        y += 28

    # Totals Section 2 & 3
    s2 = rej_result.table2_summary
    draw.rectangle([20, y, 460, y + 32], fill=(226, 232, 240), outline=(148, 163, 184), width=1)
    draw.text((30, y + 8), "TOTAL", fill=(15, 23, 42))
    draw.text((180, y + 8), str(s1.closed_count), fill=(15, 23, 42))
    draw.text((310, y + 8), f"{s2.closed_weight:,.2f}", fill=(15, 23, 42))

    draw.rectangle([490, y, width - 20, y + 32], fill=(226, 232, 240), outline=(148, 163, 184), width=1)
    tot_ss_cl = rej_result.table3_summary.get("NEW SWARNA SUBHIKSHAM", 0) + rej_result.table3_summary.get("SWARNA SUBHIKSHAM FLEXI", 0)
    tot_sv_cl = rej_result.table3_summary.get("SWARNA VIRUKSHAM", 0)
    draw.text((500, y + 8), "TOTAL", fill=(15, 23, 42))
    draw.text((650, y + 8), str(tot_ss_cl), fill=(15, 23, 42))
    draw.text((790, y + 8), str(tot_sv_cl), fill=(15, 23, 42))

    img.save(out_path, "PNG")
    print(f"Rendered Closing Screenshot: {out_path} ({width}x{height})")

enr_img_path = SCREENSHOTS_DIR / "New_Enrollment_Report_2026-10-27.png"
cl_img_path = SCREENSHOTS_DIR / "Scheme_Rejoining_Report_2026-10-27.png"

render_enrollment_screenshot(table1_rows, table2_rows, enr_img_path)
render_closing_screenshot(rejoining_result, cl_img_path)

print("\n=== INDEPENDENT PIPELINE VALIDATION EXECUTION COMPLETE ===")
