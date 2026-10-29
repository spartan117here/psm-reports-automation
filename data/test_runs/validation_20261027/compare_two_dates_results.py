import json
from pathlib import Path

project_root = Path(__file__).resolve().parents[3]
results_path = project_root / "data" / "test_runs" / "validation_20261027" / "two_date_audit_results.json"

with open(results_path, "r", encoding="utf-8") as f:
    data = json.load(f)

for dt_str in ["26", "27"]:
    print(f"\n=======================================================")
    print(f"       COMPARISON FOR DATE: {dt_str} OCTOBER 2026")
    print(f"=======================================================")
    
    comp_rev = {r["branch"]: r for r in data[f"computed_{dt_str}"]["revenue"]}
    ref_rev = data[f"ref_{dt_str}"]["revenue"]
    
    print("\n--- TABLE 1: REVENUE COMPARISON ---")
    print(f"{'Branch':8s} | {'Target (Ref/Comp)':20s} | {'SS (Ref / Comp / Diff)':30s} | {'SV (Ref / Comp / Diff)':30s} | {'Total (Ref / Comp / Diff)':30s}")
    print("-" * 125)
    
    for b in ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KANCHI", "NGL", "ONLINE", "Total"]:
        c = comp_rev.get(b, {})
        r = ref_rev.get(b, {})
        
        ss_ref = r.get("ss", 0.0)
        ss_comp = c.get("subhiksham", 0.0)
        ss_diff = ss_comp - ss_ref
        
        sv_ref = r.get("sv", 0.0)
        sv_comp = c.get("viruksham", 0.0)
        sv_diff = sv_comp - sv_ref
        
        tot_ref = r.get("total", 0.0)
        tot_comp = c.get("total", 0.0)
        tot_diff = tot_comp - tot_ref
        
        t_ref = r.get("target")
        t_comp = c.get("target")
        
        print(f"{b:8s} | {str(t_ref):10s} / {str(t_comp):10s} | {ss_ref:10,.0f} / {ss_comp:10,.0f} / {ss_diff:+8,.0f} | {sv_ref:10,.0f} / {sv_comp:10,.0f} / {sv_diff:+8,.0f} | {tot_ref:10,.0f} / {tot_comp:10,.0f} / {tot_diff:+8,.0f}")

    comp_cnt = {r["branch"]: r for r in data[f"computed_{dt_str}"]["count"]}
    ref_cnt = data[f"ref_{dt_str}"]["count"]
    
    print("\n--- TABLE 2: COUNT COMPARISON ---")
    print(f"{'Branch':8s} | {'Target':8s} | {'SS (Ref/Comp/Diff)':22s} | {'SV (Ref/Comp/Diff)':22s} | {'DG (Ref/Comp/Diff)':22s} | {'DS (Ref/Comp/Diff)':22s} | {'Total (Ref/Comp/Diff)':24s} | {'AVG':10s}")
    print("-" * 155)
    
    for b in ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KANCHI", "NGL", "E COMM", "Total"]:
        c = comp_cnt.get(b, {})
        r = ref_cnt.get(b, {})
        
        ss_ref = r.get("ss", 0)
        ss_comp = c.get("subhiksham", 0)
        ss_diff = ss_comp - ss_ref
        
        sv_ref = r.get("sv", 0)
        sv_comp = c.get("viruksham", 0)
        sv_diff = sv_comp - sv_ref
        
        dg_ref = r.get("dg", 0)
        dg_comp = c.get("digi_gold", 0)
        dg_diff = dg_comp - dg_ref
        
        ds_ref = r.get("ds", 0)
        ds_comp = c.get("digi_silver", 0)
        ds_diff = ds_comp - ds_ref
        
        tot_ref = r.get("total", 0)
        tot_comp = c.get("total", 0)
        tot_diff = tot_comp - tot_ref
        
        avg_ref = r.get("avg", 0)
        avg_comp = c.get("avg", 0)
        
        print(f"{b:8s} | {str(r.get('target')):8s} | {ss_ref:5d} / {ss_comp:5d} / {ss_diff:+5d} | {sv_ref:5d} / {sv_comp:5d} / {sv_diff:+5d} | {dg_ref:5d} / {dg_comp:5d} / {dg_diff:+5d} | {ds_ref:5d} / {ds_comp:5d} / {ds_diff:+5d} | {tot_ref:6d} / {tot_comp:6d} / {tot_diff:+6d} | {avg_ref:4d} / {avg_comp:4d}")
