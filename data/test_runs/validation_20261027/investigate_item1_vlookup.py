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
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")

print("=== INSPECTING 'New Employee'!P1:T15 ===")
p_t = sheets.read_range(digi_id, "'New Employee'!P1:T15")
for idx, r in enumerate(p_t, start=1):
    print(f"Row {idx}: {r}")

print("\n=== CHECKING FORMULAS IN OCT!R ACROSS DIFFERENT ROW RANGES ===")
# Sample rows across the sheet
sample_rows = [2, 100, 500, 1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000, 15000, 20000, 25000, 26000]
for r in sample_rows:
    res = sheets.service.spreadsheets().values().get(
        spreadsheetId=digi_id,
        range=f"OCT!R{r}",
        valueRenderOption="FORMULA"
    ).execute()
    vals = res.get("values", [[]])
    print(f"OCT!R{r}: {vals[0][0] if vals and vals[0] else 'EMPTY'}")
