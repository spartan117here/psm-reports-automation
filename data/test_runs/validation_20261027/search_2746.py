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
consolidate_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")

print("=== SEARCHING FOR '2746' AND '378' IN CONSOLIDATE REPORT WORKBOOK ===")
meta_c = sheets.service.spreadsheets().get(spreadsheetId=consolidate_id).execute()
for s in meta_c.get("sheets", []):
    title = s["properties"]["title"]
    # read sheet
    try:
        vals = sheets.read_range(consolidate_id, f"'{title}'!A1:Z100")
        for r_idx, r in enumerate(vals, start=1):
            for c_idx, cell in enumerate(r, start=1):
                if "2746" in str(cell) or "378" in str(cell):
                    print(f"Found in Consolidate '{title}'!R{r_idx}C{c_idx}: {cell} (Row: {r[:5]})")
    except Exception as e:
        pass

print("\n=== SEARCHING FOR '2746' AND '378' IN DIGI WORKBOOK ===")
meta_d = sheets.service.spreadsheets().get(spreadsheetId=digi_id).execute()
for s in meta_d.get("sheets", []):
    title = s["properties"]["title"]
    if title in ["OCT", "SEP", "AUG", "JUL", "JUN", "MAY", "APR", "MAR"]:
        continue # skip giant raw tabs
    try:
        vals = sheets.read_range(digi_id, f"'{title}'!A1:AZ100")
        for r_idx, r in enumerate(vals, start=1):
            for c_idx, cell in enumerate(r, start=1):
                if cell == "2746" or cell == "378" or "2,746" in str(cell):
                    print(f"Found in Digi '{title}'!R{r_idx}C{c_idx}: {cell} (Row: {r[:6]})")
    except Exception as e:
        pass
