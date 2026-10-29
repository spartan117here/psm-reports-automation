import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from google.oauth2 import service_account
from googleapiclient.discovery import build

def run():
    sa_path = str(project_root / "secrets" / "service_account.json")
    creds = service_account.Credentials.from_service_account_file(
        sa_path, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    service = build("sheets", "v4", credentials=creds)
    enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    
    # Read formulas in Consolidate Report - Oct
    res = service.spreadsheets().values().get(
        spreadsheetId=enr_id,
        range="'Consolidate Report - Oct'!E4:K18",
        valueRenderOption="FORMULA"
    ).execute()
    formulas = res.get("values", [])
    for idx, r in enumerate(formulas, start=4):
        print(f"Row {idx}: {r}")

if __name__ == "__main__":
    run()

