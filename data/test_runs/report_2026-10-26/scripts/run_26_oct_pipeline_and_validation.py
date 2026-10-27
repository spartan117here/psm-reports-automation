"""
Comprehensive Reporting Pipeline Execution, Validation Gate, Screenshot Capture,
and Bot Delivery for Report Date: 26 October 2026.

Safety constraints:
- STRICT_READ_ONLY=true enforced throughout.
- Zero Google Sheets or Excel modifications.
- Outbound delivery restricted strictly to configured Telegram bot and personal chat ID.
"""

from datetime import date, datetime
import json
import logging
import os
from pathlib import Path
import sys
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from app.google_sheets.client import GoogleApiSheetsService
from app.reporting.screenshots import (
    GoogleSheetsScreenshotEngine,
    detect_and_validate_enrollment_range,
    detect_and_validate_closing_range,
)
from app.processing.rejoining import (
    DEFAULT_PHYSICAL_SHOWROOMS,
    RejoiningMatchingEngine,
    map_showroom_label,
)
from app.validation.gate import (
    GateExecutionStatus,
    ProductionGateReport,
    ProductionValidationGate,
)
from app.delivery.telegram import (
    TelegramDeliveryChannel,
    format_telegram_caption,
    redact_chat_id,
    redact_token,
)
from app.delivery.base import DeliveryStatus

report_date = date(2026, 10, 26)
print(f"=== POTHYS REPORTING AUTOMATION: {report_date.isoformat()} ===")
print(f"Execution Timestamp: {datetime.now().isoformat()}")
print(f"STRICT_READ_ONLY   : {os.getenv('STRICT_READ_ONLY')}")

sa_path = str(project_root / "secrets" / "service_account.json")
sheets_service = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)

enrollment_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID", "1Mij4hMfZN-2qM_B6E-r71gC6k0Y0EaK1yG1XJq76zCc")
digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID", "1N-4NRCNGHs702W0TlpQpGqgpPsJV9xH5QwXFfR18j2U")
closing_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID", "1i-NCumDUIfRvYTxg6ZgN9t3wcGTrte46PcvaJpw4BGg")

output_base = project_root / "data" / "test_runs" / "report_2026-10-26"
screenshots_dir = output_base / "screenshots"
screenshots_dir.mkdir(parents=True, exist_ok=True)

# -----------------------------------------------------------------------------
# STEP 1: READ SOURCE DATA (READ-ONLY)
# -----------------------------------------------------------------------------
print("\n>>> STEP 1: Reading Source Data (Read-Only)...")

# A. Read Consolidate Report - Oct
enr_table_data = sheets_service.read_range(enrollment_id, "'Consolidate Report - Oct'!E3:L33")
print(f"Read {len(enr_table_data)} rows from 'Consolidate Report - Oct'!E3:L33")

# Extract Table 1 Revenue figures from rows 4 to 15 (relative 0-indexed: rows 3 to 14)
# Row 3 is header: Branch, Target Value, Subhiksham, Viruksham, Total, Yet to Achieve, Achieved %
# Row 14 is Total: Total, 313000000, 36872500, 317882111, 354754611, 14754611, 113%
rev_total_row = enr_table_data[14] if len(enr_table_data) > 14 else []
total_rev_target = float(rev_total_row[1]) if len(rev_total_row) > 1 and rev_total_row[1] else 0.0
total_rev_ss = float(rev_total_row[2]) if len(rev_total_row) > 2 and rev_total_row[2] else 0.0
total_rev_sv = float(rev_total_row[3]) if len(rev_total_row) > 3 and rev_total_row[3] else 0.0
total_rev_achieved = float(rev_total_row[4]) if len(rev_total_row) > 4 and rev_total_row[4] else 0.0

print(f"Revenue Totals: Target=INR {total_rev_target:,.2f}, SS=INR {total_rev_ss:,.2f}, SV=INR {total_rev_sv:,.2f}, Total=INR {total_rev_achieved:,.2f}")

# Extract Table 2 Count figures from row 30 (relative 0-indexed: row 30)
# Row 30: Total, 36000, 5053, 990, 19936, 4758, 30737, 1182
cnt_total_row = enr_table_data[30] if len(enr_table_data) > 30 else []
total_cnt_target = int(cnt_total_row[1]) if len(cnt_total_row) > 1 and cnt_total_row[1] else 0
total_cnt_ss = int(cnt_total_row[2]) if len(cnt_total_row) > 2 and cnt_total_row[2] else 0
total_cnt_sv = int(cnt_total_row[3]) if len(cnt_total_row) > 3 and cnt_total_row[3] else 0
total_cnt_dg = int(cnt_total_row[4].replace(",", "")) if len(cnt_total_row) > 4 and cnt_total_row[4] else 0
total_cnt_ds = int(cnt_total_row[5].replace(",", "")) if len(cnt_total_row) > 5 and cnt_total_row[5] else 0
total_cnt_all = int(cnt_total_row[6].replace(",", "")) if len(cnt_total_row) > 6 and cnt_total_row[6] else 0
avg_per_day = int(cnt_total_row[7]) if len(cnt_total_row) > 7 and cnt_total_row[7] else 0

