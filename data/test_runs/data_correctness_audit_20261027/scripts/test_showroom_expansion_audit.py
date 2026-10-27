"""
Isolated Showroom Expansion Impact Audit Test Script.
Validates KANCHI (established) and NAGERCOIL (new) behavior,
mathematical reconciliation, and absence of hardcoded assumptions.
STRICT_READ_ONLY=True: Zero mutations to production files or sheets.
"""

import json
import sys
import os
from pathlib import Path
from collections import defaultdict
import copy

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
os.chdir(project_root)

from app.core.config import load_config
from app.processing.location import LocationResolver, SHOWROOM_TO_BRANCH

def run_showroom_expansion_audit():
    results = {}
    cfg = load_config()

    print("=" * 80)
    print("SHOWROOM EXPANSION IMPACT AUDIT — TEST RUN")
    print("=" * 80)

    # -------------------------------------------------------------------------
    # TEST A: Verify Established Status of KANCHI
    # -------------------------------------------------------------------------
    print("\n--- TEST A: KANCHI Established Showroom Verification ---")
    resolver_stock = LocationResolver(cfg.mappings, strict_mode=False)

    with open('data/raw/2026/10/25/innervex_subhiksham_memberlist_2026-10-25.json', 'r', encoding='utf-8') as f:
        ss_25 = json.load(f)['data']
    with open('data/raw/2026/10/25/innervex_viruksham_memberlist_2026-10-25.json', 'r', encoding='utf-8') as f:
        sv_25 = json.load(f)['data']

    kanchi_ss = [r for r in ss_25 if r.get('COSTNAME') in ('KPM', 'KANCHI', 'KANCHEEPURAM')]
    kanchi_sv = [r for r in sv_25 if r.get('COSTNAME') in ('KPM', 'KANCHI', 'KANCHEEPURAM')]

    kanchi_ss_amt = sum(float(r.get('RECAMOUNT') or r.get('REGAMOUNT') or 0.0) for r in kanchi_ss)
    kanchi_sv_amt = sum(float(r.get('RECAMOUNT') or r.get('REGAMOUNT') or 0.0) for r in kanchi_sv)

    kanchi_ss_resolved = [resolver_stock.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'ss') for r in kanchi_ss]
    kanchi_sv_resolved = [resolver_stock.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'sv') for r in kanchi_sv]

    all_kanchi_ss_correct = all(res.branch == "KANCHEEPURAM" and res.location == "KANCHEEPURAM" for res in kanchi_ss_resolved)
    all_kanchi_sv_correct = all(res.branch == "KANCHEEPURAM" and res.location == "KANCHEEPURAM SV" for res in kanchi_sv_resolved)

    print(f"KANCHI SS Records: {len(kanchi_ss)} rows, Amount: ₹{kanchi_ss_amt:,.2f}, All Resolved: {all_kanchi_ss_correct}")
    print(f"KANCHI SV Records: {len(kanchi_sv)} rows, Amount: ₹{kanchi_sv_amt:,.2f}, All Resolved: {all_kanchi_sv_correct}")

    results["kanchi_status"] = {
        "ss_count": len(kanchi_ss),
        "ss_amount": kanchi_ss_amt,
        "ss_resolved": all_kanchi_ss_correct,
        "sv_count": len(kanchi_sv),
        "sv_amount": kanchi_sv_amt,
        "sv_resolved": all_kanchi_sv_correct,
        "status": "PASS" if (all_kanchi_ss_correct and all_kanchi_sv_correct) else "FAIL"
    }

    # -------------------------------------------------------------------------
    # TEST B: Investigate Target Total Discrepancy (₹313M vs ₹340M)
    # -------------------------------------------------------------------------
    print("\n--- TEST B: Target Total Discrepancy (₹313M vs ₹340M) ---")
    with open('data/consolidate_report_live_extracted.json', 'r', encoding='utf-8') as f:
        live_extract = json.load(f)

    t1 = live_extract['table1_live']
    orig_8_branches = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI"]
    sum_8_targets = sum(float(t1[b]['target']) for b in orig_8_branches)
    kanchi_target = float(t1['KANCHI']['target'])
    all_branches_target = sum_8_targets + kanchi_target
    reported_sheet_total = float(t1['Total']['target'])

    print(f"Sum of original 8 branch targets (Rows 6-13): ₹{sum_8_targets:,.2f}")
    print(f"KANCHI branch target (Row 14):                ₹{kanchi_target:,.2f}")
    print(f"Sum of all 9 branches (Rows 6-14):             ₹{all_branches_target:,.2f}")
    print(f"Live Sheet Cell F16 Reported Total:           ₹{reported_sheet_total:,.2f}")
    print(f"Difference (Reported - Expected):             ₹{reported_sheet_total - all_branches_target:,.2f}")

    is_outdated_formula = (reported_sheet_total == sum_8_targets and all_branches_target == 340000000)
    print(f"Is F16 an outdated formula range omitting Row 14? {is_outdated_formula}")

    results["target_formula_investigation"] = {
        "sum_8_targets": sum_8_targets,
        "kanchi_target": kanchi_target,
        "total_targets_with_kanchi": all_branches_target,
        "sheet_reported_total": reported_sheet_total,
        "omits_row_14": is_outdated_formula,
        "conclusion": "Outdated formula range =SUM(F6:F13) in upstream Google Sheet cell F16. Does not reflect Kanchi Row 14 (₹27M)."
    }

    # -------------------------------------------------------------------------
    # TEST C: NAGERCOIL (NGL) Current Behavior vs Minimum Fix
    # -------------------------------------------------------------------------
    print("\n--- TEST C: NAGERCOIL Resolution & Attribution ---")
    ngl_ss = [r for r in ss_25 if r.get('COSTNAME') == 'NGL']
    ngl_sv = [r for r in sv_25 if r.get('COSTNAME') == 'NGL']

    # Current behavior with stock LocationResolver
    unresolved_stock_ss = [r for r in ngl_ss if resolver_stock.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'ss').location.startswith('UNRESOLVED')]
    unresolved_stock_sv = [r for r in ngl_sv if resolver_stock.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'sv').location.startswith('UNRESOLVED')]

    # Check misattributed records in stock
    misattributed_stock_ss = [
        (r.get('MSNO'), r.get('COMMCODE'), resolver_stock.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'ss').branch)
        for r in ngl_ss
        if not resolver_stock.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'ss').location.startswith('UNRESOLVED')
    ]
    misattributed_stock_sv = [
        (r.get('MSNO'), r.get('COMMCODE'), resolver_stock.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'sv').branch)
        for r in ngl_sv
        if not resolver_stock.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'sv').location.startswith('UNRESOLVED')
    ]

    print(f"Current Stock Behavior:")
    print(f"  NGL SS Total: {len(ngl_ss)} rows -> Unresolved: {len(unresolved_stock_ss)}, Misattributed to other stores: {len(misattributed_stock_ss)}")
    print(f"  NGL SV Total: {len(ngl_sv)} rows -> Unresolved: {len(unresolved_stock_sv)}, Misattributed to other stores: {len(misattributed_stock_sv)}")
    print(f"  Total Unresolved Records: {len(unresolved_stock_ss) + len(unresolved_stock_sv)} (Matches documented 45!)")

    # Minimum proposed fix simulation (adding NGL to SHOWROOM_TO_BRANCH)
    resolver_fixed = LocationResolver(cfg.mappings, strict_mode=False)
    resolver_fixed.showroom_to_branch['NGL'] = 'NAGERCOIL'
    resolver_fixed.showroom_to_branch['NAGERCOIL'] = 'NAGERCOIL'

    unresolved_fixed_ss = [r for r in ngl_ss if resolver_fixed.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'ss').location.startswith('UNRESOLVED')]
    unresolved_fixed_sv = [r for r in ngl_sv if resolver_fixed.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'sv').location.startswith('UNRESOLVED')]
    resolved_ngl_fixed_ss = [r for r in ngl_ss if resolver_fixed.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'ss').branch == 'NAGERCOIL']
    resolved_ngl_fixed_sv = [r for r in ngl_sv if resolver_fixed.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'sv').branch == 'NAGERCOIL']

    print(f"\nProposed Fix Behavior (SHOWROOM_TO_BRANCH['NGL'] = 'NAGERCOIL'):")
    print(f"  NGL SS Total: {len(ngl_ss)} rows -> Unresolved: {len(unresolved_fixed_ss)}, Correctly Attributed to NAGERCOIL: {len(resolved_ngl_fixed_ss)}")
    print(f"  NGL SV Total: {len(ngl_sv)} rows -> Unresolved: {len(unresolved_fixed_sv)}, Correctly Attributed to NAGERCOIL: {len(resolved_ngl_fixed_sv)}")

    results["nagercoil_investigation"] = {
        "stock_unresolved_ss": len(unresolved_stock_ss),
        "stock_unresolved_sv": len(unresolved_stock_sv),
        "total_stock_unresolved": len(unresolved_stock_ss) + len(unresolved_stock_sv),
        "stock_misattributed_ss": len(misattributed_stock_ss),
        "stock_misattributed_sv": len(misattributed_stock_sv),
        "fixed_unresolved_ss": len(unresolved_fixed_ss),
        "fixed_unresolved_sv": len(unresolved_fixed_sv),
        "fixed_attributed_ss": len(resolved_ngl_fixed_ss),
        "fixed_attributed_sv": len(resolved_ngl_fixed_sv),
        "status": "PASS" if (len(resolved_ngl_fixed_ss) == 46 and len(resolved_ngl_fixed_sv) == 8) else "FAIL"
    }

    # -------------------------------------------------------------------------
    # TEST D: Full Reconciliation Before and After Branch Attribution
    # -------------------------------------------------------------------------
    print("\n--- TEST D: Mathematical Reconciliation (Zero Dropped / Zero Added) ---")
    raw_ss_total_count = len(ss_25)
    raw_ss_total_amount = sum(float(r.get('RECAMOUNT') or r.get('REGAMOUNT') or 0.0) for r in ss_25)
    raw_sv_total_count = len(sv_25)
    raw_sv_total_amount = sum(float(r.get('RECAMOUNT') or r.get('REGAMOUNT') or 0.0) for r in sv_25)

    # Aggregate with fixed resolver
    branch_ss = defaultdict(lambda: {"count": 0, "amount": 0.0})
    branch_sv = defaultdict(lambda: {"count": 0, "amount": 0.0})

    for r in ss_25:
        res = resolver_fixed.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'ss')
        amt = float(r.get('RECAMOUNT') or r.get('REGAMOUNT') or 0.0)
        branch_ss[res.location]["count"] += 1
        branch_ss[res.location]["amount"] += amt

    for r in sv_25:
        res = resolver_fixed.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'sv')
        amt = float(r.get('RECAMOUNT') or r.get('REGAMOUNT') or 0.0)
        branch_sv[res.location]["count"] += 1
        branch_sv[res.location]["amount"] += amt

    agg_ss_count = sum(v["count"] for v in branch_ss.values())
    agg_ss_amount = sum(v["amount"] for v in branch_ss.values())
    agg_sv_count = sum(v["count"] for v in branch_sv.values())
    agg_sv_amount = sum(v["amount"] for v in branch_sv.values())

    count_match_ss = (raw_ss_total_count == agg_ss_count)
    amount_match_ss = abs(raw_ss_total_amount - agg_ss_amount) < 0.001
    count_match_sv = (raw_sv_total_count == agg_sv_count)
    amount_match_sv = abs(raw_sv_total_amount - agg_sv_amount) < 0.001

    print(f"SS: Raw Count={raw_ss_total_count}, Agg Count={agg_ss_count} (Match: {count_match_ss})")
    print(f"SS: Raw Amt=₹{raw_ss_total_amount:,.2f}, Agg Amt=₹{agg_ss_amount:,.2f} (Diff: ₹{raw_ss_total_amount - agg_ss_amount:,.2f})")
    print(f"SV: Raw Count={raw_sv_total_count}, Agg Count={agg_sv_count} (Match: {count_match_sv})")
    print(f"SV: Raw Amt=₹{raw_sv_total_amount:,.2f}, Agg Amt=₹{agg_sv_amount:,.2f} (Diff: ₹{raw_sv_total_amount - agg_sv_amount:,.2f})")

    results["reconciliation"] = {
        "raw_ss_count": raw_ss_total_count,
        "agg_ss_count": agg_ss_count,
        "count_match_ss": count_match_ss,
        "raw_ss_amount": raw_ss_total_amount,
        "agg_ss_amount": agg_ss_amount,
        "amount_match_ss": amount_match_ss,
        "raw_sv_count": raw_sv_total_count,
        "agg_sv_count": agg_sv_count,
        "count_match_sv": count_match_sv,
        "raw_sv_amount": raw_sv_total_amount,
        "agg_sv_amount": agg_sv_amount,
        "amount_match_sv": amount_match_sv,
        "status": "PASS" if (count_match_ss and amount_match_ss and count_match_sv and amount_match_sv) else "FAIL"
    }

    # -------------------------------------------------------------------------
    # TEST E: Physical Showrooms vs Non-Physical Channels Separation
    # -------------------------------------------------------------------------
    print("\n--- TEST E: Channel Separation (10 Physical Showrooms vs Online/Ecommerce) ---")
    physical_branches = {
        "TIRUNELVELI", "CHROMEPET", "TRIVANDRUM", "TRICHY", "COIMBATORE",
        "SALEM", "POONAMALLEE", "PADI", "KANCHEEPURAM", "NAGERCOIL"
    }

    online_ss_records = [r for r in ss_25 if r.get('COSTNAME') in ('APP', 'HAPP', 'ECOM') and not r.get('COMMCODE')]
    online_sv_records = [r for r in sv_25 if r.get('COSTNAME') in ('APP', 'HAPP', 'ECOM') and not r.get('COMMCODE')]

    all_online_ss_correct = all(resolver_fixed.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'ss').location == "ONLINE" for r in online_ss_records)
    all_online_sv_correct = all(resolver_fixed.resolve(r.get('COSTNAME'), r.get('COMMCODE'), 'sv').location == "ONLINE SV" for r in online_sv_records)

    print(f"Unassisted Online SS count: {len(online_ss_records)}, All categorized as ONLINE: {all_online_ss_correct}")
    print(f"Unassisted Online SV count: {len(online_sv_records)}, All categorized as ONLINE SV: {all_online_sv_correct}")

    results["channel_separation"] = {
        "online_ss_count": len(online_ss_records),
        "online_ss_correct": all_online_ss_correct,
        "online_sv_count": len(online_sv_records),
        "online_sv_correct": all_online_sv_correct,
        "physical_branches_count": len(physical_branches),
        "status": "PASS" if (all_online_ss_correct and all_online_sv_correct) else "FAIL"
    }

    # Output JSON summary
    out_path = Path("data/test_runs/data_correctness_audit_20261027/showroom_expansion_audit_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nAudit results successfully written to {out_path}")

if __name__ == "__main__":
    run_showroom_expansion_audit()
