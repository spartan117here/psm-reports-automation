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

sa_path = str(project_root / "secrets" / "service_account.json")
sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")

# Reference screenshot (26-Oct) values:
# From reference_vs_actual_comparison.json / Reference 1 screenshot:
ref_26 = {
    "TVL": {"DG": 824, "DS": 394},
    "CPT": {"DG": 2187, "DS": 427},
    "TVM": {"DG": 1863, "DS": 46},
    "TPJ": {"DG": 1876, "DS": 685},
    "CBE": {"DG": 2045, "DS": 957},
    "SLM": {"DG": 1280, "DS": 452},
    "PNM": {"DG": 464, "DS": 155},
    "PADI": {"DG": 1506, "DS": 357},
    "KANCHI": {"DG": 2746, "DS": 378},
    "NGL": {"DG": 143, "DS": 3},
    "E COMM": {"DG": 5179, "DS": 978},
}

# Now read OCT Pivot rows
# Row 55: headers (dates)
headers = sheets.read_range(digi_id, "'OCT Pivot'!B55:AH55")[0]
# dates are columns 1..28 (2026-10-01 to 2026-10-28)
# find index for 2026-10-26 and 2026-10-27
idx_26 = headers.index("2026-10-26")
idx_27 = headers.index("2026-10-27")

print(f"Header 2026-10-26 index: {idx_26}, 2026-10-27 index: {idx_27}")

# Gold rows: 56 to 73
gold_rows = sheets.read_range(digi_id, "'OCT Pivot'!B56:AH73")
gold_pivot = {}
for r in gold_rows:
    branch = r[0].strip().upper()
    # sum values from col 1 to idx_26 inclusive
    val_26 = sum(int(x) for x in r[1:idx_26+1] if x.strip().isdigit())
    val_27 = sum(int(x) for x in r[1:idx_27+1] if x.strip().isdigit())
    gold_pivot[branch] = {"26": val_26, "27": val_27}

# Silver rows: 79 to 92
silver_rows = sheets.read_range(digi_id, "'OCT Pivot'!B79:AH92")
silver_pivot = {}
for r in silver_rows:
    branch = r[0].strip().upper()
    val_26 = sum(int(x) for x in r[1:idx_26+1] if x.strip().isdigit())
    val_27 = sum(int(x) for x in r[1:idx_27+1] if x.strip().isdigit())
    silver_pivot[branch] = {"26": val_26, "27": val_27}

print("\n=== COMPARISON FOR 26-OCTOBER: REFERENCE SCREENSHOT vs OCT PIVOT <= 26-OCT ===")
print(f"{'Branch':15s} | {'Ref DG 26':10s} | {'Piv DG 26':10s} | {'Diff DG':10s} | {'Ref DS 26':10s} | {'Piv DS 26':10s} | {'Diff DS':10s}")
print("-" * 85)

# Branch mapping between report names and pivot names:
# TVL -> NELLAI
# CPT -> CHROMEPET (+ POTHYS CHROMPET?)
# TVM -> TRIVANDRUM
# TPJ -> TRICHY (+ POTHYS TRICHY?)
# CBE -> COIMBATORE
# SLM -> SALEM
# PNM -> POONAMALLEE
# PADI -> PADI (+ PADI TEXTILES?)
# KANCHI -> KANCHEEPURAM
# NGL -> NAGERCOIL
# E COMM -> #N/A + CORPORATE OFFICE + BOUTIQUE + POTHYS HYPER + POTHYS TRADITIONAL

for b, p in gold_pivot.items():
    print(f"Pivot Branch: {b:22s} | DG <=26: {p['26']:5d} | DG <=27: {p['27']:5d} | DS <=26: {silver_pivot.get(b, {}).get('26', 0):5d} | DS <=27: {silver_pivot.get(b, {}).get('27', 0):5d}")
