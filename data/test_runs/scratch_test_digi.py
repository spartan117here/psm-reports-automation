import os
import sys
from pathlib import Path
from datetime import date

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.reporting.digi import DigiSourceReader

sa_path = "secrets/service_account.json"
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")

reader = DigiSourceReader(sheets, spreadsheet_id=digi_id)
rep_date = date(2026, 10, 27)

max_d, max_cnt, rep_cnt = reader.validate_freshness(rep_date, "OCT")
print(f"Digi Freshness: max_date={max_d}, max_count={max_cnt}, rep_cnt={rep_cnt}")

extraction = reader.extract_pivot_data("OCT Pivot")
print("\nExtracted Gold values:")
for k, v in extraction.gold_values.items():
    print(f"  {k}: {v}")

print("\nExtracted Silver values:")
for k, v in extraction.silver_values.items():
    print(f"  {k}: {v}")

print(f"\nGold Showroom Subtotal: {extraction.gold_showroom_subtotal}")
print(f"Gold Grand Total      : {extraction.gold_grand_total}")
print(f"Silver Showroom Subtotal: {extraction.silver_showroom_subtotal}")
print(f"Silver Grand Total    : {extraction.silver_grand_total}")
print(f"Pivot Date Header     : {extraction.pivot_date_header}")
