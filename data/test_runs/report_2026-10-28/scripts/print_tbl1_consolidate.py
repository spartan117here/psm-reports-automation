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

def run():
    sa_path = str(project_root / "secrets" / "service_account.json")
    sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    
    rows = sheets.read_range(enr_id, "'Consolidate Report - Oct'!E4:K17")
    for r in rows:
        print(r)

if __name__ == "__main__":
    run()

