import os
import sys
import json
from pathlib import Path
from collections import Counter, defaultdict
from datetime import date, datetime

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
from app.reporting.digi import (
    DigiSourceReader,
    evaluate_digi_feed_readiness,
    DIGI_BRANCH_ORDER,
)
from app.validation.gate import ProductionValidationGate

sa_path = str(project_root / "secrets" / "service_account.json")
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")

TARGET_TARGETS = {
    "TVL": {"rev": 40000000.0, "cnt": 3000},
    "CPT": {"rev": 72000000.0, "cnt": 6000},
    "TVM": {"rev": 27000000.0, "cnt": 4000},
    "TPJ": {"rev": 40000000.0, "cnt": 5000},
    "CBE": {"rev": 30000000.0, "cnt": 7000},
    "SLM": {"rev": 30000000.0, "cnt": 3000},
    "PNM": {"rev": 20000000.0, "cnt": 2000},
    "PADI": {"rev": 54000000.0, "cnt": 6000},
    "KANCHI": {"rev": 27000000.0, "cnt": None},
    "NGL": {"rev": None, "cnt": None},
    "ONLINE": {"rev": None, "cnt": None},
    "E COMM": {"rev": None, "cnt": None},
}

# Approved total targets
TOTAL_REV_TARGET = 313000000.0
TOTAL_CNT_TARGET = 36000

# Mapping of LOCATION 2 to canonical branch
LOCATION_MAPPING = {
    "TVL": {
        "ss": ["TIRUNELVELI", "TIRUNELVELI ECOMM"],
        "sv": ["TIRUNELVELI SV", "TIRUNELVELI SV ECOMM"],
        "dg": ["NELLAI"],
        "ds": ["NELLAI"],
    },
    "CPT": {
        "ss": ["CHROMEPET", "CHROMEPET ECOMM"],
        "sv": ["CHROMEPET SV", "CHROMEPET SV ECOMM"],
        "dg": ["CHROMEPET", "POTHYS CHROMPET"],
        "ds": ["CHROMEPET"],
    },
    "TVM": {
        "ss": ["TRIVANDRUM", "TRIVANDRUM ECOMM"],
        "sv": ["TRIVANDRUM SV", "TRIVANDRUM SV ECOMM"],
        "dg": ["TRIVANDRUM"],
        "ds": ["TRIVANDRUM"],
    },
    "TPJ": {
        "ss": ["TRICHY", "TRICHY ECOMM"],
        "sv": ["TRICHY SV", "TRICHY SV ECOMM"],
        "dg": ["TRICHY", "POTHYS TRICHY"],
        "ds": ["TRICHY"],
    },
    "CBE": {
        "ss": ["COIMBATORE", "COIMBATORE ECOMM"],
        "sv": ["COIMBATORE SV", "COIMBATORE SV ECOMM"],
        "dg": ["COIMBATORE"],
        "ds": ["COIMBATORE"],
    },
    "SLM": {
        "ss": ["SALEM", "SALEM ECOMM"],
        "sv": ["SALEM SV", "SALEM SV ECOMM"],
        "dg": ["SALEM"],
        "ds": ["SALEM"],
    },
    "PNM": {
        "ss": ["POONAMALLEE", "POONAMALLEE ECOMM"],
        "sv": ["POONAMALLEE SV", "POONAMALLEE SV ECOMM"],
        "dg": ["POONAMALLEE"],
        "ds": ["POONAMALLEE"],
    },
    "PADI": {
        "ss": ["PADI", "PADI ECOMM"],
        "sv": ["PADI SV", "PADI SV ECOMM"],
        "dg": ["PADI", "PADI TEXTILES"],
        "ds": ["PADI", "PADI TEXTILES"],
    },
    "KANCHI": {
        "ss": ["KANCHEEPURAM", "KANCHEEPURAM ECOMM"],
        "sv": ["KANCHEEPURAM SV", "KANCHEEPURAM SV ECOMM"],
        "dg": ["KANCHEEPURAM"],
        "ds": ["KANCHEEPURAM"],
    },
    "NGL": {
        "ss": ["NAGARKOVIL", "NAGARKOVIL ECOMM"],
        "sv": ["NAGARKOVIL SV", "NAGARKOVIL SV ECOMM"],
        "dg": ["NAGERCOIL"],
        "ds": ["NAGERCOIL"],
    },
    "ONLINE": {
        "ss": ["ONLINE", "CORPORATE OFFICE"],
        "sv": ["ONLINE SV"],
        "dg": ["#N/A", "CORPORATE OFFICE", "BOUTIQUE", "POTHYS HYPER", "POTHYS TRADITIONAL"],
        "ds": ["#N/A", "CORPORATE OFFICE", "POTHYS TRADITIONAL"],
    },
}

