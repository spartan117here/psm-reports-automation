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

print("=== READING 'OCT'!A1:R ===")
rows = sheets.read_range(digi_id, "'OCT'!A1:R")
header = rows[0]
print("Headers:", header)

# Column indices:
# 5: SCHDATE, 9: SCHEME, 11: REFERRAL_EMP_CODE, 17: Branch (from VLOOKUP)
gold_by_branch_26 = Counter()
gold_by_branch_27 = Counter()
silver_by_branch_26 = Counter()
silver_by_branch_27 = Counter()

kanchi_gold_records_26 = []
kanchi_gold_records_27 = []

for r in rows[1:]:
    d = parse_date_safe(r[5] if len(r) > 5 else "")
    sch = (r[9] if len(r) > 9 else "").strip().upper()
    branch = (r[17] if len(r) > 17 else "").strip().upper()
    emp = (r[11] if len(r) > 11 else "").strip()
    
    if sch == "DIGI GOLD":
        if d and d <= date(2026, 10, 26):
            gold_by_branch_26[branch] += 1
            if "KANCH" in branch:
                kanchi_gold_records_26.append((d, emp, branch))
        if d and d <= date(2026, 10, 27):
            gold_by_branch_27[branch] += 1
            if "KANCH" in branch:
                kanchi_gold_records_27.append((d, emp, branch))
    elif sch == "DIGI SILVER":
        if d and d <= date(2026, 10, 26):
            silver_by_branch_26[branch] += 1
        if d and d <= date(2026, 10, 27):
            silver_by_branch_27[branch] += 1

print("\n--- DIGI GOLD COUNT IN 'OCT' SHEET (By Evaluated Branch Column R) ---")
print(f"Total Gold records <= 26-Oct: {sum(gold_by_branch_26.values())}")
print(f"Total Gold records <= 27-Oct: {sum(gold_by_branch_27.values())}")
for b in sorted(set(list(gold_by_branch_26.keys()) + list(gold_by_branch_27.keys()))):
    c26 = gold_by_branch_26[b]
    c27 = gold_by_branch_27[b]
    print(f"  {b:25s}: <=26-Oct: {c26:5d} | <=27-Oct: {c27:5d} | Diff: {c27 - c26:+d}")

print("\n--- DIGI SILVER COUNT IN 'OCT' SHEET (By Evaluated Branch Column R) ---")
print(f"Total Silver records <= 26-Oct: {sum(silver_by_branch_26.values())}")
print(f"Total Silver records <= 27-Oct: {sum(silver_by_branch_27.values())}")
for b in sorted(set(list(silver_by_branch_26.keys()) + list(silver_by_branch_27.keys()))):
    c26 = silver_by_branch_26[b]
    c27 = silver_by_branch_27[b]
    print(f"  {b:25s}: <=26-Oct: {c26:5d} | <=27-Oct: {c27:5d} | Diff: {c27 - c26:+d}")