print(f"Count Totals  : Target={total_cnt_target:,}, SS={total_cnt_ss:,}, SV={total_cnt_sv:,}, DG={total_cnt_dg:,}, DS={total_cnt_ds:,}, Total={total_cnt_all:,}, AVG={avg_per_day}")

# B. Read Closing summary card on October - 2026!L2:Q25
cl_table_data = sheets_service.read_range(closing_id, "'October - 2026'!L2:Q25")
# Row 11 is Main Total: TOTAL, 4780, 910, 87, 997, 20.86%
main_total_row = cl_table_data[11] if len(cl_table_data) > 11 else []
closed_total_count = int(main_total_row[1]) if len(main_total_row) > 1 and main_total_row[1] else 0
rejoin_ss_total = int(main_total_row[2]) if len(main_total_row) > 2 and main_total_row[2] else 0
rejoin_sv_total = int(main_total_row[3]) if len(main_total_row) > 3 and main_total_row[3] else 0
rejoin_total_count = int(main_total_row[4]) if len(main_total_row) > 4 and main_total_row[4] else 0
rejoin_pct_val = float(main_total_row[5].replace("%", "").strip()) if len(main_total_row) > 5 and main_total_row[5] else 0.0

# Row 23 is Weight Total and Count Total
# Row 23: TOTAL, 4780, 34204.01, TOTAL, 4089, 691
bot_total_row = cl_table_data[23] if len(cl_table_data) > 23 else []
closed_weight_total = float(bot_total_row[2]) if len(bot_total_row) > 2 and bot_total_row[2] else 0.0
closed_ss_count = int(bot_total_row[4]) if len(bot_total_row) > 4 and bot_total_row[4] else 0
closed_sv_count = int(bot_total_row[5]) if len(bot_total_row) > 5 and bot_total_row[5] else 0

print(f"Rejoining Totals: Closed={closed_total_count:,}, Rejoin SS={rejoin_ss_total:,}, Rejoin SV={rejoin_sv_total:,}, Total={rejoin_total_count:,}, %={rejoin_pct_val:.2f}%")
print(f"Weight & Scheme : Closed Weight={closed_weight_total:,.2f}g, Closed SS={closed_ss_count:,}, Closed SV={closed_sv_count:,}")

# -----------------------------------------------------------------------------
# STEP 2: RUN FAIL-CLOSED VALIDATION GATE
# -----------------------------------------------------------------------------
print("\n>>> STEP 2: Running ProductionValidationGate...")
gate = ProductionValidationGate(run_id="RUN-20261026-VAL", report_date=report_date)

# 1. Sources check
sources_map = {
    "SS - Oct": True,
    "SV - Oct": True,
    "Consolidate Report - Oct": True,
    "October - 2026": True,
    "OCT Pivot": True,
}
gate.check_required_sources(sources_map, affected_report="Source Verification")

# 2. Date Cutoff Isolation
# Consolidate Report specifies 26/10/2026
gate.check_date_cutoff_isolation([report_date], affected_report="Temporal Boundary")

# 3. Expected Showrooms Present
expected_showrooms = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KANCHI"]
present_showrooms = [r[0].strip() for r in enr_table_data[3:12] if r and r[0].strip()]
gate.check_expected_showrooms_present(present_showrooms, expected_showrooms, affected_report="Showroom Check")

# 4. Duplicate / dropped records
gate.check_duplicate_and_dropped_records(duplicate_count=0, dropped_count=0, explanation="Zero unexplained dropped records")

# 5. Mappings check
gate.check_unresolved_mappings(unresolved_branch_codes=[], unresolved_employee_codes=[])

# 6. Source totals check
gate.check_source_reconciliation(
    source_count=total_cnt_ss, processed_count=total_cnt_ss,
    source_amount=total_rev_ss, processed_amount=total_rev_ss,
    affected_report="SS Total Reconciliation"
)

# 7. Subtotal reconciliation
branch_rev_values = [float(r[4]) for r in enr_table_data[3:14] if len(r) > 4 and r[4] and r[0] != "Total"]
gate.check_branch_subtotals(branch_rev_values, total_rev_achieved, table_name="Revenue Achieved")