# Reference screenshot values for 26-Oct-2026:
REF_26 = {
    "revenue": {
        "TVL": {"target": 40000000.0, "ss": 3843500.0, "sv": 44485047.0, "total": 48328547.0, "yet": 8328547.0, "ach_pct": 121},
        "CPT": {"target": 72000000.0, "ss": 9663500.0, "sv": 76668995.0, "total": 86332495.0, "yet": 14332495.0, "ach_pct": 120},
        "TVM": {"target": 27000000.0, "ss": 2082500.0, "sv": 22449742.0, "total": 24532242.0, "yet": -2467758.0, "ach_pct": 91},
        "TPJ": {"target": 40000000.0, "ss": 2719000.0, "sv": 34892821.0, "total": 37611821.0, "yet": -2388179.0, "ach_pct": 94},
        "CBE": {"target": 30000000.0, "ss": 2204000.0, "sv": 22366809.0, "total": 24570809.0, "yet": -5429191.0, "ach_pct": 82},
        "SLM": {"target": 30000000.0, "ss": 1437500.0, "sv": 28840681.0, "total": 30278181.0, "yet": 278181.0, "ach_pct": 101},
        "PNM": {"target": 20000000.0, "ss": 2423500.0, "sv": 14157021.0, "total": 16580521.0, "yet": -3419479.0, "ach_pct": 83},
        "PADI": {"target": 54000000.0, "ss": 6946000.0, "sv": 53272381.0, "total": 60218381.0, "yet": 6218381.0, "ach_pct": 112},
        "KANCHI": {"target": 27000000.0, "ss": 2805000.0, "sv": 11056518.0, "total": 13861518.0, "yet": -13138482.0, "ach_pct": 51},
        "NGL": {"target": None, "ss": 387000.0, "sv": 2375412.0, "total": 2762412.0, "yet": 2762412.0, "ach_pct": None},
        "ONLINE": {"target": None, "ss": 2361000.0, "sv": 7316684.0, "total": 9677684.0, "yet": 9677684.0, "ach_pct": None},
        "Total": {"target": 313000000.0, "ss": 36872500.0, "sv": 317882111.0, "total": 354754611.0, "yet": 14754611.0, "ach_pct": 113},
    },
    "count": {
        "TVL": {"target": 3000, "ss": 559, "sv": 119, "dg": 824, "ds": 385, "total": 1887, "avg": 73},
        "CPT": {"target": 6000, "ss": 973, "sv": 241, "dg": 1985, "ds": 373, "total": 3572, "avg": 137},
        "TVM": {"target": 4000, "ss": 287, "sv": 42, "dg": 1863, "ds": 46, "total": 2238, "avg": 86},
        "TPJ": {"target": 5000, "ss": 551, "sv": 90, "dg": 1876, "ds": 685, "total": 3202, "avg": 123},
        "CBE": {"target": 7000, "ss": 425, "sv": 75, "dg": 2070, "ds": 961, "total": 3531, "avg": 136},
        "SLM": {"target": 3000, "ss": 268, "sv": 89, "dg": 1280, "ds": 452, "total": 2089, "avg": 80},
        "PNM": {"target": 2000, "ss": 256, "sv": 43, "dg": 464, "ds": 155, "total": 918, "avg": 35},
        "PADI": {"target": 6000, "ss": 805, "sv": 175, "dg": 1506, "ds": 357, "total": 2843, "avg": 109},
        "KANCHI": {"target": None, "ss": 496, "sv": 44, "dg": 2746, "ds": 378, "total": 3664, "avg": 141},
        "NGL": {"target": None, "ss": 58, "sv": 8, "dg": 143, "ds": 3, "total": 212, "avg": 8},
        "E COMM": {"target": None, "ss": 375, "sv": 64, "dg": 5179, "ds": 963, "total": 6581, "avg": 253},
        "Total": {"target": 36000, "ss": 5053, "sv": 990, "dg": 19936, "ds": 4758, "total": 30737, "avg": 1182},
    }
}

