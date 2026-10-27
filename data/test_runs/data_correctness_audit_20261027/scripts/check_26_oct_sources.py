import os
import sys
from pathlib import Path
from datetime import date, datetime
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")

from app.google_sheets.client import GoogleApiSheetsService

sa_path = str(project_root / "secrets" / "service_account.json")
sheets_service = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)

enrollment_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID", "1Mij4hMfZN-2qM_B6E-r71gC6k0Y0EaK1yG1XJq76zCc")
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID", "1N-4NRCNGHs702W0TlpQpGqgpPsJV9xH5QwXFfR18j2U")
closing_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID", "1i-NCumDUIfRvYTxg6ZgN9t3wcGTrte46PcvaJpw4BGg")

print(f"Enrollment Sheet ID: {enrollment_id}")
print(f"Digi Sheet ID      : {digi_id}")
print(f"Closing Sheet ID   : {closing_id}")

# 1. Check Digi OCT Pivot
print("\n--- Reading Digi OCT Pivot (top 20 rows) ---")
try:
    digi_meta = sheets_service.get_spreadsheet_metadata(digi_id)
    sheets = [s["properties"]["title"] for s in digi_meta.get("sheets", [])]
    print(f"Available Digi sheets: {sheets}")
    pivot_rows = sheets_service.read_range(digi_id, "OCT Pivot!A1:T20")
    for r in pivot_rows[:10]:
        print(r[:8])
except Exception as e:
    print(f"Error reading Digi: {e}")

# 2. Check Enrollment Consolidate Report - Oct
print("\n--- Reading Consolidate Report - Oct (top 35 rows) ---")
try:
    enr_rows = sheets_service.read_range(enrollment_id, "'Consolidate Report - Oct'!E1:L35")
    for idx, r in enumerate(enr_rows[:35], start=1):
        if r and any(r):
            print(f"Row {idx:2d}: {r}")
except Exception as e:
    print(f"Error reading Consolidate Report: {e}")

# 3. Check Closing October - 2026 for 26 Oct records
print("\n--- Checking Closed Records for 26 Oct ---")
try:
    cl_meta = sheets_service.get_spreadsheet_metadata(closing_id)
    cl_sheets = [s["properties"]["title"] for s in cl_meta.get("sheets", [])]
    print(f"Available Closing sheets: {cl_sheets}")
    cl_rows = sheets_service.read_range(closing_id, "'October - 2026'!A2:J")
    count_26 = 0
    total_cl = 0
    for r in cl_rows:
        if r and r[0].strip():
            total_cl += 1
            if "26/10" in r[0] or "2026-10-26" in r[0] or "26-10-2026" in r[0] or "26-10-26" in r[0]:
                count_26 += 1
    print(f"Total closed rows: {total_cl}, Closed rows on 26 Oct: {count_26}")
except Exception as e:
    print(f"Error reading Closing: {e}")

# 4. Check SS - Oct for 26 Oct records
print("\n--- Checking SS - Oct for 26 Oct records ---")
try:
    ss_rows = sheets_service.read_range(enrollment_id, "'SS - Oct'!A2:N")
    count_26_ss = 0
    total_ss = len(ss_rows)
    for r in ss_rows:
        if len(r) > 5 and r[5].strip():
            d_str = r[5].strip()
            if "26/10" in d_str or "2026-10-26" in d_str or "26-10-2026" in d_str:
                count_26_ss += 1
    print(f"Total SS rows: {total_ss}, SS rows on 26 Oct: {count_26_ss}")
except Exception as e:
    print(f"Error reading SS - Oct: {e}")

# 5. Check Closing Report Table on October - 2026!L1:Q30
print("\n--- Reading Scheme Rejoining Summary Table (October - 2026!L1:Q30) ---")
try:
    cl_table_rows = sheets_service.read_range(closing_id, "'October - 2026'!L1:Q30")
    for idx, r in enumerate(cl_table_rows, start=1):
        if r and any(r):
            print(f"Row {idx:2d}: {r}")
except Exception as e:
    print(f"Error reading Closing summary table: {e}")

