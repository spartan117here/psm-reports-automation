import os
import sys
from pathlib import Path
from collections import Counter
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

print("=== INSPECTING 'New Employee' TAB IN DIGI WORKBOOK ===")
new_emp_rows = sheets.read_range(digi_id, "'New Employee'!A1:G10")
for r in new_emp_rows:
    print(r)

print("\nTotal rows in 'New Employee':")
all_new_emp = sheets.read_range(digi_id, "'New Employee'!A1:F")
print(f"Total rows: {len(all_new_emp)}")

# Count branches in 'New Employee'
branch_col = 4 # column E (0-indexed 4 or 5)
branches = Counter(r[branch_col].strip().upper() for r in all_new_emp[1:] if len(r) > branch_col and r[branch_col].strip())
print("\nBranches in 'New Employee':")
for b, c in branches.most_common():
    print(f"  {b}: {c}")

print("\n=== INSPECTING 'Missing EMP Code' TAB ===")
missing_emp = sheets.read_range(digi_id, "'Missing EMP Code'!A1:F10")
for r in missing_emp:
    print(r)
missing_all = sheets.read_range(digi_id, "'Missing EMP Code'!A1:A")
print(f"Total rows in Missing EMP Code: {len(missing_all)}")

