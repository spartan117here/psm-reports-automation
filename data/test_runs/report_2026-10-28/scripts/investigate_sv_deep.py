import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.processing.rejoining import parse_date_safe

def investigate_sv():
    sa_path = str(project_root / "secrets" / "service_account.json")
    sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    
    # 1. Read entire SV - Oct tab
    all_sv = sheets.read_range(enr_id, "'SV - Oct'!A1:Z")
    print(f"Total SV - Oct rows including header: {len(all_sv)}")
    headers = all_sv[0]
    print(f"Headers: {headers}")
    
    # 2. Check each row
    rows = all_sv[1:]
    total_parsed_amt = 0.0
    amounts_by_col = {}
    
    # Check what columns have numbers
    for col_idx, col_name in enumerate(headers):
        col_sum = 0.0
        num_count = 0
        for r in rows:
            if col_idx < len(r):
                val_str = r[col_idx].replace(",", "").strip()
                try:
                    v = float(val_str)
                    col_sum += v
                    num_count += 1
                except ValueError:
                    pass
        if num_count > 10:
            print(f"Col {col_idx} ({col_name}): numeric_count={num_count}, sum={col_sum:,.2f}")

    # Check 109400 rows specifically
    print("\nLooking for rows with amount 109400 or related to TRICHY SV ECOMM:")
    for idx, r in enumerate(rows, start=2):
        row_str = " | ".join(r)
        if "109400" in row_str or "109,400" in row_str or "TRICHY SV ECOMM" in row_str:
            print(f"Row {idx}: {r}")

    # Check Consolidated Report Oct tab for SV formulas and values
    print("\nReading Consolidate Report - Oct:")
    consolidate_rows = sheets.read_range(enr_id, "'Consolidate Report - Oct'!A1:Z40")
    for idx, r in enumerate(consolidate_rows, start=1):
        if idx in range(1, 40):
            print(f"Consolidate Row {idx}: {r[:10]}")

if __name__ == "__main__":
    investigate_sv()

