import os
import sys
import time
from pathlib import Path

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.google_sheets.client import GoogleApiSheetsService

def run():
    sa_path = str(project_root / "secrets" / "service_account.json")
    for attempt in range(5):
        try:
            sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
            enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
            hdr = sheets.read_range(enr_id, "'SV - Oct'!A1:Z1")
            print("SV Headers:", hdr)
            first_rows = sheets.read_range(enr_id, "'SV - Oct'!A2:Z5")
            print("First row:", first_rows[0] if first_rows else "Empty")
            return
        except Exception as e:
            print(f"Attempt {attempt+1} failed: {e}")
            time.sleep(2)

if __name__ == "__main__":
    run()

