import os
import sys
from pathlib import Path
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.reporting.digi import DigiSourceReader, evaluate_digi_feed_readiness, DIGI_BRANCH_ORDER
from datetime import date

sheets = GoogleApiSheetsService(service_account_path=str(project_root / "secrets" / "service_account.json"), read_only=True)
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID")
TARGET_DATE = date(2026, 10, 28)

reader = DigiSourceReader(sheets, spreadsheet_id=digi_id)
max_schdate, max_count, rep_count = reader.validate_freshness(TARGET_DATE, "OCT")
pivot_extr = reader.extract_pivot_data("OCT Pivot")

print("=== DIGI READINESS EVALUATION FOR 28/10/2026 ===")
print("Gold Values extracted from Pivot:")
for b, v in pivot_extr.gold_values.items():
    print(f"  {b:20s}: {v}")

print("\nSilver Values extracted from Pivot:")
for b, v in pivot_extr.silver_values.items():
    print(f"  {b:20s}: {v}")

print(f"\nGold Showroom Subtotal: {pivot_extr.gold_showroom_subtotal}")
print(f"Silver Showroom Subtotal: {pivot_extr.silver_showroom_subtotal}")

# Evaluate readiness
gold_feed = evaluate_digi_feed_readiness(
    report_date=TARGET_DATE,
    scheme="DigiGold",
    raw_date=max_schdate,
    pivot_date=pivot_extr.pivot_date_header,
    branch_values=pivot_extr.gold_values,
    branch_subtotal=pivot_extr.gold_showroom_subtotal,
    grand_total=None,
    expected_branches=DIGI_BRANCH_ORDER,
)

silver_feed = evaluate_digi_feed_readiness(
    report_date=TARGET_DATE,
    scheme="DigiSilver",
    raw_date=max_schdate,
    pivot_date=pivot_extr.pivot_date_header,
    branch_values=pivot_extr.silver_values,
    branch_subtotal=pivot_extr.silver_showroom_subtotal,
    grand_total=None,
    expected_branches=DIGI_BRANCH_ORDER,
)

print(f"\nGold Feed is_valid: {gold_feed.is_valid}, error: {gold_feed.error_message}")
print(f"Silver Feed is_valid: {silver_feed.is_valid}, error: {silver_feed.error_message}")

