import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.google_sheets.client import GoogleApiSheetsService

def run():
    sa_path = str(project_root / "secrets" / "service_account.json")
    sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    
    # Read rows 38 to 48 of columns A to C in Consolidate Report - Oct
    vals = sheets.read_range(enr_id, "'Consolidate Report - Oct'!A38:C48")
    for idx, r in enumerate(vals, start=38):
        print(f"Row {idx}: {r}")

if __name__ == "__main__":
    run()

