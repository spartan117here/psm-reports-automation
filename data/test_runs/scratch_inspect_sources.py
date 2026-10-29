import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(".env")
from app.google_sheets.client import GoogleApiSheetsService

sa_path = "secrets/service_account.json"
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)

enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")

print("=== CHECKING SS - Oct ===")
ss_header = sheets.read_range(enr_id, "'SS - Oct'!A1:N2")
print("SS Headers:", ss_header)
ss_sample = sheets.read_range(enr_id, "'SS - Oct'!A2:N5")
print("SS Sample:", ss_sample)

print("\n=== CHECKING SV - Oct ===")
sv_header = sheets.read_range(enr_id, "'SV - Oct'!A1:N2")
print("SV Headers:", sv_header)

print("\n=== CHECKING October - 2026 Cols A..K ===")
close_ak = sheets.read_range(close_id, "'October - 2026'!A1:K5")
print("Closing A..K Headers/Sample:", close_ak)

print("\n=== CHECKING Digi OCT ===")
digi_sample = sheets.read_range(digi_id, "'OCT'!A1:P5")
print("Digi OCT Headers/Sample:", digi_sample)

print("\n=== CHECKING Digi OCT Pivot ===")
digi_pivot = sheets.read_range(digi_id, "'OCT Pivot'!A1:H15")
print("Digi OCT Pivot Sample:", digi_pivot)
