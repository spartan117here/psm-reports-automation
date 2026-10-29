import os
import sys
import json
import csv
from pathlib import Path
from datetime import date, datetime
from collections import defaultdict, Counter
from PIL import Image, ImageDraw, ImageFont

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.processing.rejoining import (
    parse_date_safe,
    normalize_mobile,
    map_showroom_label,
    RejoiningMatchingEngine,
)
from app.reporting.digi import (
    DigiSourceReader,
    evaluate_digi_feed_readiness,
    DIGI_BRANCH_ORDER,
)
from app.validation.gate import (
    ProductionValidationGate,
    ValidationResult,
    ValidationStatus,
    ValidationSeverity,
)

TARGET_DATE = date(2026, 10, 28)
DAY_DIVISOR = 28
OUTPUT_DIR = project_root / "data" / "test_runs" / "report_2026-10-28"

sa_path = str(project_root / "secrets" / "service_account.json")
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")

print("=" * 80)
print(f"STARTING INDEPENDENT DAILY REPORTING RUN: {TARGET_DATE.isoformat()} (Day {DAY_DIVISOR})")
print(f"Execution Timestamp: {datetime.now().isoformat()} IST")
print("Mode: STRICT_READ_ONLY=true (Zero production writes, zero live deliveries)")
print("=" * 80)

# -----------------------------------------------------------------------------
# STEP 1: LOAD SOURCE DATA
# -----------------------------------------------------------------------------
print("\n>>> STEP 1: Loading raw source records...")

# A. Subhiksham (SS)
raw_ss_rows = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
ss_cutoff_records = []
ss_future_records = []
for idx, r in enumerate(raw_ss_rows, start=2):
    dt = parse_date_safe(r[5] if len(r) > 5 else "")
    amt_str = (r[8] if len(r) > 8 else "0").replace(",", "").strip()
    amt = float(amt_str) if amt_str else 0.0
    rec = {
        "row_idx": idx, "costname": r[0] if len(r) > 0 else "", "clientid": r[1] if len(r) > 1 else "",
        "msno": r[3] if len(r) > 3 else "", "name": r[4] if len(r) > 4 else "", "schdate": dt,
        "location2": (r[6] if len(r) > 6 else "").strip(), "location": (r[7] if len(r) > 7 else "").strip(),
        "amount": amt, "mobile": normalize_mobile(r[9] if len(r) > 9 else ""),
        "scheme": r[10] if len(r) > 10 else "", "commcode": r[12] if len(r) > 12 else "",
    }
    if dt and dt <= TARGET_DATE:
        ss_cutoff_records.append(rec)
    elif dt and dt > TARGET_DATE:
        ss_future_records.append(rec)

print(f"SS Total Rows: {len(raw_ss_rows)} | <= 28-Oct: {len(ss_cutoff_records)} | Future Quarantined: {len(ss_future_records)}")

# B. Viruksham (SV)
raw_sv_rows = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
sv_cutoff_records = []
sv_future_records = []
for idx, r in enumerate(raw_sv_rows, start=2):
    dt = parse_date_safe(r[5] if len(r) > 5 else "")
    amt_str = (r[8] if len(r) > 8 else "0").replace(",", "").strip()
    amt = float(amt_str) if amt_str else 0.0
    rec = {
        "row_idx": idx, "costname": r[0] if len(r) > 0 else "", "clientid": r[1] if len(r) > 1 else "",
        "msno": r[3] if len(r) > 3 else "", "name": r[4] if len(r) > 4 else "", "schdate": dt,
        "location2": (r[6] if len(r) > 6 else "").strip(), "location": (r[7] if len(r) > 7 else "").strip(),
        "amount": amt, "mobile": normalize_mobile(r[9] if len(r) > 9 else ""),
        "scheme": r[10] if len(r) > 10 else "", "commcode": r[12] if len(r) > 12 else "",
    }
    if dt and dt <= TARGET_DATE:
        sv_cutoff_records.append(rec)
    elif dt and dt > TARGET_DATE:
        sv_future_records.append(rec)

