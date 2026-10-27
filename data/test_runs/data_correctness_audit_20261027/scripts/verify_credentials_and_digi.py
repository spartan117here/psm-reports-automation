"""
Credential verification and DG/DS access test.
Safety: STRICT_READ_ONLY=true, zero writes, no secret leakage.
"""

import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Enforce STRICT_READ_ONLY
os.environ["STRICT_READ_ONLY"] = "true"

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PROJECT_ROOT / "scripts" / ".env")

from app.google_sheets.client import GoogleApiSheetsService
from app.reporting.digi import DigiSourceReader, DEFAULT_DIGI_SPREADSHEET_ID

def mask_email(email: str) -> str:
    if "@" in email:
        user, domain = email.split("@", 1)
        masked_user = user[:2] + "***" if len(user) > 2 else "***"
        return f"{masked_user}@{domain}"
    return "***"

def verify_credentials():
    print("=" * 70)
    print("STEP 1: VERIFYING SERVICE ACCOUNT CREDENTIALS")
    print("=" * 70)

    sa_path = PROJECT_ROOT / "secrets" / "service_account.json"
    if not sa_path.exists():
        print(f"FAILED: Service account file does not exist at {sa_path}")
        return False, None

    print(f"PASS: File exists at {sa_path}")

    # Parse JSON structure without printing secrets
    try:
        with open(sa_path, "r", encoding="utf-8") as f:
            sa_data = json.load(f)
    except Exception as e:
        print(f"FAILED: Could not parse JSON from {sa_path}: {e}")
        return False, None

    required_keys = ["type", "project_id", "private_key_id", "private_key", "client_email", "token_uri"]
    missing = [k for k in required_keys if k not in sa_data]
    if missing:
        print(f"FAILED: Service account JSON is missing required keys: {missing}")
        return False, None

    if sa_data.get("type") != "service_account":
        print(f"FAILED: JSON 'type' is not 'service_account', got '{sa_data.get('type')}'")
        return False, None

    proj_id = sa_data.get("project_id", "")
    client_email = sa_data.get("client_email", "")
    print(f"PASS: Valid service-account JSON structure.")
    print(f"  Project ID: {proj_id}")
    print(f"  Client Email: {mask_email(client_email)}")
    print(f"  Private Key ID: {sa_data.get('private_key_id', '')[:8]}...")

    # Load via GoogleApiSheetsService
    print("\nAttempting Google Sheets API client initialization...")
    try:
        sheets_service = GoogleApiSheetsService(
            service_account_path=str(sa_path),
            read_only=True
        )
        print("PASS: GoogleApiSheetsService initialized successfully in READ-ONLY mode.")
    except Exception as e:
        print(f"FAILED: Could not initialize GoogleApiSheetsService: {type(e).__name__}: {e}")
        return False, None

    # Test read-only access to DG/DS source spreadsheet
    digi_id = os.getenv("GOOGLE_SHEET_DIGI_ID", DEFAULT_DIGI_SPREADSHEET_ID)
    print(f"\nAttempting read-only access to Digi spreadsheet ({digi_id[:4]}...{digi_id[-4:]})...")
    try:
        meta = sheets_service.get_spreadsheet_metadata(digi_id)
        title = meta.get("properties", {}).get("title", "")
        sheets = [s.get("properties", {}).get("title", "") for s in meta.get("sheets", [])]
        print(f"PASS: Successfully accessed Digi spreadsheet!")
        print(f"  Title: '{title}'")
        print(f"  Worksheets found ({len(sheets)}): {sheets}")
        return True, sheets_service
    except Exception as e:
        print(f"FAILED: Access to Digi spreadsheet failed: {type(e).__name__}: {e}")
        return False, None

if __name__ == "__main__":
    success, service = verify_credentials()
    if not success:
        sys.exit(1)