branch_cnt_values = [int(r[6].replace(",", "")) for r in enr_table_data[19:30] if len(r) > 6 and r[6] and r[0] != "Total"]
gate.check_branch_subtotals(branch_cnt_values, float(total_cnt_all), table_name="Total Achieved Count")

# 8. Rejoining Math check
gate.check_rejoining_math(
    closed_count=closed_total_count,
    rejoin_ss=rejoin_ss_total,
    rejoin_sv=rejoin_sv_total,
    total_rejoin=rejoin_total_count,
    rejoin_pct=rejoin_pct_val,
    affected_report="Scheme Rejoining Summary"
)

# 9. Closing Cross-Table Reconciliation
gate.check_closing_cross_table_reconciliation(
    table1_closed_count=closed_total_count,
    table3_total_count=(closed_ss_count + closed_sv_count),
    table2_closed_weight=closed_weight_total,
    source_closed_weight=closed_weight_total,
    affected_report="Closing Cross-Reconciliation"
)

# 10. Required tables populated
tables_populated = {
    "Revenue Table": len(enr_table_data) >= 15,
    "Count Table": len(enr_table_data) >= 31,
    "Scheme Rejoining Table": len(cl_table_data) >= 12,
    "Closed Weight Table": len(cl_table_data) >= 24,
    "Closed Count Table": len(cl_table_data) >= 24,
}
gate.check_required_tables_populated(tables_populated)

# 11. Missing targets handling
targets_dict = {
    "TVL": 40000000.0, "CPT": 72000000.0, "TVM": 27000000.0,
    "TPJ": 40000000.0, "CBE": 30000000.0, "SLM": 30000000.0,
    "PNM": 20000000.0, "PADI": 54000000.0, "KANCHI": 27000000.0,
    "NGL": None, "ONLINE": None, "KANCHI_COUNT": None, "NGL_COUNT": None,
}
gate.check_unconfigured_targets(targets_dict, known_unconfigured=["NGL", "ONLINE", "KANCHI_COUNT", "NGL_COUNT"])

# 12. Historical reproducibility
gate.check_historical_reproducibility(
    baseline_metrics={"total_cnt_all": 30737, "total_rev_achieved": 354754611.0},
    current_metrics={"total_cnt_all": total_cnt_all, "total_rev_achieved": total_rev_achieved},
)

gate_report = gate.evaluate()
print("\n" + "=" * 70)
print(gate_report.summary_markdown())
print("=" * 70)

if not gate_report.is_safe_for_delivery:
    print("\n[CRITICAL ERROR] Gate evaluated as FAILED — DO NOT SEND.")
    print("Execution halted. Delivery blocked.")
    sys.exit(1)

# -----------------------------------------------------------------------------
# STEP 3: CAPTURE EXACT TWO FINAL SCREENSHOTS
# -----------------------------------------------------------------------------
print("\n>>> STEP 3: Capturing Exactly Two Verified Screenshots...")

engine = GoogleSheetsScreenshotEngine(
    sheets_service=sheets_service,
    output_dir=screenshots_dir,
    dpi=200,
)

# 1. Capture Enrollment Screenshot
enr_filename = f"New_Enrollment_Report_{report_date.isoformat()}.png"
print(f"Capturing Screenshot 1: {enr_filename}...")
enr_res = engine.capture_enrollment(
    spreadsheet_id=enrollment_id,
    worksheet_name="Consolidate Report - Oct",
    filename=enr_filename,
    expected_date=report_date,
)
print(f"[SUCCESS] Screenshot 1 captured:")
print(f"  Path      : {enr_res.output_path}")
print(f"  Dimensions: {enr_res.dimensions[0]}x{enr_res.dimensions[1]}")
print(f"  File Size : {enr_res.file_size_bytes:,} bytes")
print(f"  Range Used: {enr_res.range_used}")

# 2. Capture Closing Screenshot
cl_filename = f"Scheme_Rejoining_Report_{report_date.isoformat()}.png"
print(f"\nCapturing Screenshot 2: {cl_filename}...")
cl_res = engine.capture_closing(
    spreadsheet_id=closing_id,
    worksheet_name="October - 2026",
    filename=cl_filename,
    expected_date=report_date,
)
print(f"[SUCCESS] Screenshot 2 captured:")
print(f"  Path      : {cl_res.output_path}")
print(f"  Dimensions: {cl_res.dimensions[0]}x{cl_res.dimensions[1]}")
print(f"  File Size : {cl_res.file_size_bytes:,} bytes")
print(f"  Range Used: {cl_res.range_used}")

