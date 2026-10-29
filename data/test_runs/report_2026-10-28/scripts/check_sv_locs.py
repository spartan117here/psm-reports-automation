import os
import sys
from pathlib import Path
from collections import defaultdict

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.processing.rejoining import parse_date_safe

def run():
    sa_path = str(project_root / "secrets" / "service_account.json")
    sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    rows = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
    
    loc2_amts = defaultdict(float)
    loc2_cnts = defaultdict(int)
    total_amt = 0.0
    
    for r in rows:
        dt = parse_date_safe(r[5] if len(r)>5 else "")
        if dt and dt <= parse_date_safe("28-10-2026"):
            amt_str = (r[8] if len(r)>8 else "0").replace(",", "").strip()
            amt = float(amt_str) if amt_str else 0.0
            loc2 = (r[6] if len(r)>6 else "").strip().upper()
            loc2_amts[loc2] += amt
            loc2_cnts[loc2] += 1
            total_amt += amt
            
    print(f"Total SV Records: {sum(loc2_cnts.values())}, Total SV Amt: {total_amt}")
    for loc, amt in sorted(loc2_amts.items(), key=lambda x: x[0]):
        print(f"  {loc}: cnt={loc2_cnts[loc]}, amt={amt:,.2f}")

if __name__ == "__main__":
    run()

