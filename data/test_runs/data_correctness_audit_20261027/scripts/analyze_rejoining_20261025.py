import os
import sys
import json
from pathlib import Path
from collections import defaultdict
from datetime import date, datetime
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")
load_dotenv(project_root / "scripts" / ".env")

from app.google_sheets.client import GoogleApiSheetsService
from app.reporting.closing import DEFAULT_CLOSED_REJOINING_SPREADSHEET_ID, map_showroom_label
from app.reporting.digi import parse_schdate

sa_path = str(project_root / "secrets" / "service_account.json")
sheets_service = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
cl_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID", DEFAULT_CLOSED_REJOINING_SPREADSHEET_ID)

rows = sheets_service.read_range(cl_id, "October - 2026!A2:J")
print(f"Total rows in sheet: {len(rows)}")

rows_25 = []
rows_after_25 = []

def parse_any_date(val):
    if not val:
        return None
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None

for r in rows:
    if not r or not r[0].strip():
        continue
    dt = parse_any_date(r[0].strip())
    if not dt:
        continue
    if dt <= date(2026, 10, 25):
        rows_25.append(r)
    else:
        rows_after_25.append(r)

print(f"Rows <= 2026-10-25: {len(rows_25)}")
print(f"Rows after 2026-10-25: {len(rows_after_25)}")

showroom_counts = defaultdict(int)
showroom_weights = defaultdict(float)
showroom_ss_closed = defaultdict(int)
showroom_sv_closed = defaultdict(int)
showroom_rejoin_ss = defaultdict(int)
showroom_rejoin_sv = defaultdict(int)

for r in rows_25:
    raw_sh = r[7].strip() if len(r) > 7 else ""
    sh = map_showroom_label(raw_sh)
    sch = r[4].strip() if len(r) > 4 else ""
    wt = float(r[5].replace(",", "").strip() or 0.0) if len(r) > 5 and r[5].strip() else 0.0
    rej_ss = 1 if len(r) > 8 and r[8].strip() and r[8].strip() not in ("0", "FALSE", "") else 0
    rej_sv = 1 if len(r) > 9 and r[9].strip() and r[9].strip() not in ("0", "FALSE", "") else 0
    
    showroom_counts[sh] += 1
    showroom_weights[sh] += wt
    if "VIRUKSHAM" in sch.upper():
        showroom_sv_closed[sh] += 1
    else:
        showroom_ss_closed[sh] += 1
    showroom_rejoin_ss[sh] += rej_ss
    showroom_rejoin_sv[sh] += rej_sv

PHYSICAL = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KPM"]
print("\n--- 9 PHYSICAL SHOWROOMS <= 2026-10-25 ---")
print(f"{'Showroom':10s} | {'Closed':6s} | {'Rejoin SS':9s} | {'Rejoin SV':9s} | {'Total Rejoin':12s} | {'Rejoin %':8s} | {'Weight (g)':12s} | {'Closed SS':9s} | {'Closed SV':9s}")
print("-" * 105)

for sh in PHYSICAL:
    tot = showroom_counts[sh]
    wt = showroom_weights[sh]
    ss_c = showroom_ss_closed[sh]
    sv_c = showroom_sv_closed[sh]
    r_ss = showroom_rejoin_ss[sh]
    r_sv = showroom_rejoin_sv[sh]
    tot_rej = r_ss + r_sv
    pct = (tot_rej / tot * 100) if tot else 0.0
    print(f"{sh:10s} | {tot:6d} | {r_ss:9d} | {r_sv:9d} | {tot_rej:12d} | {pct:7.2f}% | {wt:12.3f} | {ss_c:9d} | {sv_c:9d}")

tot_closed = sum(showroom_counts[sh] for sh in PHYSICAL)
tot_wt = sum(showroom_weights[sh] for sh in PHYSICAL)
tot_ss_c = sum(showroom_ss_closed[sh] for sh in PHYSICAL)
tot_sv_c = sum(showroom_sv_closed[sh] for sh in PHYSICAL)
tot_r_ss = sum(showroom_rejoin_ss[sh] for sh in PHYSICAL)
tot_r_sv = sum(showroom_rejoin_sv[sh] for sh in PHYSICAL)
tot_rej_all = tot_r_ss + tot_r_sv
pct_all = (tot_rej_all / tot_closed * 100) if tot_closed else 0.0

print("-" * 105)
print(f"{'TOTAL':10s} | {tot_closed:6d} | {tot_r_ss:9d} | {tot_r_sv:9d} | {tot_rej_all:12d} | {pct_all:7.2f}% | {tot_wt:12.3f} | {tot_ss_c:9d} | {tot_sv_c:9d}")