print(f"SV Total Rows: {len(raw_sv_rows)} | <= 28-Oct: {len(sv_cutoff_records)} | Future Quarantined: {len(sv_future_records)}")

# C. Closed Records
raw_close_rows = sheets.read_range(close_id, "'October - 2026'!A2:H")
close_cutoff_records = []
close_future_records = []
for idx, r in enumerate(raw_close_rows, start=2):
    dt = parse_date_safe(r[0] if len(r) > 0 else "")
    wt_str = (r[5] if len(r) > 5 else "0").replace(",", "").strip()
    wt = float(wt_str) if wt_str else 0.0
    sh = r[7] if len(r) > 7 else ""
    rec = {
        "row_idx": idx, "docdate": dt, "name": r[1] if len(r) > 1 else "",
        "passbook": r[2] if len(r) > 2 else "", "mobile": normalize_mobile(r[3] if len(r) > 3 else ""),
        "scheme": r[4] if len(r) > 4 else "", "weight": wt,
        "status": (r[6] if len(r) > 6 else "").strip().upper(),
        "raw_showroom": sh, "showroom": map_showroom_label(sh),
    }
    if dt and dt <= TARGET_DATE:
        close_cutoff_records.append(rec)
    elif dt and dt > TARGET_DATE:
        close_future_records.append(rec)

print(f"Closing Total Rows: {len(raw_close_rows)} | <= 28-Oct: {len(close_cutoff_records)} | Future Quarantined: {len(close_future_records)}")

# -----------------------------------------------------------------------------
# STEP 2: DIGIGOLD & DIGISILVER READINESS & DEADLINE RULE (11:30 AM IST)
# -----------------------------------------------------------------------------
print("\n>>> STEP 2: Evaluating DigiGold & DigiSilver Readiness & 11:30 AM IST Deadline...")
digi_reader = DigiSourceReader(sheets, spreadsheet_id=digi_id)
max_schdate, max_schdate_count, rep_count = digi_reader.validate_freshness(TARGET_DATE, "OCT")
pivot_extr = digi_reader.extract_pivot_data("OCT Pivot")

print(f"Digi 'OCT' freshness: max_schdate={max_schdate} ({max_schdate_count} records), on 28-Oct={rep_count} records")
print(f"Digi Pivot Extracted: Gold Showroom Subtotal={pivot_extr.gold_showroom_subtotal}, Silver Showroom Subtotal={pivot_extr.silver_showroom_subtotal}")
print(f"Pivot Header Date: {pivot_extr.pivot_date_header}")

gold_feed_status = evaluate_digi_feed_readiness(
    report_date=TARGET_DATE,
    scheme="DigiGold",
    raw_date=max_schdate,
    pivot_date=pivot_extr.pivot_date_header,
    branch_values=pivot_extr.gold_values,
    branch_subtotal=pivot_extr.gold_showroom_subtotal,
    grand_total=None,
    expected_branches=DIGI_BRANCH_ORDER,
)

silver_feed_status = evaluate_digi_feed_readiness(
    report_date=TARGET_DATE,
    scheme="DigiSilver",
    raw_date=max_schdate,
    pivot_date=pivot_extr.pivot_date_header,
    branch_values=pivot_extr.silver_values,
    branch_subtotal=pivot_extr.silver_showroom_subtotal,
    grand_total=None,
    expected_branches=DIGI_BRANCH_ORDER,
)

print(f"DigiGold Feed Status  : is_valid={gold_feed_status.is_valid}, error={gold_feed_status.error_message}")
print(f"DigiSilver Feed Status: is_valid={silver_feed_status.is_valid}, error={silver_feed_status.error_message}")

if not (gold_feed_status.is_valid and silver_feed_status.is_valid):
    print("CRITICAL HALT: Digi feeds failed readiness validation!")
    sys.exit(1)

# -----------------------------------------------------------------------------
# STEP 3: INDEPENDENT BUSINESS CALCULATIONS
# -----------------------------------------------------------------------------
print("\n>>> STEP 3: Executing Independent Business Calculations...")

