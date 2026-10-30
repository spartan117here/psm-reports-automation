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

def run():
    sa_path = str(project_root / "secrets" / "service_account.json")
    sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")
    
    rows = sheets.read_range(close_id, "'October - 2026'!A2:H")
    print(f"Total closing rows now: {len(rows)}")
    dates = Counter(parse_date_safe(r[0] if len(r)>0 else "") for r in rows)
    print("Dates in October - 2026:")
    for d, c in sorted(dates.items(), key=lambda x: str(x[0]), reverse=True)[:10]:
        print(f"  {d}: {c} records")

if __name__ == "__main__":
    run()
