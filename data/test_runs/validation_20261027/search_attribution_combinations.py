import os
import sys
from pathlib import Path
from collections import Counter
from datetime import date

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
project_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.processing.rejoining import parse_date_safe

sa_path = str(project_root / "secrets" / "service_account.json")
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")

print("Loading OCT rows...")
oct_rows = sheets.read_range(digi_id, "'OCT'!A1:R")
header = oct_rows[0]
print("Columns:", {i: h for i, h in enumerate(header)})

cutoff_26 = date(2026, 10, 26)

# Check all possible columns that could contain branch info:
# 0: COSTNAME, 11: REFERRAL_EMP_CODE, 12: REFERRAL_EMPNAME, 13: CBRANCHNAME, 16: Employee Name, 17: Branch
costnames_26_dg = Counter()
cbranch_26_dg = Counter()
col_r_26_dg = Counter()

costnames_26_ds = Counter()
cbranch_26_ds = Counter()
col_r_26_ds = Counter()

for r in oct_rows[1:]:
    d = parse_date_safe(r[5] if len(r) > 5 else "")
    if not d or d > cutoff_26:
        continue
    sch = (r[9] if len(r) > 9 else "").strip().upper()
    cost = (r[0] if len(r) > 0 else "").strip().upper()
    cbranch = (r[13] if len(r) > 13 else "").strip().upper()
    branch_r = (r[17] if len(r) > 17 else "").strip().upper()
    
    if sch == "DIGI GOLD":
        costnames_26_dg[cost] += 1
        cbranch_26_dg[cbranch] += 1
        col_r_26_dg[branch_r] += 1
    elif sch == "DIGI SILVER":
        costnames_26_ds[cost] += 1
        cbranch_26_ds[cbranch] += 1
        col_r_26_ds[branch_r] += 1

print("\n--- By COSTNAME (Col 0) for DG <= 26-Oct ---")
for k, v in costnames_26_dg.most_common():
    if "KANCH" in k or v in [2746, 2424]:
        print(f"  {k}: {v}")

print("\n--- By CBRANCHNAME (Col 13) for DG <= 26-Oct ---")
for k, v in cbranch_26_dg.most_common():
    if "KANCH" in k or v in [2746, 2424]:
        print(f"  {k}: {v}")

print("\n--- By Branch (Col 17) for DG <= 26-Oct ---")
for k, v in col_r_26_dg.most_common():
    if "KANCH" in k or v in [2746, 2424]:
        print(f"  {k}: {v}")

print("\n--- By COSTNAME (Col 0) for DS <= 26-Oct ---")
for k, v in costnames_26_ds.most_common():
    if "KANCH" in k or v in [378, 289]:
        print(f"  {k}: {v}")

print("\n--- By CBRANCHNAME (Col 13) for DS <= 26-Oct ---")
for k, v in cbranch_26_ds.most_common():
    if "KANCH" in k or v in [378, 289]:
        print(f"  {k}: {v}")

print("\n--- By Branch (Col 17) for DS <= 26-Oct ---")
for k, v in col_r_26_ds.most_common():
    if "KANCH" in k or v in [378, 289]:
        print(f"  {k}: {v}")