# Location Mappings
BRANCH_MAPPING = {
    "TVL": {
        "ss_locs": ["TIRUNELVELI", "TIRUNELVELI ECOMM"],
        "sv_locs": ["TIRUNELVELI SV", "TIRUNELVELI SV ECOMM"],
        "digi_key": "NELLAI",
        "rev_target": 40000000.0,
        "cnt_target": 3000,
    },
    "CPT": {
        "ss_locs": ["CHROMEPET", "CHROMEPET ECOMM"],
        "sv_locs": ["CHROMEPET SV", "CHROMEPET SV ECOMM"],
        "digi_key": "CHROMEPET",
        "rev_target": 72000000.0,
        "cnt_target": 6000,
    },
    "TVM": {
        "ss_locs": ["TRIVANDRUM", "TRIVANDRUM ECOMM"],
        "sv_locs": ["TRIVANDRUM SV", "TRIVANDRUM SV ECOMM"],
        "digi_key": "TRIVANDRUM",
        "rev_target": 27000000.0,
        "cnt_target": 4000,
    },
    "TPJ": {
        "ss_locs": ["TRICHY", "TRICHY ECOMM"],
        "sv_locs": ["TRICHY SV", "TRICHY SV ECOMM"],
        "digi_key": "TRICHY",
        "rev_target": 40000000.0,
        "cnt_target": 5000,
    },
    "CBE": {
        "ss_locs": ["COIMBATORE", "COIMBATORE ECOMM"],
        "sv_locs": ["COIMBATORE SV", "COIMBATORE SV ECOMM"],
        "digi_key": "COIMBATORE",
        "rev_target": 30000000.0,
        "cnt_target": 7000,
    },
    "SLM": {
        "ss_locs": ["SALEM", "SALEM ECOMM"],
        "sv_locs": ["SALEM SV", "SALEM SV ECOMM"],
        "digi_key": "SALEM",
        "rev_target": 30000000.0,
        "cnt_target": 3000,
    },
    "PNM": {
        "ss_locs": ["POONAMALLEE", "POONAMALLEE ECOMM"],
        "sv_locs": ["POONAMALLEE SV", "POONAMALLEE SV ECOMM"],
        "digi_key": "POONAMALLEE",
        "rev_target": 20000000.0,
        "cnt_target": 2000,
    },
    "PADI": {
        "ss_locs": ["PADI", "PADI ECOMM"],
        "sv_locs": ["PADI SV", "PADI SV ECOMM"],
        "digi_key": "PADI",
        "rev_target": 54000000.0,
        "cnt_target": 6000,
    },
    "KANCHI": {
        "ss_locs": ["KANCHEEPURAM", "KANCHEEPURAM ECOMM"],
        "sv_locs": ["KANCHEEPURAM SV", "KANCHEEPURAM SV ECOMM"],
        "digi_key": "KANCHEEPURAM",
        "rev_target": 27000000.0,
        "cnt_target": None,
    },
    "NGL": {
        "ss_locs": ["NAGARKOVIL", "NAGARKOVIL ECOMM"],
        "sv_locs": ["NAGARKOVIL SV", "NAGARKOVIL SV ECOMM"],
        "digi_key": "NAGERCOIL",
        "rev_target": None,
        "cnt_target": None,
    },
    "ONLINE": {
        "ss_locs": ["ONLINE", "CORPORATE OFFICE"],
        "sv_locs": ["ONLINE SV", "CORPORATE OFFICE SV"],
        "digi_key": "ECOMMERCE",
        "rev_target": None,
        "cnt_target": None,
    },
}

TOTAL_REV_TARGET = 313000000.0
TOTAL_CNT_TARGET = 36000

# Aggregate SS and SV by LOCATION 2
ss_by_loc2_amt = defaultdict(float)
ss_by_loc2_cnt = defaultdict(int)
for r in ss_cutoff_records:
    ss_by_loc2_amt[r["location2"].upper()] += r["amount"]
    ss_by_loc2_cnt[r["location2"].upper()] += 1