# Reference screenshot values for 27-Oct-2026:
REF_27 = {
    "revenue": {
        "TVL": {"target": 40000000.0, "ss": 4034000.0, "sv": 46574744.0, "total": 50608744.0, "yet": 10608744.0, "ach_pct": 127},
        "CPT": {"target": 72000000.0, "ss": 9999000.0, "sv": 79815518.0, "total": 89814518.0, "yet": 17814518.0, "ach_pct": 125},
        "TVM": {"target": 27000000.0, "ss": 2117500.0, "sv": 22449742.0, "total": 24567242.0, "yet": -2432758.0, "ach_pct": 91},
        "TPJ": {"target": 40000000.0, "ss": 2758500.0, "sv": 38392966.0, "total": 41151466.0, "yet": 1151466.0, "ach_pct": 103},
        "CBE": {"target": 30000000.0, "ss": 2268500.0, "sv": 22421809.0, "total": 24690309.0, "yet": -5309691.0, "ach_pct": 82},
        "SLM": {"target": 30000000.0, "ss": 1462500.0, "sv": 29050841.0, "total": 30513341.0, "yet": 513341.0, "ach_pct": 102},
        "PNM": {"target": 20000000.0, "ss": 2483500.0, "sv": 15167715.0, "total": 17651215.0, "yet": -2348785.0, "ach_pct": 88},
        "PADI": {"target": 54000000.0, "ss": 7102500.0, "sv": 55229336.0, "total": 62331836.0, "yet": 8331836.0, "ach_pct": 115},
        "KANCHI": {"target": 27000000.0, "ss": 2828000.0, "sv": 12360318.0, "total": 15188318.0, "yet": -11811682.0, "ach_pct": 56},
        "NGL": {"target": None, "ss": 487000.0, "sv": 3275413.0, "total": 3762413.0, "yet": 3762413.0, "ach_pct": None},
        "ONLINE": {"target": None, "ss": 2478500.0, "sv": 8510424.0, "total": 10988924.0, "yet": 10988924.0, "ach_pct": None},
        "Total": {"target": 313000000.0, "ss": 38019500.0, "sv": 333248826.0, "total": 371268326.0, "yet": 31268326.0, "ach_pct": 119},
    },
    "count": {
        "TVL": {"target": 3000, "ss": 577, "sv": 127, "dg": 856, "ds": 408, "total": 1968, "avg": 73},
        "CPT": {"target": 6000, "ss": 1006, "sv": 253, "dg": 2201, "ds": 435, "total": 3895, "avg": 144},
        "TVM": {"target": 4000, "ss": 296, "sv": 42, "dg": 1956, "ds": 48, "total": 2342, "avg": 87},
        "TPJ": {"target": 5000, "ss": 567, "sv": 93, "dg": 1989, "ds": 699, "total": 3348, "avg": 124},
        "CBE": {"target": 7000, "ss": 438, "sv": 76, "dg": 2172, "ds": 965, "total": 3651, "avg": 135},
        "SLM": {"target": 3000, "ss": 278, "sv": 90, "dg": 1391, "ds": 488, "total": 2247, "avg": 83},
        "PNM": {"target": 2000, "ss": 266, "sv": 46, "dg": 477, "ds": 160, "total": 949, "avg": 35},
        "PADI": {"target": 6000, "ss": 830, "sv": 183, "dg": 1560, "ds": 366, "total": 2939, "avg": 109},
        "KANCHI": {"target": None, "ss": 502, "sv": 46, "dg": 2471, "ds": 296, "total": 3315, "avg": 123},
        "NGL": {"target": None, "ss": 68, "sv": 9, "dg": 190, "ds": 6, "total": 273, "avg": 10},
        "E COMM": {"target": None, "ss": 387, "sv": 72, "dg": 5283, "ds": 1017, "total": 6759, "avg": 250},
        "Total": {"target": 36000, "ss": 5215, "sv": 1037, "dg": 20546, "ds": 4888, "total": 31686, "avg": 1174},
    }
}