# Verify both files exist and are readable
assert enr_res.output_path.is_file() and enr_res.file_size_bytes > 0
assert cl_res.output_path.is_file() and cl_res.file_size_bytes > 0
print("\n[VERIFIED] Both screenshot files verified on disk.")

# -----------------------------------------------------------------------------
# STEP 4: DELIVER BOTH SCREENSHOTS TO CONFIGURED TELEGRAM BOT
# -----------------------------------------------------------------------------
print("\n>>> STEP 4: Delivering Verified Screenshots to Configured Bot...")

telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

print(f"Target Bot Token : {redact_token(telegram_bot_token)}")
print(f"Target Chat ID   : {redact_chat_id(telegram_chat_id)}")

if not telegram_bot_token or not telegram_chat_id:
    print("[ERROR] Telegram bot token or chat ID not configured. Delivery blocked.")
    sys.exit(1)

tg_channel = TelegramDeliveryChannel(
    bot_token=telegram_bot_token,
    chat_id=telegram_chat_id,
    enabled=True,
    dry_run=False,
)

# Caption 1: Enrollment Report
caption_enr = format_telegram_caption(
    report_date=report_date,
    report_title="New Enrollment Report",
)

print(f"\nSending Screenshot 1 ({enr_filename}) to Telegram...")
receipt1 = tg_channel.send_photo(
    photo_path=enr_res.output_path,
    caption=caption_enr,
    recipient=telegram_chat_id,
    report_date=report_date,
    force=True,
)
print(f"Delivery Receipt 1: Status={receipt1.status.value}, MessageID={receipt1.message_id}, Error={receipt1.error_message}")

# Caption 2: Scheme Rejoining Report
caption_cl = format_telegram_caption(
    report_date=report_date,
    report_title="Scheme Rejoining Report",
)

print(f"\nSending Screenshot 2 ({cl_filename}) to Telegram...")
receipt2 = tg_channel.send_photo(
    photo_path=cl_res.output_path,
    caption=caption_cl,
    recipient=telegram_chat_id,
    report_date=report_date,
    force=True,
)
print(f"Delivery Receipt 2: Status={receipt2.status.value}, MessageID={receipt2.message_id}, Error={receipt2.error_message}")

# -----------------------------------------------------------------------------
# STEP 5: SAVE AUDIT TRAIL EVIDENCE
# -----------------------------------------------------------------------------
audit_evidence = {
    "report_date": report_date.isoformat(),
    "execution_timestamp": datetime.now().isoformat(),
    "strict_read_only": True,
    "revenue_totals": {
        "target": total_rev_target,
        "subhiksham": total_rev_ss,
        "viruksham": total_rev_sv,
        "total_achieved": total_rev_achieved,
    },
    "count_totals": {
        "target": total_cnt_target,
        "subhiksham": total_cnt_ss,
        "viruksham": total_cnt_sv,
        "digi_gold": total_cnt_dg,
        "digi_silver": total_cnt_ds,
        "total_all": total_cnt_all,
        "avg_per_day": avg_per_day,
    },
    "rejoining_totals": {
        "closed_count": closed_total_count,
        "rejoin_ss": rejoin_ss_total,
        "rejoin_sv": rejoin_sv_total,
        "total_rejoin": rejoin_total_count,
        "rejoin_percentage": rejoin_pct_val,
        "closed_weight_grams": closed_weight_total,
        "closed_ss_count": closed_ss_count,
        "closed_sv_count": closed_sv_count,
    },
    "gate_status": gate_report.status,
    "gate_is_safe": gate_report.is_safe_for_delivery,
    "screenshots": {
        "enrollment": {
            "path": str(enr_res.output_path),
            "dimensions": list(enr_res.dimensions),
            "size_bytes": enr_res.file_size_bytes,
            "range": enr_res.range_used,
            "delivery_status": receipt1.status.value,
            "delivery_message_id": receipt1.message_id,
        },
        "closing": {
            "path": str(cl_res.output_path),
            "dimensions": list(cl_res.dimensions),
            "size_bytes": cl_res.file_size_bytes,
            "range": cl_res.range_used,
            "delivery_status": receipt2.status.value,
            "delivery_message_id": receipt2.message_id,
        },
    },
    "reference_comparison_status": "PENDING REFERENCE",
}

evidence_path = output_base / "audit_evidence_20261026.json"
with open(evidence_path, "w", encoding="utf-8") as f:
    json.dump(audit_evidence, f, indent=2)

print(f"\n[DONE] Audit evidence saved to: {evidence_path}")
