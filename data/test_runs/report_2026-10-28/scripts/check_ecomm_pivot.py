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

sheets = GoogleApiSheetsService(service_account_path=str(project_root / "secrets" / "service_account.json"), read_only=True)
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
rows = sheets.read_range(digi_id, "'OCT Pivot'!J13:T18")
for idx, r in enumerate(rows, start=13):
    print(f"Row {idx}: {r}")

print("\n--- Also checking formula/values in OCT Pivot Row 73 (#N/A) and 59 (Corporate Office) on Day 28 ---")
# Row 55 is headers (dates)
headers = sheets.read_range(digi_id, "'OCT Pivot'!B55:AH55")[0]
idx_28 = headers.index("2026-10-28")
print(f"Date index for 2026-10-28: {idx_28}")

row_na = sheets.read_range(digi_id, "'OCT Pivot'!B73:AH73")[0]
na_28_cum = sum(int(x) for x in row_na[1:idx_28+1] if x.strip().isdigit())
print(f"#N/A cumulative through 28-Oct: {na_28_cum} (Day 28 val: {row_na[idx_28]})")

row_corp = sheets.read_range(digi_id, "'OCT Pivot'!B59:AH59")[0]
corp_28_cum = sum(int(x) for x in row_corp[1:idx_28+1] if x.strip().isdigit())
print(f"Corporate Office cumulative through 28-Oct: {corp_28_cum} (Day 28 val: {row_corp[idx_28]})")

print(f"Sum (#N/A + Corporate Office) through 28-Oct: {na_28_cum + corp_28_cum}")

