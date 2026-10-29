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
from app.processing.rejoining import parse_date_safe

def run():
    sa_path = str(project_root / "secrets" / "service_account.json")
    sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    
    rows = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
    tvm_rows = [r for r in rows if "TRIVANDRUM" in (r[6] if len(r)>6 else "").upper()]
    print(f"Total TVM SV rows: {len(tvm_rows)}")
    tvm_dates = {}
    tvm_tot = 0.0
    for r in tvm_rows:
        dt = parse_date_safe(r[5] if len(r)>5 else "")
        amt = float((r[8] if len(r)>8 else "0").replace(",", "").strip())
        tvm_dates[dt] = tvm_dates.get(dt, 0) + amt
        tvm_tot += amt
        
    print("TVM SV total:", tvm_tot)
    print("TVM SV by date:")
    for dt in sorted(tvm_dates.keys()):
        print(f"  {dt}: ₹{tvm_dates[dt]:,.2f}")

    # Check TRICHY SV ECOMM row date
    trichy_ecomm_rows = [r for r in rows if "TRICHY SV ECOMM" in (r[6] if len(r)>6 else "").upper()]
    print(f"\nTRICHY SV ECOMM rows: {len(trichy_ecomm_rows)}")
    for r in trichy_ecomm_rows:
        print(f"  Date: {r[5]}, Amount: {r[8]}, ClientID: {r[1]}, Name: {r[4]}")

if __name__ == "__main__":
    run()
