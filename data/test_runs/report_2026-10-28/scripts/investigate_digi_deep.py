import os
import sys
from pathlib import Path
from collections import Counter, defaultdict

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
    digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
    enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    
    # 1. Read OCT Pivot in Digi Sheet
    pivot_rows = sheets.read_range(digi_id, "'OCT Pivot'!A1:M25")
    print("=== OCT PIVOT TABLE IN DIGI SHEET ===")
    for idx, r in enumerate(pivot_rows, start=1):
        print(f"Row {idx:2d}: {r}")

    # 2. Read Consolidate Report Table 2 in New Enrollment Sheet
    print("\n=== CONSOLIDATE REPORT - OCT TABLE 2 ===")
    enr_tbl2 = sheets.read_range(enr_id, "'Consolidate Report - Oct'!E20:K35")
    for idx, r in enumerate(enr_tbl2, start=20):
        print(f"Row {idx:2d}: {r}")
        
    # Check formulas in Table 2
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        sa_path, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    service = build("sheets", "v4", credentials=creds)
    res = service.spreadsheets().values().get(
        spreadsheetId=enr_id,
        range="'Consolidate Report - Oct'!E20:K34",
        valueRenderOption="FORMULA"
    ).execute()
    formulas = res.get("values", [])
    print("\n=== CONSOLIDATE REPORT - OCT TABLE 2 FORMULAS ===")
    for idx, r in enumerate(formulas, start=20):
        print(f"Row {idx:2d}: {r}")

if __name__ == "__main__":
    run()
