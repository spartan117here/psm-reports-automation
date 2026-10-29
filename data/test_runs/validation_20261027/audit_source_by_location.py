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
enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")

print("Loading raw SS rows...")
raw_ss = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
print(f"Total SS rows: {len(raw_ss)}")

print("Loading raw SV rows...")
raw_sv = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
print(f"Total SV rows: {len(raw_sv)}")

d26 = date(2026, 10, 26)
d27 = date(2026, 10, 27)

def analyze_sheet(raw_rows, cutoff):
    counts = Counter()
    amounts = defaultdict(float)
    for r in raw_rows:
        dt = parse_date_safe(r[5] if len(r) > 5 else "")
        if not dt or dt > cutoff:
            continue
        loc2 = (r[6] if len(r) > 6 else "").strip().upper()
        amt_str = (r[8] if len(r) > 8 else "0").replace(",", "").strip()
        amt = float(amt_str) if amt_str else 0.0
        counts[loc2] += 1
        amounts[loc2] += amt
    return counts, amounts

ss_cnt_26, ss_amt_26 = analyze_sheet(raw_ss, d26)
ss_cnt_27, ss_amt_27 = analyze_sheet(raw_ss, d27)

sv_cnt_26, sv_amt_26 = analyze_sheet(raw_sv, d26)
sv_cnt_27, sv_amt_27 = analyze_sheet(raw_sv, d27)

print("\n=== SUBHIKSHAM (SS) BY LOCATION 2 ===")
print(f"{'Location 2':25s} | {'26-Oct Cnt':10s} | {'27-Oct Cnt':10s} | {'26-Oct Amt':15s} | {'27-Oct Amt':15s}")
print("-" * 85)
all_ss_locs = sorted(set(list(ss_cnt_26.keys()) + list(ss_cnt_27.keys())))
for loc in all_ss_locs:
    print(f"{loc:25s} | {ss_cnt_26[loc]:10d} | {ss_cnt_27[loc]:10d} | {ss_amt_26[loc]:15,.2f} | {ss_amt_27[loc]:15,.2f}")

print("\n=== VIRUKSHAM (SV) BY LOCATION 2 ===")
print(f"{'Location 2':25s} | {'26-Oct Cnt':10s} | {'27-Oct Cnt':10s} | {'26-Oct Amt':15s} | {'27-Oct Amt':15s}")
print("-" * 85)
all_sv_locs = sorted(set(list(sv_cnt_26.keys()) + list(sv_cnt_27.keys())))
for loc in all_sv_locs:
    print(f"{loc:25s} | {sv_cnt_26[loc]:10d} | {sv_cnt_27[loc]:10d} | {sv_amt_26[loc]:15,.2f} | {sv_amt_27[loc]:15,.2f}")
