import os
import sys
from pathlib import Path
from datetime import date
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.processing.rejoining import parse_date_safe
from app.reporting.digi import DigiSourceReader, evaluate_digi_feed_readiness, DIGI_BRANCH_ORDER

sa_path = str(project_root / "secrets" / "service_account.json")
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")

TARGET_DATE = date(2026, 10, 28)

print("=== CHECKING SOURCES FOR 28 OCTOBER 2026 ===")

# 1. SS - Oct
raw_ss = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
ss_dates = Counter(parse_date_safe(r[5] if len(r)>5 else "") for r in raw_ss)
print(f"\nSS - Oct total rows: {len(raw_ss)}")
print(f"SS date distribution top 5:")
for d, c in sorted(ss_dates.items(), key=lambda x: str(x[0]), reverse=True)[:5]:
    print(f"  {d}: {c} records")

# 2. SV - Oct
raw_sv = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
sv_dates = Counter(parse_date_safe(r[5] if len(r)>5 else "") for r in raw_sv)
print(f"\nSV - Oct total rows: {len(raw_sv)}")
print(f"SV date distribution top 5:")
for d, c in sorted(sv_dates.items(), key=lambda x: str(x[0]), reverse=True)[:5]:
    print(f"  {d}: {c} records")

# 3. Digi Raw Tab 'OCT'
digi_reader = DigiSourceReader(sheets, spreadsheet_id=digi_id)
max_schdate, max_count, rep_count = digi_reader.validate_freshness(TARGET_DATE, "OCT")
print(f"\nDigi 'OCT' freshness: max_date={max_schdate} ({max_count} recs), on 28-Oct={rep_count} recs")

# 4. Digi Pivot Tab 'OCT Pivot'
pivot_extr = digi_reader.extract_pivot_data("OCT Pivot")
print(f"Digi Pivot Extracted:")
print(f"  Pivot Header Date: {pivot_extr.pivot_date_header}")
print(f"  Gold Showroom Subtotal: {pivot_extr.gold_showroom_subtotal}")
print(f"  Silver Showroom Subtotal: {pivot_extr.silver_showroom_subtotal}")
print(f"  Gold Grand Total: {pivot_extr.gold_grand_total}")
print(f"  Silver Grand Total: {pivot_extr.silver_grand_total}")

# 5. Closed Rejoining 'October - 2026'
raw_close = sheets.read_range(close_id, "'October - 2026'!A2:H")
close_dates = Counter(parse_date_safe(r[0] if len(r)>0 else "") for r in raw_close)
print(f"\nClosing 'October - 2026' total rows: {len(raw_close)}")
print(f"Closing date distribution top 5:")
for d, c in sorted(close_dates.items(), key=lambda x: str(x[0]), reverse=True)[:5]:
    print(f"  {d}: {c} records")