print("=== STARTING TWO-DATE SOURCE-TO-REPORT AUDIT ===")
# Fetch all raw data once in memory
print("Fetching 'SS - Oct'...")
raw_ss_rows = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
print(f"Total SS rows: {len(raw_ss_rows)}")

print("Fetching 'SV - Oct'...")
raw_sv_rows = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
print(f"Total SV rows: {len(raw_sv_rows)}")

print("Fetching 'OCT' Digi raw data...")
raw_digi_rows = sheets.read_range(digi_id, "'OCT'!A2:R")
print(f"Total Digi rows: {len(raw_digi_rows)}")

print("Fetching 'October - 2026' Closed Rejoining data...")
raw_close_rows = sheets.read_range(close_id, "'October - 2026'!A2:H")
print(f"Total Closing rows: {len(raw_close_rows)}")

def compute_date_results(report_date, day_num):
    print(f"\n--- COMPUTING RESULTS FOR {report_date} (Day {day_num}) ---")
    
    # 1. SS Data
    ss_by_loc = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for r in raw_ss_rows:
        dt = parse_date_safe(r[5] if len(r) > 5 else "")
        if dt and dt <= report_date:
            loc = (r[6] if len(r) > 6 else "").strip().upper()
            amt_str = (r[8] if len(r) > 8 else "0").replace(",", "").strip()
            amt = float(amt_str) if amt_str else 0.0
            ss_by_loc[loc]["count"] += 1
            ss_by_loc[loc]["amount"] += amt

    # 2. SV Data
    sv_by_loc = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for r in raw_sv_rows:
        dt = parse_date_safe(r[5] if len(r) > 5 else "")
        if dt and dt <= report_date:
            loc = (r[6] if len(r) > 6 else "").strip().upper()
            amt_str = (r[8] if len(r) > 8 else "0").replace(",", "").strip()
            amt = float(amt_str) if amt_str else 0.0
            sv_by_loc[loc]["count"] += 1
            sv_by_loc[loc]["amount"] += amt

    # 3. Digi Data (evaluated Column R)
    dg_by_branch = Counter()
    ds_by_branch = Counter()
    for r in raw_digi_rows:
        dt = parse_date_safe(r[5] if len(r) > 5 else "")
        if dt and dt <= report_date:
            sch = (r[9] if len(r) > 9 else "").strip().upper()
            branch = (r[17] if len(r) > 17 else "").strip().upper()
            if sch == "DIGI GOLD":
                dg_by_branch[branch] += 1
            elif sch == "DIGI SILVER":
                ds_by_branch[branch] += 1

    # Now aggregate by report branches
    branches_order = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KANCHI", "NGL", "ONLINE"]
    
    table1 = []
    table2 = []
    
    tot_ss_rev = 0.0
    tot_sv_rev = 0.0
    tot_ss_cnt = 0
    tot_sv_cnt = 0
    tot_dg_cnt = 0
    tot_ds_cnt = 0
    
    for b in branches_order:
        cfg = LOCATION_MAPPING[b]
        
        # SS revenue and count
        ss_amt = sum(ss_by_loc[loc]["amount"] for loc in cfg["ss"])
        ss_cnt = sum(ss_by_loc[loc]["count"] for loc in cfg["ss"])
        
        # SV revenue and count
        sv_amt = sum(sv_by_loc[loc]["amount"] for loc in cfg["sv"])
        sv_cnt = sum(sv_by_loc[loc]["count"] for loc in cfg["sv"])
        
        # DG & DS count
        dg_cnt = sum(dg_by_branch[loc] for loc in cfg["dg"])
        ds_cnt = sum(ds_by_branch[loc] for loc in cfg["ds"])
        
        # Totals
        b_rev = ss_amt + sv_amt
        b_cnt = ss_cnt + sv_cnt + dg_cnt + ds_cnt
        
        tot_ss_rev += ss_amt
        tot_sv_rev += sv_amt
        tot_ss_cnt += ss_cnt
        tot_sv_cnt += sv_cnt
        tot_dg_cnt += dg_cnt
        tot_ds_cnt += ds_cnt
        
        t_rev = TARGET_TARGETS[b]["rev"]
        t_cnt = TARGET_TARGETS[b]["cnt"]
        
        # yet to achieve & achieved %
        if t_rev is not None:
            yet_rev = b_rev - t_rev
            ach_pct_rev = round((b_rev / t_rev) * 100)
        else:
            yet_rev = b_rev
            ach_pct_rev = None
            
        avg_cnt = round(b_cnt / day_num)
        
        table1.append({
            "branch": b,
            "target": t_rev,
            "subhiksham": ss_amt,
            "viruksham": sv_amt,
            "total": b_rev,
            "yet_to_achieve": yet_rev,
            "achieved_pct": ach_pct_rev,
        })
        
        table2.append({
            "branch": "E COMM" if b == "ONLINE" else b,
            "target": t_cnt,
            "subhiksham": ss_cnt,
            "viruksham": sv_cnt,
            "digi_gold": dg_cnt,
            "digi_silver": ds_cnt,
            "total": b_cnt,
            "avg": avg_cnt,
        })
        
    tot_rev = tot_ss_rev + tot_sv_rev
    tot_cnt = tot_ss_cnt + tot_sv_cnt + tot_dg_cnt + tot_ds_cnt
    tot_yet_rev = tot_rev - TOTAL_REV_TARGET
    tot_ach_pct_rev = round((tot_rev / TOTAL_REV_TARGET) * 100)
    tot_avg_cnt = round(tot_cnt / day_num)
    
    table1.append({
        "branch": "Total",
        "target": TOTAL_REV_TARGET,
        "subhiksham": tot_ss_rev,
        "viruksham": tot_sv_rev,
        "total": tot_rev,
        "yet_to_achieve": tot_yet_rev,
        "achieved_pct": tot_ach_pct_rev,
    })
    
    table2.append({
        "branch": "Total",
        "target": TOTAL_CNT_TARGET,
        "subhiksham": tot_ss_cnt,
        "viruksham": tot_sv_cnt,
        "digi_gold": tot_dg_cnt,
        "digi_silver": tot_ds_cnt,
        "total": tot_cnt,
        "avg": tot_avg_cnt,
    })
    
    return table1, table2

table1_26, table2_26 = compute_date_results(date(2026, 10, 26), 26)
table1_27, table2_27 = compute_date_results(date(2026, 10, 27), 27)

# Save results for comparison
out_data = {
    "computed_26": {"revenue": table1_26, "count": table2_26},
    "computed_27": {"revenue": table1_27, "count": table2_27},
    "ref_26": REF_26,
    "ref_27": REF_27,
}

out_path = project_root / "data" / "test_runs" / "validation_20261027" / "two_date_audit_results.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out_data, f, indent=2)

print(f"\nAudit results written to {out_path}")