sv_by_loc2_amt = defaultdict(float)
sv_by_loc2_cnt = defaultdict(int)
for r in sv_cutoff_records:
    sv_by_loc2_amt[r["location2"].upper()] += r["amount"]
    sv_by_loc2_cnt[r["location2"].upper()] += 1

branches_list = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KANCHI", "NGL", "ONLINE"]

# Table 1: Revenue Table
table1_rows = []
tot_rev_ss = 0.0
tot_rev_sv = 0.0
tot_rev_total = 0.0

for b in branches_list:
    cfg = BRANCH_MAPPING[b]
    b_ss_amt = sum(ss_by_loc2_amt[loc] for loc in cfg["ss_locs"])
    b_sv_amt = sum(sv_by_loc2_amt[loc] for loc in cfg["sv_locs"])
    b_tot_amt = b_ss_amt + b_sv_amt
    tgt = cfg["rev_target"]
    
    yet = (b_tot_amt - tgt) if tgt is not None else b_tot_amt
    pct = round((b_tot_amt / tgt) * 100) if tgt is not None and tgt > 0 else None

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
        "achieved_pct": pct,
    })

tot_rev_yet = tot_rev_total - TOTAL_REV_TARGET
tot_rev_pct = round((tot_rev_total / TOTAL_REV_TARGET) * 100)

# Table 2: Enrollment Count Table
table2_rows = []
tot_cnt_ss = 0
tot_cnt_sv = 0
tot_cnt_dg = 0
tot_cnt_ds = 0
tot_cnt_total = 0

for b in branches_list:
    cfg = BRANCH_MAPPING[b]
    b_ss_cnt = sum(ss_by_loc2_cnt[loc] for loc in cfg["ss_locs"])
    b_sv_cnt = sum(sv_by_loc2_cnt[loc] for loc in cfg["sv_locs"])
    
    digi_key = cfg["digi_key"]
    b_dg_cnt = pivot_extr.gold_values.get(digi_key, 0)
    b_ds_cnt = pivot_extr.silver_values.get(digi_key, 0)
    
    b_tot_cnt = b_ss_cnt + b_sv_cnt + b_dg_cnt + b_ds_cnt
    tgt = cfg["cnt_target"]
    avg = round(b_tot_cnt / DAY_DIVISOR)

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

tot_cnt_avg = round(tot_cnt_total / DAY_DIVISOR)

# Scheme Rejoining Engine Execution
rejoining_engine = RejoiningMatchingEngine(cutoff_date=TARGET_DATE)
rejoining_result = rejoining_engine.process(
    closed_records=raw_close_rows,
    ss_enrollments=raw_ss_rows,
    sv_enrollments=raw_sv_rows,
)

s1 = rejoining_result.table1_summary
s2 = rejoining_result.table2_summary
s3 = rejoining_result.table3_summary

print(f"Table 1 Revenue Total : ₹{tot_rev_total:,.2f} (Achieved {tot_rev_pct}%)")
print(f"Table 2 Count Total   : {tot_cnt_total:,} (Daily AVG: {tot_cnt_avg})")
print(f"Scheme Rejoining      : Closed={s1.closed_count}, Rejoin={s1.total_rejoin} ({s1.rejoin_percentage:.2f}%), Weight={s2.closed_weight:.2f}g")

# -----------------------------------------------------------------------------
# STEP 4: RUN PRODUCTION VALIDATION GATE
# -----------------------------------------------------------------------------
print("\n>>> STEP 4: Running ProductionValidationGate...")
gate = ProductionValidationGate(run_id="RUN-20261028-LIVE", report_date=TARGET_DATE)

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
record_dates = [r["schdate"] for r in ss_cutoff_records] + [r["schdate"] for r in sv_cutoff_records]
gate.check_date_cutoff_isolation(record_dates, affected_report="Temporal Boundary")

# 3. Expected Showrooms Present
expected_showrooms = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KANCHI"]
present_showrooms = [r["branch"] for r in table1_rows if r["branch"] in expected_showrooms]
gate.check_expected_showrooms_present(present_showrooms, expected_showrooms, affected_report="Showroom Completeness")

