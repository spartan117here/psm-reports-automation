import os
import sys
from pathlib import Path
from collections import Counter

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
    
    rows = sheets.read_range(digi_id, "'OCT'!A2:Z")
    
    ecomm_by_date = Counter()
    total_dg_by_date = Counter()
    
    for r in rows:
        sch = r[9].strip().upper() if len(r)>9 else ""
        br = r[17].strip() if len(r)>17 else ""
        dt = parse_date_safe(r[5] if len(r)>5 else "")
        if dt and ("GOLD" in sch or "DG" in sch):
            total_dg_by_date[dt] += 1
            if br in ["#N/A", "CORPORATE OFFICE"]:
                ecomm_by_date[dt] += 1
                
    cum_ecomm = 0
    cum_total = 0
    print("Cumulative DigiGold Progression in raw OCT:")
    for d in sorted(total_dg_by_date.keys()):
        cum_ecomm += ecomm_by_date[d]
        cum_total += total_dg_by_date[d]
        if d >= parse_date_safe("25-10-2026"):
            print(f"  As of {d}: E COMM DG = {cum_ecomm} (new: {ecomm_by_date[d]}), Total DG = {cum_total} (new: {total_dg_by_date[d]})")

if __name__ == "__main__":
    run()

