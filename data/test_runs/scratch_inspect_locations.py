import os
import sys
from pathlib import Path
from collections import Counter, defaultdict
from datetime import date
#dev console(sys.args [init--> git_rebase])
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

print("=== CHECKING SS - Oct LOCATION VALUES (<= 2026-10-27) ===")
ss_rows = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
ss_loc_counter = Counter()
ss_loc2_counter = Counter()
ss_cutoff_rows = 0

for r in ss_rows:
    d = parse_date_safe(r[5] if len(r) > 5 else "")
    if d and d <= date(2026, 10, 27):
        ss_cutoff_rows += 1
        loc2 = r[6] if len(r) > 6 else ""
        loc = r[7] if len(r) > 7 else ""
        ss_loc2_counter[loc2] += 1
        ss_loc_counter[loc] += 1

print(f"Total SS rows <= 27 Oct: {ss_cutoff_rows}")
print("Top LOCATION 2 in SS:")
for k, v in ss_loc2_counter.most_common():
    print(f"  {repr(k)}: {v}")
print("\nTop LOCATION in SS:")
for k, v in ss_loc_counter.most_common():
    print(f"  {repr(k)}: {v}")

print("\n=== CHECKING SV - Oct LOCATION VALUES (<= 2026-10-27) ===")
sv_rows = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
sv_loc_counter = Counter()
sv_loc2_counter = Counter()
sv_cutoff_rows = 0

for r in sv_rows:
    d = parse_date_safe(r[5] if len(r) > 5 else "")
    if d and d <= date(2026, 10, 27):
        sv_cutoff_rows += 1
        loc2 = r[6] if len(r) > 6 else ""
        loc = r[7] if len(r) > 7 else ""
        sv_loc2_counter[loc2] += 1
        sv_loc_counter[loc] += 1

print(f"Total SV rows <= 27 Oct: {sv_cutoff_rows}")
print("Top LOCATION 2 in SV:")
for k, v in sv_loc2_counter.most_common():
    print(f"  {repr(k)}: {v}")
print("\nTop LOCATION in SV:")
for k, v in sv_loc_counter.most_common():
    print(f"  {repr(k)}: {v}")
