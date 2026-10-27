"""
Resend 26 October 2026 Reports Delivery Verification Script.

Explicitly authorized task:
Resend the existing, already-generated report screenshots for 26 October 2026
to the configured Telegram bot to verify the updated professional two-line captions.

Guards:
- STRICT_READ_ONLY=true
- Reuse existing screenshot files (no regeneration)
- Verify production validation gate evidence prior to dispatch
- Check exact 2-line captions (zero emojis, zero metric summaries, zero footers)
- Verify Telegram API HTTP 200 and ok=True responses
- Mask all sensitive tokens, credentials, and chat IDs
"""

from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import sys

# Configure UTF-8 encoding for console output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from PIL import Image

from app.delivery.base import DeliveryStatus
from app.delivery.telegram import (
    TelegramDeliveryChannel,
    format_telegram_caption,
    redact_chat_id,
    redact_token,
)


def compute_sha256(path: Path) -> str:
    """Calculate SHA256 checksum of file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def main():
    print("=" * 80)
    print("TELEGRAM DELIVERY VERIFICATION — RESEND 26 OCTOBER 2026 REPORTS")
    print("=" * 80)

    # -------------------------------------------------------------------------
    # STEP 1: SAFETY & ENVIRONMENT CHECKS
    # -------------------------------------------------------------------------
    print("\n>>> STEP 1: Verifying Safety Constraints & Environment...")
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    strict_ro = os.getenv("STRICT_READ_ONLY", "true").lower() == "true"
    print(f"  STRICT_READ_ONLY              : {strict_ro}")
    if not strict_ro:
        print("[FAIL-CLOSED] STRICT_READ_ONLY must be enabled (true). Aborting.")
        sys.exit(1)

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("[FAIL-CLOSED] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing. Aborting.")
        sys.exit(1)

    print(f"  Configured Bot Token (Masked) : {redact_token(bot_token)}")
    print(f"  Configured Chat ID (Masked)   : {redact_chat_id(chat_id)}")
    print("  [PASS] Safety constraints and destination verified.")

    # -------------------------------------------------------------------------
    # STEP 2: LOCATE & VERIFY EXISTING ARTIFACTS
    # -------------------------------------------------------------------------
    print("\n>>> STEP 2: Locating Existing 26 October 2026 Report Artifacts...")
    report_date = date(2026, 10, 26)
    screenshots_dir = PROJECT_ROOT / "data" / "test_runs" / "report_2026-10-26" / "screenshots"

    enr_path = screenshots_dir / "New_Enrollment_Report_2026-10-26.png"
    rej_path = screenshots_dir / "Scheme_Rejoining_Report_2026-10-26.png"

    if not enr_path.is_file():
        print(f"[FAIL-CLOSED] Missing New Enrollment screenshot: {enr_path}")
        sys.exit(1)
    if not rej_path.is_file():
        print(f"[FAIL-CLOSED] Missing Scheme Rejoining screenshot: {rej_path}")
        sys.exit(1)

    enr_size = enr_path.stat().st_size
    rej_size = rej_path.stat().st_size
    enr_sha_before = compute_sha256(enr_path)
    rej_sha_before = compute_sha256(rej_path)

    print(f"  New Enrollment Screenshot    : {enr_path}")
    print(f"    Size: {enr_size:,} bytes | SHA256: {enr_sha_before[:16]}...")
    print(f"  Scheme Rejoining Screenshot  : {rej_path}")
    print(f"    Size: {rej_size:,} bytes | SHA256: {rej_sha_before[:16]}...")

    # Validate image readability and dimensions via PIL
    try:
        with Image.open(enr_path) as img:
            enr_dims = img.size
            img.verify()
        with Image.open(rej_path) as img:
            rej_dims = img.size
            img.verify()
    except Exception as e:
        print(f"[FAIL-CLOSED] Screenshot image file corrupted or invalid: {e}")
        sys.exit(1)

    print(f"  [PASS] Both screenshot files are valid PNGs (Enrollment: {enr_dims[0]}x{enr_dims[1]}, Rejoining: {rej_dims[0]}x{rej_dims[1]}).")

    # -------------------------------------------------------------------------
    # STEP 3: CHECK RECORDED VALIDATION RESULTS & PERMISSION TO SEND
    # -------------------------------------------------------------------------
    print("\n>>> STEP 3: Checking Recorded Production Gate Evidence...")
    evidence_path = PROJECT_ROOT / "data" / "test_runs" / "report_2026-10-26" / "audit_evidence_20261026.json"
    if not evidence_path.is_file():
        print(f"[FAIL-CLOSED] Audit evidence file missing: {evidence_path}. Delivery blocked.")
        sys.exit(1)

    with open(evidence_path, "r", encoding="utf-8") as f:
        evidence = json.load(f)

    if evidence.get("report_date") != "2026-10-26":
        print(f"[FAIL-CLOSED] Report date mismatch in audit evidence: {evidence.get('report_date')}. Delivery blocked.")
        sys.exit(1)

    gate_status = evidence.get("gate_status")
    gate_safe = evidence.get("gate_is_safe")
    print(f"  Recorded Gate Status          : {gate_status}")
    print(f"  Recorded Gate Is Safe         : {gate_safe}")

    if gate_status != "PASS" or not gate_safe:
        print(f"[FAIL-CLOSED] Production gate did not pass safely (status={gate_status}, safe={gate_safe}). Delivery blocked.")
        sys.exit(1)

    print("  [PASS] Production validation gate permits delivery.")

    # -------------------------------------------------------------------------
    # STEP 4: VERIFY CAPTION FORMATTING (EXACT TWO LINES ONLY)
    # -------------------------------------------------------------------------
    print("\n>>> STEP 4: Formatting and Verifying Captions...")
    caption_enr = format_telegram_caption(report_date, "New Enrollment Report")
    caption_rej = format_telegram_caption(report_date, "Scheme Rejoining Report")

    expected_enr = "POTHYS SWARNA MAHAL\nDaily New Enrollment Report — 26/10/2026"
    expected_rej = "POTHYS SWARNA MAHAL\nDaily Scheme Rejoining Report — 26/10/2026"

    print("--- New Enrollment Caption ---")
    print(repr(caption_enr))
    print("--- Scheme Rejoining Caption ---")
    print(repr(caption_rej))

    assert caption_enr == expected_enr, f"Enrollment caption mismatch: expected {repr(expected_enr)}, got {repr(caption_enr)}"
    assert caption_rej == expected_rej, f"Rejoining caption mismatch: expected {repr(expected_rej)}, got {repr(caption_rej)}"

    # Negative assertion checks: no emojis, no footers, no numbers other than the date, exactly 2 lines
    forbidden_tokens = ["📊", "🔒", "ℹ️", "•", "━", "---", "Confidential", "Revenue", "Closed", "30737", "4780"]
    for cap, name in [(caption_enr, "New Enrollment"), (caption_rej, "Scheme Rejoining")]:
        lines = cap.split("\n")
        assert len(lines) == 2, f"{name} caption must have exactly 2 lines, has {len(lines)}"
        for tok in forbidden_tokens:
            assert tok not in cap, f"{name} caption contains forbidden token: {tok}"

    print("  [PASS] Captions match required two-line header specifications 100% exactly.")

    # -------------------------------------------------------------------------
    # STEP 5: DELIVER BOTH SCREENSHOTS TO CONFIGURED TELEGRAM BOT
    # -------------------------------------------------------------------------
    print("\n>>> STEP 5: Dispatching Screenshots via TelegramDeliveryChannel...")
    tg_channel = TelegramDeliveryChannel(
        bot_token=bot_token,
        chat_id=chat_id,
        enabled=True,
        dry_run=False,
    )

    print(f"  Sending Image 1: {enr_path.name}...")
    receipt1 = tg_channel.send_photo(
        photo_path=enr_path,
        caption=caption_enr,
        recipient=chat_id,
        report_date=report_date,
        force=True,  # Explicitly authorized resend
    )
    print(f"    Status       : {receipt1.status.value}")
    print(f"    Message ID   : {receipt1.message_id}")
    print(f"    Error Message: {receipt1.error_message}")

    if receipt1.status != DeliveryStatus.SENT:
        print(f"[FAIL] Image 1 delivery failed: {receipt1.error_message}. Halting.")
        sys.exit(1)

    print(f"\n  Sending Image 2: {rej_path.name}...")
    receipt2 = tg_channel.send_photo(
        photo_path=rej_path,
        caption=caption_rej,
        recipient=chat_id,
        report_date=report_date,
        force=True,  # Explicitly authorized resend
    )
    print(f"    Status       : {receipt2.status.value}")
    print(f"    Message ID   : {receipt2.message_id}")
    print(f"    Error Message: {receipt2.error_message}")

    if receipt2.status != DeliveryStatus.SENT:
        print(f"[FAIL] Image 2 delivery failed: {receipt2.error_message}. Halting.")
        sys.exit(1)

    # -------------------------------------------------------------------------
    # STEP 6: VERIFY IMMUTABILITY OF ARTIFACTS
    # -------------------------------------------------------------------------
    print("\n>>> STEP 6: Verifying Original Report Screenshots Were Not Modified...")
    enr_sha_after = compute_sha256(enr_path)
    rej_sha_after = compute_sha256(rej_path)

    assert enr_sha_before == enr_sha_after, "New Enrollment screenshot was altered during delivery!"
    assert rej_sha_before == rej_sha_after, "Scheme Rejoining screenshot was altered during delivery!"
    assert enr_path.stat().st_size == enr_size, "New Enrollment file size changed!"
    assert rej_path.stat().st_size == rej_size, "Scheme Rejoining file size changed!"

    print("  [PASS] Immutability confirmed: Screenshot bytes and checksums are 100% identical.")

    # -------------------------------------------------------------------------
    # STEP 7: RECORD DELIVERY LOG & SUMMARY
    # -------------------------------------------------------------------------
    resend_record = {
        "timestamp": datetime.now().isoformat(),
        "report_date": "2026-10-26",
        "action": "RESEND_DELIVERY_VERIFICATION",
        "strict_read_only": strict_ro,
        "screenshots": {
            "new_enrollment": {
                "path": str(enr_path),
                "caption": caption_enr,
                "status": receipt1.status.value,
                "message_id": receipt1.message_id,
                "sha256": enr_sha_after,
            },
            "scheme_rejoining": {
                "path": str(rej_path),
                "caption": caption_rej,
                "status": receipt2.status.value,
                "message_id": receipt2.message_id,
                "sha256": rej_sha_after,
            },
        },
    }

    log_path = PROJECT_ROOT / "data" / "test_runs" / "report_2026-10-26" / "telegram_resend_verification.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(resend_record, f, indent=2)

    print(f"\n[SUCCESS] Delivery verification log saved to: {log_path}")
    print("\n" + "=" * 80)
    print("ALL DELIVERY VERIFICATION CHECKS COMPLETED SUCCESSFULLY")
    print("=" * 80)


if __name__ == "__main__":
    main()
