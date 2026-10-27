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
from app.reporting.digi import (
    DEFAULT_DIGI_SPREADSHEET_ID,
    parse_schdate,
    normalize_branch_name,
)

sa_path = str(project_root / "secrets" / "service_account.json")
sheets_service = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
enrollment_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID", "1Mij4hMfZN-2qM_B6E-r71gC6k0Y0EaK1yG1XJq76zCc")
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID", DEFAULT_DIGI_SPREADSHEET_ID)

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

target_date = date(2026, 10, 25)

# ---------------------------------------------------------
# 1. SS - Oct
# ---------------------------------------------------------
print("Reading SS - Oct...")
ss_rows = sheets_service.read_range(enrollment_id, "SS - Oct!A2:M")
ss_mtd_counts = defaultdict(int)
ss_mtd_amount = defaultdict(float)
ss_total_count = 0
ss_total_amt = 0.0

for r in ss_rows:
    if len(r) > 5 and r[5].strip():
        dt = parse_any_date(r[5].strip())
        if dt and dt <= target_date:
            loc2 = r[6].strip() if len(r) > 6 else ""
            amt = float(r[8].replace(",", "").strip() or 0.0) if len(r) > 8 and r[8].strip() else 0.0
            ss_mtd_counts[loc2] += 1
            ss_mtd_amount[loc2] += amt
            ss_total_count += 1
            ss_total_amt += amt

print(f"SS MTD <= 25 Oct: Total Records={ss_total_count}, Total Amount={ss_total_amt:,.2f}")

# ---------------------------------------------------------
# 2. SV - Oct
# ---------------------------------------------------------
print("Reading SV - Oct...")
sv_rows = sheets_service.read_range(enrollment_id, "SV - Oct!A2:M")
sv_mtd_counts = defaultdict(int)
sv_mtd_amount = defaultdict(float)
sv_total_count = 0
sv_total_amt = 0.0

for r in sv_rows:
    if len(r) > 5 and r[5].strip():
        dt = parse_any_date(r[5].strip())
        if dt and dt <= target_date:
            loc2 = r[6].strip() if len(r) > 6 else ""
            amt = float(r[8].replace(",", "").strip() or 0.0) if len(r) > 8 and r[8].strip() else 0.0
            sv_mtd_counts[loc2] += 1
            sv_mtd_amount[loc2] += amt
            sv_total_count += 1
            sv_total_amt += amt

print(f"SV MTD <= 25 Oct: Total Records={sv_total_count}, Total Amount={sv_total_amt:,.2f}")

# ---------------------------------------------------------
# 3. Digi Gold & Silver from OCT tab
# ---------------------------------------------------------
print("Reading OCT from Digi sheet...")
digi_oct_rows = sheets_service.read_range(digi_id, "OCT!A1:R")
headers = [str(c).strip().upper() for c in digi_oct_rows[0]]
col_map = {h: i for i, h in enumerate(headers)}
schdate_idx = col_map["SCHDATE"]
scheme_idx = col_map["SCHEME"]
branch_idx = col_map["BRANCH"]
costname_idx = col_map["COSTNAME"]

dg_mtd_counts = defaultdict(int)
ds_mtd_counts = defaultdict(int)

for r in digi_oct_rows[1:]:
    if len(r) > schdate_idx and r[schdate_idx].strip():
        dt = parse_any_date(r[schdate_idx].strip())
        if dt and dt <= target_date:
            sch = r[scheme_idx].strip().upper() if len(r) > scheme_idx else ""
            br = r[branch_idx].strip() if len(r) > branch_idx else ""
            cost = r[costname_idx].strip() if len(r) > costname_idx else ""
            norm_br = "ONLINE" if (br == "#N/A" or not br) and cost in ("APP", "HAPP", "ECOM") else normalize_branch_name(br)
            if sch == "DIGI GOLD":
                dg_mtd_counts[norm_br] += 1
            elif sch == "DIGI SILVER":
                ds_mtd_counts[norm_br] += 1

print(f"DG MTD <= 25 Oct: Total={sum(dg_mtd_counts.values())}")
print(f"DS MTD <= 25 Oct: Total={sum(ds_mtd_counts.values())}")

print("\n--- SS LOCATION 2 COUNTS & AMOUNTS ---")
for k, v in sorted(ss_mtd_counts.items()):
    print(f"  {k:25s}: Count={v:4d}, Amt=Rs {ss_mtd_amount[k]:12,.2f}")

print("\n--- SV LOCATION 2 COUNTS & AMOUNTS ---")
for k, v in sorted(sv_mtd_counts.items()):
    print(f"  {k:25s}: Count={v:4d}, Amt=Rs {sv_mtd_amount[k]:12,.2f}")

print("\n--- DG BRANCH COUNTS ---")
for k, v in sorted(dg_mtd_counts.items()):
    print(f"  {k:25s}: Count={v:4d}")

print("\n--- DS BRANCH COUNTS ---")
for k, v in sorted(ds_mtd_counts.items()):
    print(f"  {k:25s}: Count={v:4d}")
