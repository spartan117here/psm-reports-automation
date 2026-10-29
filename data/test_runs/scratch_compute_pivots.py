import os
import sys
from pathlib import Path
from collections import defaultdict
from datetime import date

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.processing.rejoining import parse_date_safe

sa_path = "secrets/service_account.json"
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")

ss_rows = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
sv_rows = sheets.read_range(enr_id, "'SV - Oct'!A2:N")

for target_cutoff in [date(2026, 10, 27), date(2026, 10, 28)]:
    print(f"\n=======================================================")
    print(f"PIVOT BY LOCATION 2 WITH CUTOFF <= {target_cutoff}")
    print(f"=======================================================")
    
    ss_by_loc2 = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for r in ss_rows:
        d = parse_date_safe(r[5] if len(r) > 5 else "")
        if d and d <= target_cutoff:
            loc2 = (r[6] if len(r) > 6 else "").strip()
            amt_str = (r[8] if len(r) > 8 else "0").replace(",", "").strip()
            amt = float(amt_str) if amt_str else 0.0
            ss_by_loc2[loc2]["count"] += 1
            ss_by_loc2[loc2]["amount"] += amt

    print("\n--- SUBHIKSHAM (SS) ---")
    tot_ss_cnt = 0
    tot_ss_amt = 0.0
    for loc2 in sorted(ss_by_loc2.keys()):
        c = ss_by_loc2[loc2]["count"]
        a = ss_by_loc2[loc2]["amount"]
        tot_ss_cnt += c
        tot_ss_amt += a
        print(f"  {loc2:25s}: Count={c:5d}, Amount=INR {a:12,.2f}")
    print(f"  {'TOTAL':25s}: Count={tot_ss_cnt:5d}, Amount=INR {tot_ss_amt:12,.2f}")

    sv_by_loc2 = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for r in sv_rows:
        d = parse_date_safe(r[5] if len(r) > 5 else "")
        if d and d <= target_cutoff:
            loc2 = (r[6] if len(r) > 6 else "").strip()
            amt_str = (r[8] if len(r) > 8 else "0").replace(",", "").strip()
            amt = float(amt_str) if amt_str else 0.0
            sv_by_loc2[loc2]["count"] += 1
            sv_by_loc2[loc2]["amount"] += amt

    print("\n--- VIRUKSHAM (SV) ---")
    tot_sv_cnt = 0
    tot_sv_amt = 0.0
    for loc2 in sorted(sv_by_loc2.keys()):
        c = sv_by_loc2[loc2]["count"]
        a = sv_by_loc2[loc2]["amount"]
        tot_sv_cnt += c
        tot_sv_amt += a
        print(f"  {loc2:25s}: Count={c:5d}, Amount=INR {a:12,.2f}")
    print(f"  {'TOTAL':25s}: Count={tot_sv_cnt:5d}, Amount=INR {tot_sv_amt:12,.2f}")
