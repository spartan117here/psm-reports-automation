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
sheet_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")

print("=== READING Consolidate Report - Oct!A1:N40 ===")
vals = sheets.read_range(sheet_id, "'Consolidate Report - Oct'!A1:N40")
for idx, r in enumerate(vals, start=1):
    print(f"Row {idx:2d}: {r}")

print("\n=== FORMULAS IN Consolidate Report - Oct!A1:N40 ===")
form_res = sheets.service.spreadsheets().values().get(
    spreadsheetId=sheet_id,
    range="'Consolidate Report - Oct'!A1:N40",
    valueRenderOption="FORMULA"
).execute()
forms = form_res.get("values", [])
for idx, r in enumerate(forms, start=1):
    # print rows with interesting formulas (Row 14 to 35)
    if idx in [4, 5, 14, 15, 16, 17, 21, 22, 30, 31, 32, 33, 34]:
        print(f"Row {idx:2d}: {r}")
