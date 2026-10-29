import os
import sys
from pathlib import Path
from collections import Counter

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

print("=== AUDITING FORMULA IN OCT!R2:R27000 ===")
# Fetch formulas in batches of 5000
res = sheets.service.spreadsheets().values().get(
    spreadsheetId=digi_id,
    range="OCT!R2:R26812",
    valueRenderOption="FORMULA"
).execute()

formulas = res.get("values", [])
print(f"Total rows retrieved: {len(formulas)}")

# Analyze patterns
formula_patterns = Counter()
transitions = []
prev_pattern = None

for idx, f in enumerate(formulas, start=2):
    formula_str = f[0] if f else ""
    # Simplify formula pattern (e.g. 'New Employee'!B:F vs 'New Employee'!P:T vs 'Employee - New'!B:F)
    if "'New Employee'!B:F" in formula_str:
        pattern = "'New Employee'!B:F"
    elif "'New Employee'!P:T" in formula_str:
        pattern = "'New Employee'!P:T"
    elif "'Employee - New'!B:F" in formula_str:
        pattern = "'Employee - New'!B:F"
    elif "'Employee'!B:F" in formula_str:
        pattern = "'Employee'!B:F"
    elif formula_str == "":
        pattern = "EMPTY"
    else:
        pattern = formula_str[:30]
    
    formula_patterns[pattern] += 1
    if pattern != prev_pattern:
        transitions.append((idx, pattern))
        prev_pattern = pattern

print("\n--- FORMULA COUNTS ---")
for p, count in formula_patterns.items():
    print(f"  {p}: {count} rows")

print("\n--- FORMULA TRANSITIONS (Row Ranges) ---")
for idx, p in transitions:
    print(f"  Row {idx}: starts {p}")
