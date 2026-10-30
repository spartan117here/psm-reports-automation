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
from app.reporting.digi import DigiSourceReader, evaluate_digi_feed_readiness

def check_digi():
    sa_path = str(project_root / "secrets" / "service_account.json")
    sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
    target_date = date(2026, 10, 29)
    
    reader = DigiSourceReader(sheets, spreadsheet_id=digi_id)
    max_schdate, max_count, rep_count = reader.validate_freshness(target_date, "OCT")
    print(f"Digi OCT freshness: max_schdate={max_schdate} ({max_count} recs), on 29-Oct={rep_count} recs")
    
    pivot = reader.extract_pivot_data("OCT Pivot")
    print(f"Pivot header: {pivot.pivot_date_header}")
    print(f"Gold Showroom subtotal: {pivot.gold_showroom_subtotal}")
    print(f"Silver Showroom subtotal: {pivot.silver_showroom_subtotal}")

if __name__ == "__main__":
    check_digi()

