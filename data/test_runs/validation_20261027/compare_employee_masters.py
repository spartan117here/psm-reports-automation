import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
project_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.google_sheets.client import GoogleApiSheetsService

sa_path = str(project_root / "secrets" / "service_account.json")
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")

print("=== COMPARING EMPLOYEE REFERENCE TABLES ===")

# 1. 'New Employee'!B:F
ne_bf = sheets.read_range(digi_id, "'New Employee'!B1:F")
print(f"'New Employee'!B:F header: {ne_bf[0]}, count: {len(ne_bf)}")
dict_ne_bf = {}
for r in ne_bf[1:]:
    if len(r) >= 5 and r[0].strip():
        dict_ne_bf[r[0].strip()] = r[4].strip()

# 2. 'New Employee'!P:T
ne_pt = sheets.read_range(digi_id, "'New Employee'!P1:T")
print(f"'New Employee'!P:T header: {ne_pt[0]}, count: {len(ne_pt)}")
dict_ne_pt = {}
for r in ne_pt[1:]:
    if len(r) >= 5 and r[0].strip():
        dict_ne_pt[r[0].strip()] = r[4].strip()

# 3. 'Employee - New'!B:F
en_bf = sheets.read_range(digi_id, "'Employee - New'!B1:F")
print(f"'Employee - New'!B:F header: {en_bf[0]}, count: {len(en_bf)}")
dict_en_bf = {}
for r in en_bf[1:]:
    if len(r) >= 5 and r[0].strip():
        dict_en_bf[r[0].strip()] = r[4].strip()

# 4. 'Employee'!B:F
emp_bf = sheets.read_range(digi_id, "'Employee'!B1:F")
print(f"'Employee'!B:F header: {emp_bf[0]}, count: {len(emp_bf)}")
dict_emp_bf = {}
for r in emp_bf[1:]:
    if len(r) >= 5 and r[0].strip():
        dict_emp_bf[r[0].strip()] = r[4].strip()

# Now find differences!
all_emp_codes = set(list(dict_ne_bf.keys()) + list(dict_ne_pt.keys()) + list(dict_en_bf.keys()) + list(dict_emp_bf.keys()))
print(f"Total unique employee codes: {len(all_emp_codes)}")

diff_branch_count = 0
kanchi_diffs = []
for code in all_emp_codes:
    b1 = dict_ne_bf.get(code, "MISSING")
    b2 = dict_ne_pt.get(code, "MISSING")
    b3 = dict_en_bf.get(code, "MISSING")
    b4 = dict_emp_bf.get(code, "MISSING")
    
    distinct_branches = set([b1, b2, b3, b4]) - {"MISSING"}
    if len(distinct_branches) > 1:
        diff_branch_count += 1
        if any("KANCH" in b.upper() for b in distinct_branches):
            kanchi_diffs.append((code, b1, b2, b3, b4))

print(f"Number of employees with DIFFERENT branch assignments across tables: {diff_branch_count}")
print(f"Number of employees with KANCHI discrepancies: {len(kanchi_diffs)}")
print("\nSample KANCHI discrepancies (Code, 'New Employee'!B:F, 'New Employee'!P:T, 'Employee - New'!B:F, 'Employee'!B:F):")
for d in kanchi_diffs[:20]:
    print(f"  Code {d[0]:6s}: NE_BF={d[1]:20s} | NE_PT={d[2]:20s} | EN_BF={d[3]:20s} | EMP_BF={d[4]:20s}")
