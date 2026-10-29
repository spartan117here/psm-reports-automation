import os
import sys
from pathlib import Path
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.processing.rejoining import parse_date_safe

sheets = GoogleApiSheetsService(service_account_path=str(project_root / "secrets" / "service_account.json"), read_only=True)
close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")
raw_close = sheets.read_range(close_id, "'October - 2026'!A2:H")

close_dates = Counter()
invalid_dates = 0
for idx, r in enumerate(raw_close, start=2):
    raw_d = r[0] if len(r)>0 else ""
    dt = parse_date_safe(raw_d)
    if dt:
        close_dates[dt] += 1
    else:
        invalid_dates += 1

print("Total rows:", len(raw_close))
print("Invalid dates:", invalid_dates)
print("Closing dates (sorted descending):")
for d, c in sorted(close_dates.items(), key=lambda x: x[0], reverse=True)[:10]:
    print(f"  {d}: {c} records")

