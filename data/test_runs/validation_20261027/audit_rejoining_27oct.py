import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
project_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.processing.rejoining import (
    parse_date_safe,
    normalize_mobile,
    map_showroom_label,
    RejoiningMatchingEngine,
)
from datetime import date

sa_path = str(project_root / "secrets" / "service_account.json")
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")
enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")

TARGET_DATE = date(2026, 10, 27)

raw_close = sheets.read_range(close_id, "'October - 2026'!A2:H")
raw_ss = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
raw_sv = sheets.read_range(enr_id, "'SV - Oct'!A2:N")

close_records = []
for idx, r in enumerate(raw_close, start=2):
    dt = parse_date_safe(r[0] if len(r) > 0 else "")
    if dt and dt <= TARGET_DATE:
        wt_str = (r[5] if len(r) > 5 else "0").replace(",", "").strip()
        close_records.append({
            "row_idx": idx,
            "docdate": dt,
            "name": r[1] if len(r) > 1 else "",
            "passbook": r[2] if len(r) > 2 else "",
            "mobile": normalize_mobile(r[3] if len(r) > 3 else ""),
            "scheme": r[4] if len(r) > 4 else "",
            "weight": float(wt_str) if wt_str else 0.0,
            "status": (r[6] if len(r) > 6 else "").strip().upper(),
            "showroom": map_showroom_label(r[7] if len(r) > 7 else ""),
        })

ss_records = []
for idx, r in enumerate(raw_ss, start=2):
    dt = parse_date_safe(r[5] if len(r) > 5 else "")
    if dt and dt <= TARGET_DATE:
        ss_records.append({
            "row_idx": idx,
            "mobile": normalize_mobile(r[9] if len(r) > 9 else ""),
            "schdate": dt,
            "location2": (r[6] if len(r) > 6 else "").strip(),
        })

sv_records = []
for idx, r in enumerate(raw_sv, start=2):
    dt = parse_date_safe(r[5] if len(r) > 5 else "")
    if dt and dt <= TARGET_DATE:
        sv_records.append({
            "row_idx": idx,
            "mobile": normalize_mobile(r[9] if len(r) > 9 else ""),
            "schdate": dt,
            "location2": (r[6] if len(r) > 6 else "").strip(),
        })

print(f"Total Closing records <= 27-Oct: {len(close_records)}")
print(f"Total SS records <= 27-Oct: {len(ss_records)}")
print(f"Total SV records <= 27-Oct: {len(sv_records)}")

engine = RejoiningMatchingEngine()
res = engine.process(close_records, ss_records, sv_records)

print("\n=== SCHEME REJOINING SUMMARY AS OF 27/10/2026 ===")
s1 = res.table1_summary
print(f"Closed Count  : {s1.closed_count}")
print(f"Rejoin SS     : {s1.rejoin_ss}")
print(f"Rejoin SV     : {s1.rejoin_sv}")
print(f"Total Rejoin  : {s1.total_rejoin}")
print(f"Rejoining %   : {s1.rejoin_percentage:.2f}%")

print("\n=== CLOSED WEIGHT SUMMARY ===")
s2 = res.table2_summary
print(f"Closed Count  : {s2.closed_count}")
print(f"Closed Weight : {s2.closed_weight:,.2f} g")

print("\n=== CLOSED COUNT BY SCHEME ===")
s3 = res.table3_summary
for sch, count in s3.items():
    print(f"  {sch:30s}: {count:5d}")
print(f"Total Closed Schemes: {sum(s3.values())}")

print("\n=== SHOWROOM BREAKDOWN TABLE 1 ===")
for r in res.table1_rows:
    print(f"  {r.showroom:15s} | Closed: {r.closed_count:4d} | Rejoin SS: {r.rejoin_ss:3d} | Rejoin SV: {r.rejoin_sv:3d} | Total: {r.total_rejoin:4d} | %: {r.rejoin_percentage:6.2f}%")
