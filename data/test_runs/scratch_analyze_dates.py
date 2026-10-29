import os
import sys
from pathlib import Path
from collections import Counter
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.processing.rejoining import parse_date_safe

sa_path = "secrets/service_account.json"
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)

enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")

print("=== ANALYZING SS - Oct DATES ===")
ss_rows = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
print(f"Total SS rows: {len(ss_rows)}")
ss_date_counter = Counter()
ss_invalid_dates = 0
for r in ss_rows:
    raw_d = r[5] if len(r) > 5 else ""
    d = parse_date_safe(raw_d)
    if d:
        ss_date_counter[d] += 1
    else:
        ss_invalid_dates += 1
for d in sorted(ss_date_counter.keys()):
    print(f"  {d}: {ss_date_counter[d]} records")
if ss_invalid_dates:
    print(f"  Invalid dates: {ss_invalid_dates}")

print("\n=== ANALYZING SV - Oct DATES ===")
sv_rows = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
print(f"Total SV rows: {len(sv_rows)}")
sv_date_counter = Counter()
sv_invalid_dates = 0
for r in sv_rows:
    raw_d = r[5] if len(r) > 5 else ""
    d = parse_date_safe(raw_d)
    if d:
        sv_date_counter[d] += 1
    else:
        sv_invalid_dates += 1
for d in sorted(sv_date_counter.keys()):
    print(f"  {d}: {sv_date_counter[d]} records")
if sv_invalid_dates:
    print(f"  Invalid dates: {sv_invalid_dates}")

print("\n=== ANALYZING October - 2026 (CLOSING) DATES ===")
close_rows = sheets.read_range(close_id, "'October - 2026'!A2:H")
print(f"Total Closing rows: {len(close_rows)}")
close_date_counter = Counter()
close_invalid_dates = 0
for r in close_rows:
    raw_d = r[0] if len(r) > 0 else ""
    d = parse_date_safe(raw_d)
    if d:
        close_date_counter[d] += 1
    else:
        close_invalid_dates += 1
for d in sorted(close_date_counter.keys()):
    print(f"  {d}: {close_date_counter[d]} records")
if close_invalid_dates:
    print(f"  Invalid dates: {close_invalid_dates}")

print("\n=== ANALYZING Digi OCT DATES ===")
digi_rows = sheets.read_range(digi_id, "'OCT'!A2:P")
print(f"Total Digi OCT rows: {len(digi_rows)}")
digi_date_counter = Counter()
digi_invalid_dates = 0
for r in digi_rows:
    raw_d = r[5] if len(r) > 5 else ""
    d = parse_date_safe(raw_d)
    if d:
        digi_date_counter[d] += 1
    else:
        digi_invalid_dates += 1
for d in sorted(digi_date_counter.keys()):
    print(f"  {d}: {digi_date_counter[d]} records")
if digi_invalid_dates:
    print(f"  Invalid dates: {digi_invalid_dates}")
