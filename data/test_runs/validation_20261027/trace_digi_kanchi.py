import os
import sys
from pathlib import Path
from collections import Counter, defaultdict
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

print("=== 1. CHECKING AUDIT EVIDENCE FROM 26 OCT ===")
evidence_26_path = project_root / "data" / "test_runs" / "report_2026-10-26" / "audit_evidence_20261026.json"
if evidence_26_path.exists():
    import json
    with open(evidence_26_path, "r", encoding="utf-8") as f:
        print(json.dumps(json.load(f), indent=2))
else:
    print("No audit_evidence_20261026.json found.")

print("\n=== 2. READING ALL ROWS FROM 'OCT' RAW SHEET IN DIGI WORKBOOK ===")
rows = sheets.read_range(digi_id, "'OCT'!A1:P")
header = rows[0]
print(f"Header: {header}")
print(f"Total rows in 'OCT': {len(rows)-1}")

# Analyze records by SCHDATE and CBRANCHNAME or other location fields
cbranch_col = header.index("CBRANCHNAME") if "CBRANCHNAME" in header else 13
scheme_col = header.index("SCHEME") if "SCHEME" in header else 9
schdate_col = header.index("SCHDATE") if "SCHDATE" in header else 5
emp_code_col = header.index("REFERRAL_EMP_CODE") if "REFERRAL_EMP_CODE" in header else 11

print(f"Columns: SCHDATE={schdate_col}, SCHEME={scheme_col}, CBRANCHNAME={cbranch_col}, EMP={emp_code_col}")

# Count records by scheme and branch for <= 2026-10-26 and <= 2026-10-27
counts_26 = defaultdict(lambda: Counter())
counts_27 = defaultdict(lambda: Counter())

for r in rows[1:]:
    d = parse_date_safe(r[schdate_col] if len(r) > schdate_col else "")
    sch = (r[scheme_col] if len(r) > scheme_col else "").strip().upper()
    cbranch = (r[cbranch_col] if len(r) > cbranch_col else "").strip().upper()
    
    if d and d <= date(2026, 10, 26):
        counts_26[sch][cbranch] += 1
    if d and d <= date(2026, 10, 27):
        counts_27[sch][cbranch] += 1

print("\n--- DIGI GOLD RAW COUNTS BY CBRANCHNAME ---")
all_branches = sorted(set(list(counts_26["DIGI GOLD"].keys()) + list(counts_27["DIGI GOLD"].keys())))
for b in all_branches:
    c26 = counts_26["DIGI GOLD"][b]
    c27 = counts_27["DIGI GOLD"][b]
    diff = c27 - c26
    print(f"  {b:25s}: <=26-Oct: {c26:5d} | <=27-Oct: {c27:5d} | Diff: {diff:+d}")

print("\n--- DIGI SILVER RAW COUNTS BY CBRANCHNAME ---")
all_branches_s = sorted(set(list(counts_26["DIGI SILVER"].keys()) + list(counts_27["DIGI SILVER"].keys())))
for b in all_branches_s:
    c26 = counts_26["DIGI SILVER"][b]
    c27 = counts_27["DIGI SILVER"][b]
    diff = c27 - c26
    print(f"  {b:25s}: <=26-Oct: {c26:5d} | <=27-Oct: {c27:5d} | Diff: {diff:+d}")
