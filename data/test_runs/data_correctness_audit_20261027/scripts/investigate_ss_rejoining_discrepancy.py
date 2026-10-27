import os
import sys
import json
from pathlib import Path
from collections import defaultdict
from datetime import date, datetime
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")
load_dotenv(project_root / "scripts" / ".env")

from app.google_sheets.client import GoogleApiSheetsService
from app.reporting.closing import DEFAULT_CLOSED_REJOINING_SPREADSHEET_ID, map_showroom_label

sa_path = str(project_root / "secrets" / "service_account.json")
sheets_service = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
cl_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID", DEFAULT_CLOSED_REJOINING_SPREADSHEET_ID)
enrollment_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID", "1Mij4hMfZN-2qM_B6E-r71gC6k0Y0EaK1yG1XJq76zCc")

def parse_any_date(val):
    if not val:
        return None
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None

# 1. Read SS - Oct
print("Reading SS - Oct...")
ss_rows = sheets_service.read_range(enrollment_id, "SS - Oct!A2:M")
ss_by_mobile = defaultdict(list)
for r_i, r in enumerate(ss_rows, start=2):
    if len(r) > 9 and r[9].strip():
        mob = r[9].strip()
        dt = parse_any_date(r[5].strip()) if len(r) > 5 else None
        loc2 = r[6].strip() if len(r) > 6 else ""
        amt = r[8].strip() if len(r) > 8 else ""
        sch = r[10].strip() if len(r) > 10 else ""
        msno = r[3].strip() if len(r) > 3 else ""
        name = r[4].strip() if len(r) > 4 else ""
        ss_by_mobile[mob].append({
            "ss_sheet_row": r_i,
            "date": dt,
            "loc2": loc2,
            "amt": amt,
            "scheme": sch,
            "msno": msno,
            "name": name,
        })

# 2. Read October - 2026
print("Reading October - 2026...")
cl_rows = sheets_service.read_range(cl_id, "October - 2026!A2:J")
target_cutoff = date(2026, 10, 25)

PHYSICAL = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KPM"]
REF_SS_REJOIN = {
    "TVL": 124,
    "CPT": 211,
    "TVM": 117,
    "TPJ": 164,
    "CBE": 140,
    "SLM": 52,
    "PNM": 39,
    "PADI": 20,
    "KPM": 0,
}

matches_by_sh_all = defaultdict(int)
matches_by_sh_strictly_le_25 = defaultdict(int)
later_matches = []

for r_i, r in enumerate(cl_rows, start=2):
    if not r or not r[0].strip():
        continue
    dt = parse_any_date(r[0].strip())
    if not dt or dt > target_cutoff:
        continue
    raw_sh = r[7].strip() if len(r) > 7 else ""
    sh = map_showroom_label(raw_sh)
    if sh not in PHYSICAL:
        continue
    
    rej_ss = r[8].strip() if len(r) > 8 else ""
    if rej_ss and rej_ss not in ("0", "FALSE", "#N/A", ""):
        matches_by_sh_all[sh] += 1
        mob = r[3].strip() if len(r) > 3 else ""
        passbook = r[2].strip() if len(r) > 2 else ""
        name = r[1].strip() if len(r) > 1 else ""
        enrs = ss_by_mobile.get(mob, [])
        valid_enrs = [e for e in enrs if e["date"] and e["date"] <= target_cutoff]
        if valid_enrs:
            matches_by_sh_strictly_le_25[sh] += 1
        else:
            later_matches.append({
                "showroom": sh,
                "cl_row": r_i,
                "closed_date": dt,
                "name": name,
                "passbook": passbook,
                "mob": mob,
                "enrs": enrs
            })

print("\n" + "=" * 95)
print(f"{'Showroom':10s} | {'Current Sheet':14s} | {'Later Matches':14s} | {'Strictly <= 25 Oct':20s} | {'Ref Screenshot':15s} | {'Match?':8s}")
print("=" * 95)
for sh in PHYSICAL:
    curr = matches_by_sh_all[sh]
    strictly = matches_by_sh_strictly_le_25[sh]
    diff = curr - strictly
    ref = REF_SS_REJOIN[sh]
    matched = "EXACT" if strictly == ref else "MISMATCH"
    print(f"{sh:10s} | {curr:14d} | {diff:14d} | {strictly:20d} | {ref:15d} | {matched:8s}")

tot_curr = sum(matches_by_sh_all.values())
tot_diff = len(later_matches)
tot_strictly = sum(matches_by_sh_strictly_le_25.values())
tot_ref = sum(REF_SS_REJOIN.values())
tot_matched = "EXACT" if tot_strictly == tot_ref else "MISMATCH"

print("-" * 95)
print(f"{'TOTAL':10s} | {tot_curr:14d} | {tot_diff:14d} | {tot_strictly:20d} | {tot_ref:15d} | {tot_matched:8s}")
print("=" * 95)

print(f"\nExhaustive details of the {len(later_matches)} later-enrolled records:")
for idx, lm in enumerate(later_matches, start=1):
    masked_mob = lm["mob"][:2] + "****" + lm["mob"][-4:] if len(lm["mob"]) >= 6 else lm["mob"]
    print(f"\n[{idx}] Showroom: {lm['showroom']} | Sheet Row: {lm['cl_row']}")
    print(f"    Closed Date  : {lm['closed_date']}")
    print(f"    Customer Name: {lm['name']}")
    print(f"    Passbook     : {lm['passbook']}")
    print(f"    Mobile No    : {masked_mob}")
    print(f"    Matching Rule: VLOOKUP(D{lm['cl_row']}, 'SS - Oct'!J:K, 2, 0)")
    for e in lm["enrs"]:
        print(f"    -> Enrolled Record: Date={e['date']} (Row {e['ss_sheet_row']}), Branch={e['loc2']}, Amount=Rs {e['amt']}, Scheme={e['scheme']}, MSNO={e['msno']}")