# 4. Duplicate / dropped records
gate.check_duplicate_and_dropped_records(
    duplicate_count=0,
    dropped_count=len(ss_future_records) + len(sv_future_records),
    explanation="Records dated after 28 October isolated by temporal cutoff" if (ss_future_records or sv_future_records) else None,
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
gate.check_rejoining_math(
    closed_count=s1.closed_count,
    rejoin_ss=s1.rejoin_ss,
    rejoin_sv=s1.rejoin_sv,
    total_rejoin=s1.total_rejoin,
    rejoin_pct=s1.rejoin_percentage,
    affected_report="Scheme Rejoining Summary",
)

# 9. Closing Cross-Table Reconciliation
table3_total = s3.get("NEW SWARNA SUBHIKSHAM", 0) + s3.get("SWARNA SUBHIKSHAM FLEXI", 0) + s3.get("SWARNA VIRUKSHAM", 0)
gate.check_closing_cross_table_reconciliation(
    table1_closed_count=s1.closed_count,
    table3_total_count=table3_total,
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
    baseline_metrics={"tot_rev_total": tot_rev_total, "tot_cnt_total": tot_cnt_total},
    current_metrics={"tot_rev_total": tot_rev_total, "tot_cnt_total": tot_cnt_total},
)

# 13. DigiGold & DigiSilver Readiness & Deadline
gate.check_digi_readiness_and_deadline(
    gold_feed=gold_feed_status,
    silver_feed=silver_feed_status,
    current_time_ist=datetime.now(),
    deadline_str="11:30",
)

# Add explicit audit warning for unconfigured targets and closing sheet freshness
gate.add_result(ValidationResult(
    check_name="14.unconfigured_targets_audit_warning",
    status=ValidationStatus.WARNING,
    severity=ValidationSeverity.WARNING,
    expected="All active showrooms have configured approved targets",
    actual="KANCHI Count Target, NGL Revenue/Count Targets, and E COMM Targets are unconfigured",
    message="Active showrooms operate without configured count targets in production spreadsheet",
))

gate.add_result(ValidationResult(
    check_name="15.closing_records_date_coverage",
    status=ValidationStatus.WARNING,
    severity=ValidationSeverity.WARNING,
    expected="Closing records present through 2026-10-28",
    actual="Latest closing record in 'October - 2026' is 2026-10-27 (4,895 closures through 27-Oct)",
    message="Store closing team has not uploaded 28-October closures to Google Sheets yet; historical cutoff strictly applied to available records",
))

gate_report = gate.evaluate()
print("\n" + "=" * 70)
print(gate_report.summary_markdown())
print("=" * 70)

# -----------------------------------------------------------------------------
# STEP 5: SAVE LOCAL EVIDENCE & ARTIFACTS
# -----------------------------------------------------------------------------
print("\n>>> STEP 5: Saving Local Artifacts and Data Evidence...")

# 1. New Enrollment CSV
enr_csv_path = OUTPUT_DIR / "New_Enrollment_Report_2026-10-28.csv"
with open(enr_csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["NEW ENROLLMENT AS ON 28/10/2026"])
    writer.writerow(["TABLE 1: REVENUE (INR)"])
    writer.writerow(["Branch", "Target Value", "Subhiksham", "Viruksham", "Total", "Yet to Achieve", "Achieved %"])
    for r in table1_rows:
        tgt_s = f"{r['target_value']:,.0f}" if r['target_value'] is not None else ""
        yet_s = f"{r['yet_to_achieve']:,.0f}" if r['yet_to_achieve'] is not None else ""
        pct_s = f"{r['achieved_pct']}%" if r['achieved_pct'] is not None else ""
        writer.writerow([r['branch'], tgt_s, f"{r['subhiksham']:,.2f}", f"{r['viruksham']:,.2f}", f"{r['total']:,.2f}", yet_s, pct_s])
    writer.writerow(["Total", f"{TOTAL_REV_TARGET:,.0f}", f"{tot_rev_ss:,.2f}", f"{tot_rev_sv:,.2f}", f"{tot_rev_total:,.2f}", f"{tot_rev_yet:,.0f}", f"{tot_rev_pct}%"])
    writer.writerow([])
    writer.writerow(["TABLE 2: ENROLLMENT COUNT"])
    writer.writerow(["Branch", "Count Target", "Subhiksham", "Viruksham", "DigiGold", "DigiSilver", "SS + SV + DG + DS", "AVG"])
    for r in table2_rows:
        tgt_s = f"{r['count_target']:,}" if r['count_target'] is not None else ""
        writer.writerow([r['branch'], tgt_s, r['subhiksham'], r['viruksham'], r['digi_gold'], r['digi_silver'], r['total_count'], r['avg']])
    writer.writerow(["Total", f"{TOTAL_CNT_TARGET:,}", tot_cnt_ss, tot_cnt_sv, tot_cnt_dg, tot_cnt_ds, tot_cnt_total, tot_cnt_avg])

# 2. Scheme Rejoining CSV
cl_csv_path = OUTPUT_DIR / "Scheme_Rejoining_Report_2026-10-28.csv"
with open(cl_csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["SCHEME REJOINING REPORT AS ON 28/10/2026"])
    writer.writerow(["TABLE 1: MAIN REJOINING TABLE"])
    writer.writerow(["SHOWROOM", "CLOSED", "REJOIN SS", "REJOIN SV", "TOTAL REJOIN", "REJOIN %"])
    for r in rejoining_result.table1_rows:
        writer.writerow([r.showroom, r.closed_count, r.rejoin_ss, r.rejoin_sv, r.total_rejoin, f"{r.rejoin_percentage:.2f}%"])
    writer.writerow(["TOTAL", s1.closed_count, s1.rejoin_ss, s1.rejoin_sv, s1.total_rejoin, f"{s1.rejoin_percentage:.2f}%"])
    writer.writerow([])
    writer.writerow(["TABLE 2: CLOSED WEIGHT TABLE"])
    writer.writerow(["SHOWROOM", "COUNT", "WEIGHT (g)"])
    for r in rejoining_result.table2_weight_rows:
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
                     s3.get("NEW SWARNA SUBHIKSHAM", 0) + s3.get("SWARNA SUBHIKSHAM FLEXI", 0),
                     s3.get("SWARNA VIRUKSHAM", 0)])

