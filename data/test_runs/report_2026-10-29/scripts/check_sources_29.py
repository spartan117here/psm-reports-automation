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

def check_sources_29():
    sa_path = str(project_root / "secrets" / "service_account.json")
    sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
    close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")
    
    target_date = date(2026, 10, 29)
    print(f"=== CHECKING SOURCES FOR {target_date} ===")
    
    # 1. SS - Oct
    raw_ss = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
    ss_dates = Counter(parse_date_safe(r[5] if len(r)>5 else "") for r in raw_ss)
    print(f"\nSS - Oct total rows: {len(raw_ss)}")
    print("Latest dates in SS:")
    for d, c in sorted(ss_dates.items(), key=lambda x: str(x[0]), reverse=True)[:5]:
        print(f"  {d}: {c} records")
        
    # 2. SV - Oct
    raw_sv = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
    sv_dates = Counter(parse_date_safe(r[5] if len(r)>5 else "") for r in raw_sv)
    print(f"\nSV - Oct total rows: {len(raw_sv)}")
    print("Latest dates in SV:")
    for d, c in sorted(sv_dates.items(), key=lambda x: str(x[0]), reverse=True)[:5]:
        print(f"  {d}: {c} records")
        
    # 3. Digi raw OCT
    raw_digi = sheets.read_range(digi_id, "'OCT'!A2:Z")
    digi_dates = Counter(parse_date_safe(r[5] if len(r)>5 else "") for r in raw_digi)
    print(f"\nDigi 'OCT' total rows: {len(raw_digi)}")
    print("Latest dates in OCT:")
    for d, c in sorted(digi_dates.items(), key=lambda x: str(x[0]), reverse=True)[:5]:
        print(f"  {d}: {c} records")
        
    # 4. Digi Pivot
    pivot_rows = sheets.read_range(digi_id, "'OCT Pivot'!A1:M25")
    print("\nOCT Pivot header row 3:")
    if len(pivot_rows) > 2:
        print(f"  {pivot_rows[2]}")
    if len(pivot_rows) > 3:
        print(f"  Row 4: {pivot_rows[3]}")
    if len(pivot_rows) > 14:
        print(f"  Row 15 (Branch Total): {pivot_rows[14]}")
    if len(pivot_rows) > 16:
        print(f"  Row 17 (Ecommerce): {pivot_rows[16]}")
    if len(pivot_rows) > 21:
        print(f"  Row 22 (Grand Total): {pivot_rows[21]}")
        
    # 5. Closed Rejoining 'October - 2026'
    raw_close = sheets.read_range(close_id, "'October - 2026'!A2:H")
    close_dates = Counter(parse_date_safe(r[0] if len(r)>0 else "") for r in raw_close)
    print(f"\nClosing 'October - 2026' total rows: {len(raw_close)}")
    print("Latest dates in Closing:")
    for d, c in sorted(close_dates.items(), key=lambda x: str(x[0]), reverse=True)[:5]:
        print(f"  {d}: {c} records")

if __name__ == "__main__":
    check_sources_29()

