import os
import sys
from pathlib import Path
from datetime import date

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.processing.rejoining import (
    RejoiningMatchingEngine,
    DEFAULT_PHYSICAL_SHOWROOMS,
    parse_date_safe,
    normalize_mobile,
    map_showroom_label,
)

sa_path = "secrets/service_account.json"
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")
enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")

target_date = date(2026, 10, 27)

# 1. Read closing records
close_raw = sheets.read_range(close_id, "'October - 2026'!A2:H")
print(f"Total raw closing rows in sheet: {len(close_raw)}")

# 2. Read enrollment records
ss_raw = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
sv_raw = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
print(f"Total raw SS rows: {len(ss_raw)}, SV rows: {len(sv_raw)}")

# Run rejoining engine with cutoff <= 2026-10-27
engine = RejoiningMatchingEngine(cutoff_date=target_date)
result = engine.process(
    closed_records=close_raw,
    ss_enrollments=ss_raw,
    sv_enrollments=sv_raw,
)

print("\n=======================================================")
print(f"SCHEME REJOINING REPORT AS ON {target_date.strftime('%d/%m/%Y')}")
print("=======================================================")
print("\n--- TABLE 1: MAIN REJOINING TABLE ---")
print(f"{'SHOWROOM':12s} {'CLOSED':8s} {'REJOIN SS':10s} {'REJOIN SV':10s} {'TOTAL REJOIN':13s} {'REJOIN %':10s}")
for r in result.table1_rows:
    print(f"{r.showroom:12s} {r.closed_count:8d} {r.rejoin_ss:10d} {r.rejoin_sv:10d} {r.total_rejoin:13d} {r.rejoin_percentage:9.2f}%")
s1 = result.table1_summary
print(f"{s1.showroom:12s} {s1.closed_count:8d} {s1.rejoin_ss:10d} {s1.rejoin_sv:10d} {s1.total_rejoin:13d} {s1.rejoin_percentage:9.2f}%")

print("\n--- TABLE 2: CLOSED WEIGHT TABLE ---")
print(f"{'SHOWROOM':12s} {'COUNT':8s} {'WEIGHT':12s}")
for r in result.table2_weight_rows:
    print(f"{r.showroom:12s} {r.closed_weight:8.2f}g") # wait, let's check attributes of RejoiningWeightRow
s2 = result.table2_summary
print(f"{s2.showroom:12s} {s2.closed_weight:8.2f}g")

print("\n--- TABLE 3: CLOSED COUNT TABLE ---")
print(f"{'SHOWROOM':12s} {'SS':8s} {'SV':8s} {'TOTAL':8s}")
for r in result.table3_scheme_rows:
    ss_c = r.scheme_counts.get("NEW SWARNA SUBHIKSHAM", 0) + r.scheme_counts.get("SS", 0)
    sv_c = r.scheme_counts.get("SWARNA VIRUKSHAM", 0) + r.scheme_counts.get("SV", 0)
    print(f"{r.showroom:12s} {ss_c:8d} {sv_c:8d} {r.total_count:8d}")
print(f"Summary Table 3: {result.table3_summary}")
