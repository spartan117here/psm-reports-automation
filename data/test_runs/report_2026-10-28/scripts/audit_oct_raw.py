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
    
    # Read headers of OCT
    hdr = sheets.read_range(digi_id, "'OCT'!A1:Z1")
    print("OCT Headers:", hdr)
    
    # Read all rows of OCT
    rows = sheets.read_range(digi_id, "'OCT'!A2:Z")
    print(f"Total OCT rows: {len(rows)}")
    
    # Distribution of SCHDATE
    dates = Counter()
    gold_by_branch = Counter()
    silver_by_branch = Counter()
    gold_by_date = Counter()
    silver_by_date = Counter()
    
    for r in rows:
        # Check scheme
        sch = r[9].strip().upper() if len(r)>9 else ""
        br = r[17].strip() if len(r)>17 else "" # Branch (col 17)
        cbr = r[13].strip() if len(r)>13 else "" # CBRANCHNAME (col 13)
        dt_str = r[5] if len(r)>5 else ""
        dt = parse_date_safe(dt_str)
        if dt:
            dates[dt] += 1
            if "GOLD" in sch or "DG" in sch:
                gold_by_date[dt] += 1
                if dt <= parse_date_safe("28-10-2026"):
                    gold_by_branch[br] += 1
            elif "SILVER" in sch or "DS" in sch:
                silver_by_date[dt] += 1
                if dt <= parse_date_safe("28-10-2026"):
                    silver_by_branch[br] += 1
                    
    print("\nDate distribution in OCT up to 28-Oct:")
    for d in sorted(dates.keys()):
        if d >= parse_date_safe("24-10-2026"):
            print(f"  {d}: total={dates[d]}, gold={gold_by_date[d]}, silver={silver_by_date[d]}")
            
    print(f"\nGold count by Branch up to 28-Oct (Total: {sum(gold_by_branch.values())}):")
    for b, c in sorted(gold_by_branch.items(), key=lambda x: str(x[0])):
        print(f"  '{b}': {c}")

if __name__ == "__main__":
    run()
