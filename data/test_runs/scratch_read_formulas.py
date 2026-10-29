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
enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")

print("=== READING FORMULAS IN Consolidate Report - Oct E3:L33 ===")
formulas = sheets.read_formulas(enr_id, "'Consolidate Report - Oct'!E3:L33")
for idx, row in enumerate(formulas, start=3):
    print(f"Row {idx}: {row}")
