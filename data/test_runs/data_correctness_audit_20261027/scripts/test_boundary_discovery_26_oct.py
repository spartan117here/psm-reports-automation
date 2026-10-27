import os
import sys
from pathlib import Path
from datetime import date
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")

from app.google_sheets.client import GoogleApiSheetsService
from app.reporting.screenshots import detect_and_validate_enrollment_range, detect_and_validate_closing_range

sa_path = str(project_root / "secrets" / "service_account.json")
sheets_service = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)

enrollment_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID", "1Mij4hMfZN-2qM_B6E-r71gC6k0Y0EaK1yG1XJq76zCc")
closing_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID", "1i-NCumDUIfRvYTxg6ZgN9t3wcGTrte46PcvaJpw4BGg")
target_date = date(2026, 10, 26)

print("Validating Enrollment range for 2026-10-26...")
try:
    enr_b = detect_and_validate_enrollment_range(
        sheets_service, enrollment_id, worksheet_name="Consolidate Report - Oct", expected_date=target_date
    )
    print(f"Enrollment Range Validated: {enr_b.detected_range}, Date: {enr_b.report_date}, Title: {enr_b.report_title}")
except Exception as e:
    print(f"Enrollment Boundary Error: {e}")

print("\nValidating Closing range for 2026-10-26...")
try:
    cl_b = detect_and_validate_closing_range(
        sheets_service, closing_id, worksheet_name="October - 2026", expected_date=target_date
    )
    print(f"Closing Range Validated: {cl_b.detected_range}, Date: {cl_b.report_date}, Title: {cl_b.report_title}")
except Exception as e:
    print(f"Closing Boundary Error: {e}")
