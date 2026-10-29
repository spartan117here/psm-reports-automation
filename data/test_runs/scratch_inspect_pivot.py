import os
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(".env")
from app.google_sheets.client import GoogleApiSheetsService

sa_path = "secrets/service_account.json"
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")

print("=== INSPECTING OCT PIVOT (A1:L30) ===")
pivot_data = sheets.read_range(digi_id, "'OCT Pivot'!A1:M30")
for idx, r in enumerate(pivot_data, start=1):
    print(f"R{idx}: {r}")
