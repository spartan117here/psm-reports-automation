"""
Deep inspection of DigiGold and DigiSilver data for 25 October 2026.
Safety: STRICT_READ_ONLY=true, zero writes.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Enforce UTF-8 for console output
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

os.environ["STRICT_READ_ONLY"] = "true"

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PROJECT_ROOT / "scripts" / ".env")

from app.google_sheets.client import GoogleApiSheetsService
from app.reporting.digi import (
    DigiSourceReader,
    DEFAULT_DIGI_SPREADSHEET_ID,
    parse_schdate,
    DIGI_BRANCH_ORDER,
    ALL_SHOWROOM_BRANCHES,
    BRANCH_NAME_ALIASES,
    normalize_branch_name,
)

def inspect_digi_20261025():
    sa_path = str(PROJECT_ROOT / "secrets" / "service_account.json")
    sheets_service = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID", DEFAULT_DIGI_SPREADSHEET_ID)

    target_date = date(2026, 10, 25)
    print("=" * 80)
    print(f"DIGI GOLD / DIGI SILVER INSPECTION & RECONCILIATION FOR {target_date.isoformat()}")
    print("=" * 80)

    # 1. Read OCT raw data
    print("\nReading 'OCT' raw sheet...")
    oct_rows = sheets_service.read_range(digi_id, "OCT!A1:Z")
    print(f"Total rows in 'OCT' sheet: {len(oct_rows)}")
    if not oct_rows:
        print("ERROR: OCT sheet is empty!")
        return

    headers = [str(c).strip() for c in oct_rows[0]]
    print(f"Headers ({len(headers)}): {headers}")

    # Find relevant column indices
    col_map = {h.upper(): i for i, h in enumerate(headers)}
    schdate_idx = col_map.get("SCHDATE")
    scheme_idx = col_map.get("SCHEME") or col_map.get("SCHEMENAME")
    branch_idx = col_map.get("BRANCH") or col_map.get("BRANCHNAME") or col_map.get("COSTNAME") or col_map.get("SHOWROOM")
    amount_idx = col_map.get("AMOUNT") or col_map.get("RECAMOUNT")
    weight_idx = col_map.get("WEIGHT") or col_map.get("PUREWT") or col_map.get("GRAMS")
    msno_idx = col_map.get("MSNO") or col_map.get("MEMBERNO") or col_map.get("ACCOUNTNO")
    clientid_idx = col_map.get("CLIENTID") or col_map.get("CUSTID")

    print(f"Detected Column Indices:")
    for k, v in [("SCHDATE", schdate_idx), ("SCHEME", scheme_idx), ("BRANCH", branch_idx),
                 ("AMOUNT", amount_idx), ("WEIGHT", weight_idx), ("MSNO", msno_idx), ("CLIENTID", clientid_idx)]:
        print(f"  {k}: index {v} ({headers[v] if v is not None and v < len(headers) else 'None'})")

    # Analyze date distribution
    date_dist = defaultdict(int)
    rows_by_date = defaultdict(list)
    for r_idx, row in enumerate(oct_rows[1:], start=2):
        if schdate_idx is not None and len(row) > schdate_idx:
            parsed_d = parse_schdate(row[schdate_idx])
            if parsed_d:
                date_dist[parsed_d] += 1
                rows_by_date[parsed_d].append((r_idx, row))

    sorted_dates = sorted(date_dist.keys())
    print(f"\nDate range in OCT raw sheet: {sorted_dates[0]} to {sorted_dates[-1]}")
    print(f"Total unique dates: {len(sorted_dates)}")
    for d in sorted_dates[-7:]:
        print(f"  Date {d}: {date_dist[d]} rows")

    oct_25_rows = rows_by_date.get(target_date, [])
    print(f"\n>>> Records with SCHDATE == {target_date.isoformat()}: {len(oct_25_rows)} rows")

    # 2. Inspect OCT Pivot
    print("\nReading 'OCT Pivot' sheet...")
    pivot_rows = sheets_service.read_range(digi_id, "OCT Pivot!A1:Z35")
    print(f"Total rows read from 'OCT Pivot': {len(pivot_rows)}")
    for i, r in enumerate(pivot_rows[:15]):
        non_empty = [(col_idx, val) for col_idx, val in enumerate(r) if str(val).strip()]
        if non_empty:
            print(f"  Row {i+1}: {non_empty}")

    # 3. Use DigiSourceReader
    reader = DigiSourceReader(sheets_service=sheets_service, spreadsheet_id=digi_id)
    print("\nExecuting DigiSourceReader.extract_pivot_data('OCT Pivot')...")
    pivot_extraction = reader.extract_pivot_data("OCT Pivot")
    print(f"Pivot Extraction Result:")
    print(f"  Pivot Date Header: {pivot_extraction.pivot_date_header}")
    print(f"  Gold Values: {pivot_extraction.gold_values}")
    print(f"  Gold Showroom Subtotal: {pivot_extraction.gold_showroom_subtotal}")
    print(f"  Gold Grand Total: {pivot_extraction.gold_grand_total}")
    print(f"  Silver Values: {pivot_extraction.silver_values}")
    print(f"  Silver Showroom Subtotal: {pivot_extraction.silver_showroom_subtotal}")
    print(f"  Silver Grand Total: {pivot_extraction.silver_grand_total}")

    # 4. Check read_and_validate for target_date
    print(f"\nExecuting DigiSourceReader.read_and_validate({target_date})...")
    try:
        val_result = reader.read_and_validate(target_date, validate_subtotals=True, validate_pivot_date=True)
        print("PASS: read_and_validate succeeded!")
        print(f"  Source latest SCHDATE: {val_result.source_latest_schdate}")
        print(f"  Source records for report date: {val_result.source_records_for_report_date}")
        print(f"  Gold showroom subtotal: {val_result.gold_showroom_subtotal}, ecomm: {val_result.gold_ecommerce_subtotal}, total: {val_result.gold_overall_total}")
        print(f"  Silver showroom subtotal: {val_result.silver_showroom_subtotal}, ecomm: {val_result.silver_ecommerce_subtotal}, total: {val_result.silver_overall_total}")
    except Exception as e:
        print(f"read_and_validate raised: {type(e).__name__}: {e}")

    # 5. Raw 25 October Breakdown
    print(f"\n--- Raw 25 October 2026 Rows Breakdown ---")
    schemes_25 = defaultdict(int)
    branches_25 = defaultdict(int)
    branch_scheme_25 = defaultdict(lambda: defaultdict(int))
    branch_scheme_amt_25 = defaultdict(lambda: defaultdict(float))
    branch_scheme_wt_25 = defaultdict(lambda: defaultdict(float))
    msno_set_25 = set()
    dup_msno_25 = []

    for r_idx, row in oct_25_rows:
        sch = row[scheme_idx].strip() if scheme_idx is not None and len(row) > scheme_idx else "UNKNOWN_SCHEME"
        br = row[branch_idx].strip() if branch_idx is not None and len(row) > branch_idx else "UNKNOWN_BRANCH"
        amt_str = row[amount_idx] if amount_idx is not None and len(row) > amount_idx else "0"
        wt_str = row[weight_idx] if weight_idx is not None and len(row) > weight_idx else "0"
        msno = row[msno_idx].strip() if msno_idx is not None and len(row) > msno_idx else f"ROW_{r_idx}"

        try:
            amt = float(str(amt_str).replace(",", "").strip() or 0.0)
        except ValueError:
            amt = 0.0

        try:
            wt = float(str(wt_str).replace(",", "").strip() or 0.0)
        except ValueError:
            wt = 0.0

        if msno in msno_set_25:
            dup_msno_25.append((r_idx, msno))
        else:
            msno_set_25.add(msno)

        schemes_25[sch] += 1
        branches_25[br] += 1
        branch_scheme_25[br][sch] += 1
        branch_scheme_amt_25[br][sch] += amt
        branch_scheme_wt_25[br][sch] += wt

    print(f"Schemes on 25 Oct: {dict(schemes_25)}")
    print(f"Unique MSNOs on 25 Oct: {len(msno_set_25)} / {len(oct_25_rows)} rows (Duplicates: {len(dup_msno_25)})")
    if dup_msno_25:
        print(f"  Duplicate MSNOs: {dup_msno_25[:5]}")

    print("\nBranch & Scheme Counts on 25 Oct (Raw Data):")
    total_raw_gold_count = 0
    total_raw_silver_count = 0
    total_raw_gold_amt = 0.0
    total_raw_silver_amt = 0.0
    total_raw_gold_wt = 0.0
    total_raw_silver_wt = 0.0

    for br in sorted(branches_25.keys()):
        gold_c = sum(c for s, c in branch_scheme_25[br].items() if "GOLD" in s.upper())
        silver_c = sum(c for s, c in branch_scheme_25[br].items() if "SILVER" in s.upper())
        gold_a = sum(a for s, a in branch_scheme_amt_25[br].items() if "GOLD" in s.upper())
        silver_a = sum(a for s, a in branch_scheme_amt_25[br].items() if "SILVER" in s.upper())
        gold_w = sum(w for s, w in branch_scheme_wt_25[br].items() if "GOLD" in s.upper())
        silver_w = sum(w for s, w in branch_scheme_wt_25[br].items() if "SILVER" in s.upper())

        total_raw_gold_count += gold_c
        total_raw_silver_count += silver_c
        total_raw_gold_amt += gold_a
        total_raw_silver_amt += silver_a
        total_raw_gold_wt += gold_w
        total_raw_silver_wt += silver_w

        norm = normalize_branch_name(br)
        print(f"  Raw: '{br}' -> Norm: '{norm}': Gold Count={gold_c}, Amt={gold_a:,.2f}, Wt={gold_w:.3f}g | Silver Count={silver_c}, Amt={silver_a:,.2f}, Wt={silver_w:.3f}g")

    print(f"\nRaw Source Totals for 25 Oct:")
    print(f"  Gold:   Count={total_raw_gold_count}, Amount=Rs {total_raw_gold_amt:,.2f}, Weight={total_raw_gold_wt:.3f}g")
    print(f"  Silver: Count={total_raw_silver_count}, Amount=Rs {total_raw_silver_amt:,.2f}, Weight={total_raw_silver_wt:.3f}g")
    print(f"  Combined: Count={total_raw_gold_count + total_raw_silver_count}, Amount=Rs {total_raw_gold_amt + total_raw_silver_amt:,.2f}")

    # Check NGL, KANCHI, ONLINE in raw data
    print("\nSpecific Branch Attribution Checks for 25 Oct:")
    for query in ["NGL", "NAGERCOIL", "KPM", "KANCHI", "KANCHEEPURAM", "ONLINE", "ECOMMERCE", "APP"]:
        matched = [(br, sum(branch_scheme_25[br].values())) for br in branches_25 if query in br.upper()]
        if matched:
            print(f"  Matching '{query}': {matched}")
        else:
            print(f"  Matching '{query}': None")

if __name__ == "__main__":
    inspect_digi_20261025()
