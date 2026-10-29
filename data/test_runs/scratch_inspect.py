import os
import sys
import json
from dotenv import load_dotenv

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

load_dotenv(".env")
from app.google_sheets.client import GoogleApiSheetsService

sa_path = "secrets/service_account.json"
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)

enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")

print("=== 1. CONSOLIDATE REPORT - OCT ===")
enr_slice = sheets.read_range(enr_id, "'Consolidate Report - Oct'!E1:L35")
for idx, r in enumerate(enr_slice, start=1):
    print(f"R{idx}: {r}")

print("\n=== 2. OCTOBER - 2026 (CLOSING) ===")
close_slice = sheets.read_range(close_id, "'October - 2026'!L1:Q30")
for idx, r in enumerate(close_slice, start=1):
    print(f"R{idx}: {r}")
