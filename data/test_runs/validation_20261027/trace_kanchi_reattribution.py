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

# 1. Load employee mappings
def load_emp_dict(tab_range):
    rows = sheets.read_range(digi_id, tab_range)
    d = {}
    for r in rows[1:]:
        if len(r) >= 5 and r[0].strip():
            d[r[0].strip()] = r[4].strip().upper()
    return d

dict_ne_bf = load_emp_dict("'New Employee'!B1:F")
dict_ne_pt = load_emp_dict("'New Employee'!P1:T")
dict_en_bf = load_emp_dict("'Employee - New'!B1:F")
dict_emp_bf = load_emp_dict("'Employee'!B1:F")

# Load OCT rows
print("Loading OCT rows...")
oct_rows = sheets.read_range(digi_id, "'OCT'!A1:R")
print(f"Loaded {len(oct_rows)-1} rows.")

# For records dated <= 2026-10-26:
# Find all records where Column R != dict_ne_bf or Column R != dict_ne_pt
cutoff_26 = date(2026, 10, 26)

gold_reattributed = []
silver_reattributed = []

for idx, r in enumerate(oct_rows[1:], start=2):
    d = parse_date_safe(r[5] if len(r) > 5 else "")
    if not d or d > cutoff_26:
        continue
    sch = (r[9] if len(r) > 9 else "").strip().upper()
    emp = (r[11] if len(r) > 11 else "").strip()
    emp_name = (r[12] if len(r) > 12 else "").strip()
    col_r = (r[17] if len(r) > 17 else "").strip().upper()
    
    b_ne_bf = dict_ne_bf.get(emp, "MISSING")
    b_ne_pt = dict_ne_pt.get(emp, "MISSING")
    b_en_bf = dict_en_bf.get(emp, "MISSING")
    
    # Check if this employee maps to KANCHEEPURAM in one table but another showroom in another table
    tables = {"ColR": col_r, "NE_BF": b_ne_bf, "NE_PT": b_ne_pt, "EN_BF": b_en_bf}
    has_kanchi = any("KANCH" in b for b in tables.values())
    has_other = any(b not in ["MISSING", ""] and "KANCH" not in b for b in tables.values())
    
    if has_kanchi and has_other:
        item = {
            "row": idx, "date": d, "emp": emp, "emp_name": emp_name,
            "col_r": col_r, "ne_bf": b_ne_bf, "ne_pt": b_ne_pt, "en_bf": b_en_bf
        }
        if sch == "DIGI GOLD":
            gold_reattributed.append(item)
        elif sch == "DIGI SILVER":
            silver_reattributed.append(item)

print(f"\nGold records <= 26-Oct with Kanchi vs other showroom branch conflict: {len(gold_reattributed)}")
print(f"Silver records <= 26-Oct with Kanchi vs other showroom branch conflict: {len(silver_reattributed)}")

# Group by employee
emp_gold = Counter(x["emp"] + " (" + x["emp_name"] + ")" for x in gold_reattributed)
print("\nTop conflicting employees for Gold:")
for emp, count in emp_gold.most_common(15):
    sample = next(x for x in gold_reattributed if x["emp"] in emp)
    print(f"  {emp:35s}: {count:3d} records | ColR={sample['col_r']:12s} | NE_BF={sample['ne_bf']:12s} | NE_PT={sample['ne_pt']:12s}")

emp_silver = Counter(x["emp"] + " (" + x["emp_name"] + ")" for x in silver_reattributed)
print("\nTop conflicting employees for Silver:")
for emp, count in emp_silver.most_common(15):
    sample = next(x for x in silver_reattributed if x["emp"] in emp)
    print(f"  {emp:35s}: {count:3d} records | ColR={sample['col_r']:12s} | NE_BF={sample['ne_bf']:12s} | NE_PT={sample['ne_pt']:12s}")
