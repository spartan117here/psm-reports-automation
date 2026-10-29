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

print("=== 1. FORMULAS IN 'OCT Pivot' ===")
formulas = sheets.read_formulas(digi_id, "'OCT Pivot'!A1:Z10")
for idx, r in enumerate(formulas, start=1):
    for c_idx, c in enumerate(r):
        if str(c).startswith("="):
            print(f"R{idx}C{c_idx}: {c}")

print("\n=== 2. RAW ROW COLUMNS IN 'OCT' (First 2 rows) ===")
oct_sample = sheets.read_range(digi_id, "'OCT'!A1:Z3")
for r in oct_sample:
    print(r)

print("\n=== 3. CHECKING IF THERE ARE HIDDEN COLUMNS IN 'OCT' ===")
# read formulas in 'OCT'!A1:Z5
oct_forms = sheets.read_formulas(digi_id, "'OCT'!A1:Z3")
for r in oct_forms:
    for c_i, c in enumerate(r):
        if str(c).startswith("="):
            print(f"Col {c_i} has formula: {c}")

print("\n=== 4. CHECKING 'Employee' TAB IN DIGI WORKBOOK ===")
emp_sample = sheets.read_range(digi_id, "'Employee'!A1:H5")
print("Employee headers/sample:", emp_sample)
