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

print("=== INSPECTING DAILY BREAKDOWN IN OCT PIVOT ===")
# read header row of daily breakdown around row 54
daily_header = sheets.read_range(digi_id, "'OCT Pivot'!B54:AH56")
for idx, r in enumerate(daily_header, start=54):
    print(f"Row {idx}: {r}")

# read rows 55 to 75 (Gold daily by branch)
print("\n--- GOLD DAILY BY BRANCH ---")
gold_daily = sheets.read_range(digi_id, "'OCT Pivot'!B55:AH75")
for idx, r in enumerate(gold_daily, start=55):
    if r:
        print(f"Row {idx} ({r[0] if len(r)>0 else ''}): {r[1:5]} ... {r[-5:] if len(r)>5 else []}")

# read rows 78 to 95 (Silver daily by branch)
print("\n--- SILVER DAILY BY BRANCH ---")
silver_daily = sheets.read_range(digi_id, "'OCT Pivot'!B78:AH95")
for idx, r in enumerate(silver_daily, start=78):
    if r:
        print(f"Row {idx} ({r[0] if len(r)>0 else ''}): {r[1:5]} ... {r[-5:] if len(r)>5 else []}")
