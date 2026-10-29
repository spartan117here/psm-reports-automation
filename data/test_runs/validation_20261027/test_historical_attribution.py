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

# Load employee mappings
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
header = oct_rows[0]
print(f"Loaded {len(oct_rows)-1} rows.")

# For 26-Oct (SCHDATE <= 2026-10-26):
# Test counts for KANCHI under:
# A: Actual evaluated Column R in sheet
# B: dict_ne_bf
# C: dict_ne_pt
# D: dict_en_bf
# E: dict_emp_bf

cutoff_26 = date(2026, 10, 26)
cutoff_27 = date(2026, 10, 27)

def test_counts(cutoff):
    res = {
        "ColR": {"DG": 0, "DS": 0},
        "NE_BF": {"DG": 0, "DS": 0},
        "NE_PT": {"DG": 0, "DS": 0},
        "EN_BF": {"DG": 0, "DS": 0},
        "EMP_BF": {"DG": 0, "DS": 0},
    }
    for r in oct_rows[1:]:
        d = parse_date_safe(r[5] if len(r) > 5 else "")
        if not d or d > cutoff:
            continue
        sch = (r[9] if len(r) > 9 else "").strip().upper()
        if sch not in ["DIGI GOLD", "DIGI SILVER"]:
            continue
        
        emp = (r[11] if len(r) > 11 else "").strip()
        col_r_branch = (r[17] if len(r) > 17 else "").strip().upper()
        
        b_ne_bf = dict_ne_bf.get(emp, "")
        b_ne_pt = dict_ne_pt.get(emp, "")
        b_en_bf = dict_en_bf.get(emp, "")
        b_emp_bf = dict_emp_bf.get(emp, "")
        
        key = "DG" if sch == "DIGI GOLD" else "DS"
        
        if "KANCH" in col_r_branch:
            res["ColR"][key] += 1
        if "KANCH" in b_ne_bf:
            res["NE_BF"][key] += 1
        if "KANCH" in b_ne_pt:
            res["NE_PT"][key] += 1
        if "KANCH" in b_en_bf:
            res["EN_BF"][key] += 1
        if "KANCH" in b_emp_bf:
            res["EMP_BF"][key] += 1
            
    return res

res_26 = test_counts(cutoff_26)
res_27 = test_counts(cutoff_27)

print("\n=== KANCHI DIGI COUNTS AS OF 26-OCTOBER ===")
print("Reference Screenshot 26-Oct: DG = 2,746 | DS = 378")
for k, v in res_26.items():
    print(f"  Mapping {k:10s}: DG = {v['DG']:5d} | DS = {v['DS']:5d} | Diff from Ref: DG {v['DG']-2746:+5d}, DS {v['DS']-378:+4d}")

print("\n=== KANCHI DIGI COUNTS AS OF 27-OCTOBER ===")
print("Pipeline Generated 27-Oct : DG = 2,471 | DS = 296")
for k, v in res_27.items():
    print(f"  Mapping {k:10s}: DG = {v['DG']:5d} | DS = {v['DS']:5d}")
