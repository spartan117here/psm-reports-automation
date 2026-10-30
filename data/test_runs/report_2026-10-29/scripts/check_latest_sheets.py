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

def check_latest():
    sa_path = str(project_root / "secrets" / "service_account.json")
    sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
    close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")
    
    # 1. SS - Oct
    raw_ss = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
    print(f"SS - Oct total rows: {len(raw_ss)}")
    print("SS last 5 rows:")
    for r in raw_ss[-5:]:
        print(f"  Row: Date={r[5] if len(r)>5 else ''}, Branch={r[6] if len(r)>6 else ''}, Amt={r[8] if len(r)>8 else ''}")
        
    # 2. SV - Oct
    raw_sv = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
    print(f"\nSV - Oct total rows: {len(raw_sv)}")
    print("SV last 5 rows:")
    for r in raw_sv[-5:]:
        print(f"  Row: Date={r[5] if len(r)>5 else ''}, Branch={r[6] if len(r)>6 else ''}, Amt={r[8] if len(r)>8 else ''}")
        
    # 3. Digi OCT
    raw_digi = sheets.read_range(digi_id, "'OCT'!A2:R")
    print(f"\nDigi OCT total rows: {len(raw_digi)}")
    print("Digi last 5 rows:")
    for r in raw_digi[-5:]:
        print(f"  Row: Date={r[5] if len(r)>5 else ''}, Scheme={r[9] if len(r)>9 else ''}, Branch={r[17] if len(r)>17 else ''}")

    # 4. Closing October - 2026
    raw_close = sheets.read_range(close_id, "'October - 2026'!A2:H")
    print(f"\nClosing October - 2026 total rows: {len(raw_close)}")
    print("Closing last 5 rows:")
    for r in raw_close[-5:]:
        print(f"  Row: Date={r[0] if len(r)>0 else ''}, Branch={r[7] if len(r)>7 else ''}, Scheme={r[4] if len(r)>4 else ''}")

if __name__ == "__main__":
    check_latest()

