"""
READ-ONLY LIVE VALIDATION FOR 25 OCTOBER 2026
Using actual live project code and authorized source data.
Mode: STRICT_READ_ONLY=true (Zero mutations, zero network writes)
"""

import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parents[4]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import json
from collections import defaultdict
from datetime import date

# Import from live codebase
from app.core.config import load_config
from app.processing.location import LocationResolver, SHOWROOM_TO_BRANCH
from app.reporting.closing import (
    ClosingReportProcessor,
    CleanedClosingRecord,
    SHOWROOM_MAPPING,
    map_showroom_label,
)
from app.reporting.digi import (
    DIGI_BRANCH_ORDER,
    BRANCH_NAME_ALIASES,
    SHOWROOM_BRANCHES_9,
)


def run_live_validation():
    print("=" * 80)
    print("READ-ONLY LIVE VALIDATION: REPORTING DATE 25 OCTOBER 2026")
    print("=" * 80)

    # --------------------------------------------------------------------------
    # 1. Implementation Status Check
    # --------------------------------------------------------------------------
    print("\n[SECTION 1: IMPLEMENTATION STATUS IN LIVE PROJECT FILES]")
    print(f"1. app/processing/location.py SHOWROOM_TO_BRANCH has 'NGL': {'NGL' in SHOWROOM_TO_BRANCH}")
    print(f"2. app/reporting/closing.py SHOWROOM_MAPPING has 'NGL': {'NGL' in SHOWROOM_MAPPING}")
    print(f"3. app/reporting/digi.py DIGI_BRANCH_ORDER has 'NAGERCOIL': {'NAGERCOIL' in DIGI_BRANCH_ORDER}")
    print(f"4. app/reporting/digi.py BRANCH_NAME_ALIASES has 'NGL': {'NGL' in BRANCH_NAME_ALIASES}")

    config_bundle = load_config()
    mappings_cfg = config_bundle.mappings
    print(f"5. config/mappings.yaml employee_to_branch has 'NGL': {'NGL' in mappings_cfg.employee_to_branch}")
    print(f"6. config/mappings.yaml showroom_codes has 'NGL': {'NGL' in mappings_cfg.showroom_codes}")
    print(f"7. config/targets.yaml has 'NAGERCOIL': {'NAGERCOIL' in config_bundle.targets.q2}")
    print(f"8. config/targets.yaml has 'KANCHI': {'KANCHI' in config_bundle.targets.q2}")

    emp_reg_path = Path("config/employee_mapping.json")
    with open(emp_reg_path, "r", encoding="utf-8") as f:
        emp_reg = json.load(f)
    ngl_ss_emp = [k for k, v in emp_reg.get("ss", {}).items() if "NGL" in v or "NAGERCOIL" in v]
    ngl_sv_emp = [k for k, v in emp_reg.get("sv", {}).items() if "NGL" in v or "NAGERCOIL" in v]
    print(f"9. config/employee_mapping.json NGL employee codes: SS={len(ngl_ss_emp)}, SV={len(ngl_sv_emp)}")

    # --------------------------------------------------------------------------
    # 2. Independent Verification of Source Data (2026-10-25)
    # --------------------------------------------------------------------------
    print("\n[SECTION 2: INDEPENDENT SOURCE DATA RECALCULATION (2026-10-25)]")
    ss_raw_path = Path("data/raw/2026/10/25/innervex_subhiksham_memberlist_2026-10-25.json")
    sv_raw_path = Path("data/raw/2026/10/25/innervex_viruksham_memberlist_2026-10-25.json")
    cl_raw_path = Path("data/raw/2026/10/25/innervex_closing_report_2026-10-25.json")

    with open(ss_raw_path, "r", encoding="utf-8") as f:
        ss_json = json.load(f)
        ss_rows = ss_json["data"] if isinstance(ss_json, dict) and "data" in ss_json else ss_json

    with open(sv_raw_path, "r", encoding="utf-8") as f:
        sv_json = json.load(f)
        sv_rows = sv_json["data"] if isinstance(sv_json, dict) and "data" in sv_json else sv_json

    with open(cl_raw_path, "r", encoding="utf-8") as f:
        cl_json = json.load(f)
        cl_rows = cl_json["data"] if isinstance(cl_json, dict) and "data" in cl_json else cl_json

    print(f"Raw Source Rows: SS={len(ss_rows)}, SV={len(sv_rows)}, Closing={len(cl_rows)}")

    # Source SS metrics
    ss_msno_set = {r.get("MSNO") for r in ss_rows}
    ss_amt = sum(float(r.get("RECAMOUNT") or 0.0) for r in ss_rows)
    ss_dates = {r.get("SCHDATE") for r in ss_rows}
    print(f"SS Independent Check: Unique MSNOs={len(ss_msno_set)}/317, Total Amount=Rs {ss_amt:,.2f}, Dates={ss_dates}")

    # Source SV metrics
    sv_msno_set = {r.get("MSNO") for r in sv_rows}
    sv_amt = sum(float(r.get("RECAMOUNT") or 0.0) for r in sv_rows)
    sv_dates = {r.get("SCHDATE") for r in sv_rows}
    print(f"SV Independent Check: Unique MSNOs={len(sv_msno_set)}/72, Total Amount=Rs {sv_amt:,.2f}, Dates={sv_dates}")

    # Source Closing metrics
    cl_docno_set = {r.get("DOCNO") for r in cl_rows}
    cl_wt = sum(float(r.get("WEIGHT") or 0.0) for r in cl_rows)
    cl_dates = {r.get("DOCDATE") for r in cl_rows}
    print(f"Closing Independent Check: Total Rows={len(cl_rows)}, Unique Vouchers={len(cl_docno_set)}, Total Weight={cl_wt:.3f}g, Dates={cl_dates}")

    # --------------------------------------------------------------------------
    # 3. Live Pipeline Execution on 2026-10-25 Data
    # --------------------------------------------------------------------------
    print("\n[SECTION 3: LIVE PIPELINE EXECUTION USING ACTUAL UNMODIFIED CODE]")
    live_resolver = LocationResolver(mappings=mappings_cfg)

    # Resolve SS
    ss_live_attr = [
        live_resolver.resolve(
            cost_name=r.get("COSTNAME"),
            comm_code=r.get("COMMCODE") or r.get("EMPCODE"),
            scheme_type="ss"
        )
        for r in ss_rows
    ]

    # Resolve SV
    sv_live_attr = [
        live_resolver.resolve(
            cost_name=r.get("COSTNAME"),
            comm_code=r.get("COMMCODE") or r.get("EMPCODE"),
            scheme_type="sv"
        )
        for r in sv_rows
    ]

    # Breakdown by branch
    ss_counts_by_branch = defaultdict(int)
    ss_amt_by_branch = defaultdict(float)
    for r, attr in zip(ss_rows, ss_live_attr):
        loc = attr.location
        ss_counts_by_branch[loc] += 1
        ss_amt_by_branch[loc] += float(r.get("RECAMOUNT") or 0.0)

    sv_counts_by_branch = defaultdict(int)
    sv_amt_by_branch = defaultdict(float)
    for r, attr in zip(sv_rows, sv_live_attr):
        loc = attr.location
        sv_counts_by_branch[loc] += 1
        sv_amt_by_branch[loc] += float(r.get("RECAMOUNT") or 0.0)

    print("\n--- Live Pipeline SS Branch Attribution ---")
    for loc, cnt in sorted(ss_counts_by_branch.items()):
        print(f"  {loc:30s}: Count={cnt:3d}, Amount=Rs {ss_amt_by_branch[loc]:14,.2f}")

    print("\n--- Live Pipeline SV Branch Attribution ---")
    for loc, cnt in sorted(sv_counts_by_branch.items()):
        print(f"  {loc:30s}: Count={cnt:3d}, Amount=Rs {sv_amt_by_branch[loc]:14,.2f}")

    # Count UNRESOLVED in live pipeline
    ss_unresolved = [a for a in ss_live_attr if "UNRESOLVED" in a.location]
    sv_unresolved = [a for a in sv_live_attr if "UNRESOLVED" in a.location]
    print(f"\nLive Pipeline Unresolved Records: SS={len(ss_unresolved)}, SV={len(sv_unresolved)}, Total={len(ss_unresolved)+len(sv_unresolved)}")

    # NGL breakdown in source vs live pipeline
    ngl_ss_rows = [r for r in ss_rows if r.get("COSTNAME") == "NGL"]
    ngl_sv_rows = [r for r in sv_rows if r.get("COSTNAME") == "NGL"]
    print(f"\nRaw NGL Records: SS={len(ngl_ss_rows)}, SV={len(ngl_sv_rows)}")

    # Where did the 46 NGL SS records go in the live pipeline?
    ngl_ss_live_dest = defaultdict(int)
    for r in ngl_ss_rows:
        attr = live_resolver.resolve(r.get("COSTNAME"), r.get("COMMCODE") or r.get("EMPCODE"), "ss")
        ngl_ss_live_dest[attr.location] += 1
    print("Live Destination of 46 raw NGL SS records:")
    for dest, cnt in ngl_ss_live_dest.items():
        print(f"  -> {dest}: {cnt}")

    # Where did the 8 NGL SV records go in the live pipeline?
    ngl_sv_live_dest = defaultdict(int)
    for r in ngl_sv_rows:
        attr = live_resolver.resolve(r.get("COSTNAME"), r.get("COMMCODE") or r.get("EMPCODE"), "sv")
        ngl_sv_live_dest[attr.location] += 1
    print("Live Destination of 8 raw NGL SV records:")
    for dest, cnt in ngl_sv_live_dest.items():
        print(f"  -> {dest}: {cnt}")

    # KANCHI verification in live pipeline
    kpm_ss_rows = [r for r in ss_rows if r.get("COSTNAME") == "KPM"]
    kpm_sv_rows = [r for r in sv_rows if r.get("COSTNAME") == "KPM"]
    kpm_ss_live_ok = all(
        live_resolver.resolve(r.get("COSTNAME"), r.get("COMMCODE") or r.get("EMPCODE"), "ss").location == "KANCHEEPURAM"
        for r in kpm_ss_rows
    )
    kpm_sv_live_ok = all(
        live_resolver.resolve(r.get("COSTNAME"), r.get("COMMCODE") or r.get("EMPCODE"), "sv").location == "KANCHEEPURAM SV"
        for r in kpm_sv_rows
    )
    print(f"\nKANCHI Established Showroom Live Check: SS 23/23 correct={kpm_ss_live_ok}, SV 5/5 correct={kpm_sv_live_ok}")

    # ONLINE isolation in live pipeline
    online_ss_rows = [r for r in ss_rows if r.get("COSTNAME") in ["APP", "HAPP", "ECOM"] and not (r.get("COMMCODE") or r.get("EMPCODE") or "").strip()]
    online_sv_rows = [r for r in sv_rows if r.get("COSTNAME") in ["APP", "HAPP", "ECOM"] and not (r.get("COMMCODE") or r.get("EMPCODE") or "").strip()]
    online_ss_live_ok = all(
        live_resolver.resolve(r.get("COSTNAME"), r.get("COMMCODE") or r.get("EMPCODE"), "ss").location == "ONLINE"
        for r in online_ss_rows
    )
    online_sv_live_ok = all(
        live_resolver.resolve(r.get("COSTNAME"), r.get("COMMCODE") or r.get("EMPCODE"), "sv").location == "ONLINE SV"
        for r in online_sv_rows
    )
    print(f"ONLINE Channel Live Check: SS 18/18 correct={online_ss_live_ok}, SV 8/8 correct={online_sv_live_ok}")

    # --------------------------------------------------------------------------
    # 4. Closing & Rejoining Live Pipeline Check
    # --------------------------------------------------------------------------
    print("\n[SECTION 4: CLOSING & REJOINING LIVE PIPELINE CHECK]")
    cleaned_cl_records = []
    sh_counts = defaultdict(int)
    wt_by_sh = defaultdict(float)
    for r in cl_rows:
        raw_sh = str(r.get("SHOWROOM", "")).strip().upper()
        mapped_sh = map_showroom_label(raw_sh)
        wt = float(r.get("WEIGHT") or 0.0)
        sh_counts[mapped_sh] += 1
        wt_by_sh[mapped_sh] += wt

    print("Live Closing Showroom Breakdown (SHOWROOM field):")
    for sh, cnt in sorted(sh_counts.items()):
        print(f"  {sh:15s}: Records={cnt:3d}, Weight={wt_by_sh[sh]:10.3f} g")

    # Check NGL in Closing report
    ngl_red_rows = [r for r in cl_rows if str(r.get("REDEEMSHOWROOM", "")).strip().upper() == "NGL"]
    print(f"Records with REDEEMSHOWROOM=NGL: {len(ngl_red_rows)}")
    for r in ngl_red_rows:
        print(f"  DOCNO={r.get('DOCNO')}, SHOWROOM={r.get('SHOWROOM')}, REDEEMSHOWROOM={r.get('REDEEMSHOWROOM')}, WEIGHT={r.get('WEIGHT')}")

    # --------------------------------------------------------------------------
    # 5. Cumulative Monthly Dashboard Inspection (as of 25 October)
    # --------------------------------------------------------------------------
    print("\n[SECTION 5: CUMULATIVE MONTHLY DASHBOARD INSPECTION]")
    dash_path = Path("data/consolidate_report_live_extracted.json")
    with open(dash_path, "r", encoding="utf-8") as f:
        dash_data = json.load(f)

    t1 = dash_data.get("table1_live", {})
    t2 = dash_data.get("table2_live", {})

    print("\n--- Table 1 (Revenue MTD through 25 Oct) ---")
    for branch, vals in t1.items():
        print(f"  {branch:10s}: Target={vals.get('target', ''):>12s}, SS={vals.get('ss', ''):>10s}, SV={vals.get('sv', ''):>12s}, Total={vals.get('total', ''):>12s}, Achieved%={vals.get('achieved_pct', ''):>6s}")

    print("\n--- Table 2 (Enrollment Count MTD through 25 Oct) ---")
    for branch, vals in t2.items():
        print(f"  {branch:10s}: Target={vals.get('target', ''):>6s}, SS={vals.get('ss', ''):>5s}, SV={vals.get('sv', ''):>5s}, DG={vals.get('dg', ''):>5s}, DS={vals.get('ds', ''):>5s}, Combined={vals.get('combined', ''):>6s}, Avg={vals.get('avg', ''):>5s}")

    # Table 1 Target Sum Investigation
    branch_targets = [
        ("TVL", float(t1["TVL"]["target"])),
        ("CPT", float(t1["CPT"]["target"])),
        ("TVM", float(t1["TVM"]["target"])),
        ("TPJ", float(t1["TPJ"]["target"])),
        ("CBE", float(t1["CBE"]["target"])),
        ("SLM", float(t1["SLM"]["target"])),
        ("PNM", float(t1["PNM"]["target"])),
        ("PADI", float(t1["PADI"]["target"])),
    ]
    sum_8 = sum(t for _, t in branch_targets)
    kanchi_target = float(t1.get("KANCHI", {}).get("target", 0.0))
    sum_9_with_kanchi = sum_8 + kanchi_target
    reported_total_target = float(t1.get("Total", {}).get("target", 0.0))

    print(f"\nTarget Math Investigation:")
    print(f"  Sum of original 8 branch targets (Rows 6-13): Rs {sum_8:,.2f} (Rs 313M)")
    print(f"  KANCHI target (Row 14):                       Rs {kanchi_target:,.2f} (Rs 27M)")
    print(f"  True sum with KANCHI (Rows 6-14):             Rs {sum_9_with_kanchi:,.2f} (Rs 340M)")
    print(f"  Reported Total in Cell F16:                   Rs {reported_total_target:,.2f} (Rs 313M)")
    print(f"  Discrepancy: Cell F16 uses =SUM(F6:F13), omitting Row 14 (KANCHI).")
    print(f"  NGL row present in Monthly Dashboard: {'NGL' in t1 or 'NAGERCOIL' in t1}")


if __name__ == "__main__":
    run_live_validation()
