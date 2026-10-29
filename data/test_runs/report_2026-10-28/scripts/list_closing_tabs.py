import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.google_sheets.client import GoogleApiSheetsService

sheets = GoogleApiSheetsService(service_account_path=str(project_root / "secrets" / "service_account.json"), read_only=True)
close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")

meta = sheets.service.spreadsheets().get(spreadsheetId=close_id).execute()
print("Tabs in Closed Rejoining Spreadsheet:")
for s in meta.get("sheets", []):
    props = s["properties"]
    print(f"  {props['title']} (rows: {props.get('gridProperties', {}).get('rowCount')})")