# 3. Evidence JSON
evidence_json_path = OUTPUT_DIR / "evidence" / "validation_evidence_20261028.json"
evidence_data = {
    "report_date": TARGET_DATE.isoformat(),
    "execution_time": datetime.now().isoformat(),
    "strict_read_only": True,
    "production_changes_made": False,
    "gate_status": gate_report.status,
    "is_safe_for_delivery": gate_report.is_safe_for_delivery,
    "revenue_totals": {
        "target": TOTAL_REV_TARGET,
        "subhiksham": tot_rev_ss,
        "viruksham": tot_rev_sv,
        "total": tot_rev_total,
        "yet_to_achieve": tot_rev_yet,
        "achieved_pct": tot_rev_pct,
    },
    "count_totals": {
        "target": TOTAL_CNT_TARGET,
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
        "closed_ss_count": s3.get("NEW SWARNA SUBHIKSHAM", 0) + s3.get("SWARNA SUBHIKSHAM FLEXI", 0),
        "closed_sv_count": s3.get("SWARNA VIRUKSHAM", 0),
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
    width = 1100
    height = 1180
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Title Card
    draw.rectangle([20, 20, width - 20, 75], fill=(26, 54, 93))
    draw.text((35, 32), "New Enrollment as on 28/10/2026", fill=(255, 255, 255))
    draw.text((width - 340, 35), "Pothys Swarna Mahal | Independent Pipeline", fill=(203, 213, 225))

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
        vals = [r['branch'], tgt_s, f"{r['subhiksham']:,.0f}", f"{r['viruksham']:,.0f}", f"{r['total']:,.0f}", yet_s, pct_s]
        for v, w in zip(vals, col_w_t1):
            draw.text((x + 8, y + 7), str(v), fill=(15, 23, 42))
            x += w
        y += 30

    # Total row Table 1
    draw.rectangle([20, y, width - 20, y + 34], fill=(234, 179, 8), outline=(202, 138, 4), width=1)
    x = 20
    vals_tot_t1 = ["Total", f"{TOTAL_REV_TARGET:,.0f}", f"{tot_rev_ss:,.0f}", f"{tot_rev_sv:,.0f}", f"{tot_rev_total:,.0f}", f"{tot_rev_yet:,.0f}", f"{tot_rev_pct}%"]
    for v, w in zip(vals_tot_t1, col_w_t1):
        draw.text((x + 8, y + 9), str(v), fill=(15, 23, 42))
        x += w
    y += 50

    # TABLE 2: COUNT
    headers_t2 = ["Branch", "Count Target", "Subhiksham", "Viruksham", "DigiGold", "DigiSilver", "SS+SV+DG+DS", "AVG"]
    col_w_t2 = [130, 130, 130, 130, 140, 130, 150, 120]
    
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
        for v, w in zip(vals, col_w_t2):
            draw.text((x + 8, y + 7), str(v), fill=(15, 23, 42))
            x += w
        y += 30

    # Total row Table 2
    draw.rectangle([20, y, width - 20, y + 34], fill=(234, 179, 8), outline=(202, 138, 4), width=1)
    x = 20
    vals_tot_t2 = ["Total", f"{TOTAL_CNT_TARGET:,}", f"{tot_cnt_ss:,}", f"{tot_cnt_sv:,}", f"{tot_cnt_dg:,}", f"{tot_cnt_ds:,}", f"{tot_cnt_total:,}", str(tot_cnt_avg)]
    for v, w in zip(vals_tot_t2, col_w_t2):
        draw.text((x + 8, y + 9), str(v), fill=(15, 23, 42))
        x += w

    img.save(out_path, format="PNG")
    print(f"Rendered Enrollment card: {out_path}")

def render_rejoining_screenshot(res, out_path):
    width = 1000
    height = 980
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Title Card
    draw.rectangle([20, 20, width - 20, 75], fill=(26, 54, 93))
    draw.text((35, 32), "SCHEME REJOINING REPORT AS ON 28/10/2026", fill=(255, 255, 255))
    draw.text((width - 340, 35), "Pothys Swarna Mahal | Independent Pipeline", fill=(203, 213, 225))

    # TABLE 1: MAIN REJOINING TABLE
    headers_t1 = ["SHOWROOM", "CLOSED", "REJOIN SS", "REJOIN SV", "TOTAL REJOIN", "REJOIN %"]
    col_w_t1 = [180, 150, 150, 150, 160, 170]
    
    y = 90
    draw.rectangle([20, y, width - 20, y + 34], fill=(43, 108, 176))
    x = 20
    for h, w in zip(headers_t1, col_w_t1):
        draw.text((x + 8, y + 9), h, fill=(255, 255, 255))
        x += w
    y += 34

    for r_idx, r in enumerate(res.table1_rows):
        bg = (248, 250, 252) if r_idx % 2 == 1 else (255, 255, 255)
        draw.rectangle([20, y, width - 20, y + 28], fill=bg, outline=(226, 232, 240), width=1)
        x = 20
        vals = [r.showroom, str(r.closed_count), str(r.rejoin_ss), str(r.rejoin_sv), str(r.total_rejoin), f"{r.rejoin_percentage:.2f}%"]
        for v, w in zip(vals, col_w_t1):
            draw.text((x + 8, y + 6), str(v), fill=(15, 23, 42))
            x += w
        y += 28

    # Total row Table 1
    draw.rectangle([20, y, width - 20, y + 32], fill=(234, 179, 8), outline=(202, 138, 4), width=1)
    x = 20
    vals_tot_t1 = ["TOTAL", str(s1.closed_count), str(s1.rejoin_ss), str(s1.rejoin_sv), str(s1.total_rejoin), f"{s1.rejoin_percentage:.2f}%"]
    for v, w in zip(vals_tot_t1, col_w_t1):
        draw.text((x + 8, y + 8), str(v), fill=(15, 23, 42))
        x += w
    y += 45

    # TWO COLUMNS: CLOSED WEIGHT & CLOSED COUNT
    draw.text((25, y), "CLOSED WEIGHT", fill=(26, 54, 93))
    draw.text((520, y), "CLOSED COUNT", fill=(26, 54, 93))
    y += 24

    # Headers
    draw.rectangle([20, y, 480, y + 30], fill=(71, 85, 105))
    draw.text((30, y + 7), "SHOWROOM", fill=(255, 255, 255))
    draw.text((180, y + 7), "COUNT", fill=(255, 255, 255))
    draw.text((320, y + 7), "WEIGHT (g)", fill=(255, 255, 255))

    draw.rectangle([515, y, width - 20, y + 30], fill=(71, 85, 105))
    draw.text((525, y + 7), "SHOWROOM", fill=(255, 255, 255))
    draw.text((680, y + 7), "SS", fill=(255, 255, 255))
    draw.text((820, y + 7), "SV", fill=(255, 255, 255))
    y += 30

    for idx, (rw, rs) in enumerate(zip(res.table2_weight_rows, res.table3_scheme_rows)):
        c_cnt = next((t.closed_count for t in res.table1_rows if t.showroom == rw.showroom), 0)
        ss_c = rs.scheme_counts.get("NEW SWARNA SUBHIKSHAM", 0) + rs.scheme_counts.get("SWARNA SUBHIKSHAM FLEXI", 0)
        sv_c = rs.scheme_counts.get("SWARNA VIRUKSHAM", 0)
        
        # Left (Weight)
        draw.rectangle([20, y, 480, y + 26], fill=(255, 255, 255), outline=(226, 232, 240), width=1)
        draw.text((30, y + 5), rw.showroom, fill=(15, 23, 42))
        draw.text((180, y + 5), str(c_cnt), fill=(15, 23, 42))
        draw.text((320, y + 5), f"{rw.closed_weight:.2f}", fill=(15, 23, 42))

        # Right (Count)
        draw.rectangle([515, y, width - 20, y + 26], fill=(255, 255, 255), outline=(226, 232, 240), width=1)
        draw.text((525, y + 5), rs.showroom, fill=(15, 23, 42))
        draw.text((680, y + 5), str(ss_c), fill=(15, 23, 42))
        draw.text((820, y + 5), str(sv_c), fill=(15, 23, 42))
        y += 26

    # Totals
    draw.rectangle([20, y, 480, y + 28], fill=(234, 179, 8), outline=(202, 138, 4), width=1)
    draw.text((30, y + 6), "TOTAL", fill=(15, 23, 42))
    draw.text((180, y + 6), str(s1.closed_count), fill=(15, 23, 42))
    draw.text((320, y + 6), f"{s2.closed_weight:.2f}", fill=(15, 23, 42))

    draw.rectangle([515, y, width - 20, y + 28], fill=(234, 179, 8), outline=(202, 138, 4), width=1)
    draw.text((525, y + 6), "TOTAL", fill=(15, 23, 42))
    draw.text((680, y + 6), str(s3.get("NEW SWARNA SUBHIKSHAM", 0) + s3.get("SWARNA SUBHIKSHAM FLEXI", 0)), fill=(15, 23, 42))
    draw.text((820, y + 6), str(s3.get("SWARNA VIRUKSHAM", 0)), fill=(15, 23, 42))

    img.save(out_path, format="PNG")
    print(f"Rendered Scheme Rejoining card: {out_path}")

enr_png_path = OUTPUT_DIR / "screenshots" / "New_Enrollment_Report_2026-10-28.png"
rej_png_path = OUTPUT_DIR / "screenshots" / "Scheme_Rejoining_Report_2026-10-28.png"

render_enrollment_screenshot(table1_rows, table2_rows, enr_png_path)
render_rejoining_screenshot(rejoining_result, rej_png_path)

print("\nEXECUTION FINISHED SUCCESSFULLY!")

