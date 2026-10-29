import os
import sys
from pathlib import Path
from datetime import date

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
project_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.processing.rejoining import RejoiningMatchingEngine

sa_path = str(project_root / "secrets" / "service_account.json")
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")
enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")

print("Loading raw sheets...")
raw_close_rows = sheets.read_range(close_id, "'October - 2026'!A2:H")
raw_ss_rows = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
raw_sv_rows = sheets.read_range(enr_id, "'SV - Oct'!A2:N")

# 26 October Reference values from Reference_2.png:
REF_CLOSE_26 = {
    "TVL": {"closed": 582, "rejoin_ss": 132, "rejoin_sv": 17, "total": 149, "pct": 25.60, "weight": 4537.66, "ss_close": 490, "sv_close": 92},
    "CPT": {"closed": 1155, "rejoin_ss": 221, "rejoin_sv": 22, "total": 243, "pct": 21.04, "weight": 11770.32, "ss_close": 960, "sv_close": 195},
    "TVM": {"closed": 547, "rejoin_ss": 121, "rejoin_sv": 10, "total": 131, "pct": 23.95, "weight": 3281.66, "ss_close": 484, "sv_close": 63},
    "TPJ": {"closed": 887, "rejoin_ss": 172, "rejoin_sv": 9, "total": 181, "pct": 20.41, "weight": 4982.23, "ss_close": 767, "sv_close": 120},
    "CBE": {"closed": 726, "rejoin_ss": 143, "rejoin_sv": 9, "total": 152, "pct": 20.94, "weight": 3625.71, "ss_close": 643, "sv_close": 83},
    "SLM": {"closed": 356, "rejoin_ss": 53, "rejoin_sv": 7, "total": 60, "pct": 16.85, "weight": 2525.24, "ss_close": 279, "sv_close": 77},
    "PNM": {"closed": 334, "rejoin_ss": 44, "rejoin_sv": 7, "total": 51, "pct": 15.27, "weight": 2415.69, "ss_close": 283, "sv_close": 51},
    "PADI": {"closed": 192, "rejoin_ss": 24, "rejoin_sv": 6, "total": 30, "pct": 15.63, "weight": 1064.77, "ss_close": 182, "sv_close": 10},
    "KPM": {"closed": 1, "rejoin_ss": 0, "rejoin_sv": 0, "total": 0, "pct": 0.00, "weight": 0.75, "ss_close": 1, "sv_close": 0},
    "TOTAL": {"closed": 4780, "rejoin_ss": 910, "rejoin_sv": 87, "total": 997, "pct": 20.86, "weight": 34204.01, "ss_close": 4089, "sv_close": 691},
}

for d, d_str in [(date(2026, 10, 26), "26"), (date(2026, 10, 27), "27")]:
    print(f"\n=================================================================")
    print(f"       SCHEME REJOINING INDEPENDENT AUDIT FOR: {d.isoformat()}")
    print(f"=================================================================")
    engine = RejoiningMatchingEngine(cutoff_date=d)
    res = engine.process(raw_close_rows, raw_ss_rows, raw_sv_rows)
    
    s1 = res.table1_summary
    s2 = res.table2_summary
    s3 = res.table3_summary
    
    print(f"Table 1 Summary: Closed={s1.closed_count}, Rejoin SS={s1.rejoin_ss}, Rejoin SV={s1.rejoin_sv}, Total={s1.total_rejoin}, Pct={s1.rejoin_percentage:.2f}%")
    print(f"Table 2 Weight : Closed Weight={s2.closed_weight:.2f} g, Rejoin Weight={s2.total_rejoin_weight:.2f} g ({s2.rejoin_weight_percentage:.2f}%)")
    print(f"Table 3 Scheme : {dict(s3)}")
    
    print("\nShowroom Breakdown (Table 1):")
    print(f"{'Showroom':10s} | {'Closed':7s} | {'SS Rejoin':10s} | {'SV Rejoin':10s} | {'Total':7s} | {'Rejoin %':9s}")
    print("-" * 65)
    for r in res.table1_rows:
        print(f"{r.showroom:10s} | {r.closed_count:7d} | {r.rejoin_ss:10d} | {r.rejoin_sv:10d} | {r.total_rejoin:7d} | {r.rejoin_percentage:8.2f}%")
        
    if d_str == "26":
        print("\n--- COMPARISON WITH REFERENCE SCREENSHOT (26-OCT) ---")
        mismatches = 0
        for r in res.table1_rows:
            ref = REF_CLOSE_26.get(r.showroom, {})
            diff_closed = r.closed_count - ref.get("closed", 0)
            diff_rejoin = r.total_rejoin - ref.get("total", 0)
            if diff_closed != 0 or diff_rejoin != 0:
                print(f"  MISMATCH in {r.showroom}: Closed diff {diff_closed:+d}, Rejoin diff {diff_rejoin:+d}")
                mismatches += 1
        if mismatches == 0:
            print("  ALL 9 SHOWROOMS MATCH THE 26-OCTOBER REJOINING SCREENSHOT WITH ZERO DIFFERENCE!")
