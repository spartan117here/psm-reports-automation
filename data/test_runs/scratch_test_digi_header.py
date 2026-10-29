import os
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.reporting.digi import extract_pivot_header_date

sheets = GoogleApiSheetsService(service_account_path='secrets/service_account.json', read_only=True)
digi_id = os.getenv('GOOGLE_SHEET_DIGI_ID')
rows = sheets.read_range(digi_id, "'OCT Pivot'!A1:Z6")
for r_i, r in enumerate(rows, 1):
    for c_i, c in enumerate(r):
        if c:
            print(f"R{r_i}C{c_i}: {repr(c)}")
