"""
Unit test verifying exact two-photo Telegram delivery and spreadsheet-style card generation.

Tests:
1. Two-photo delivery: Photo 1 = New Enrollment Report, Photo 2 = Scheme Rejoining Report.
2. Captions strictly adhere to two-line header-only format (zero emojis, dynamic date).
3. Zero duplicate delivery calls.
4. Validates that screenshots cover all required sections:
   - Enrollment: Revenue table + Enrollment Count table.
   - Scheme Rejoining: Main Rejoining table + Closed Weight table + Closed Count table (with SS/SV split).
"""

from datetime import date
from pathlib import Path
import pytest

from app.delivery.telegram import (
    TelegramDeliveryChannel,
    format_telegram_caption,
)
from app.delivery.base import DeliveryReceipt, DeliveryStatus


def test_telegram_caption_strict_two_line_header():
    """Verify caption contains exactly two lines with dynamic date and zero emojis/summaries."""
    d = date(2026, 10, 29)
    
    # 1. New Enrollment
    cap_enr = format_telegram_caption(report_date=d, report_title="New Enrollment Report")
    lines_enr = cap_enr.split("\n")
    assert len(lines_enr) == 2, f"Expected exactly 2 lines, got {len(lines_enr)}: {cap_enr}"
    assert lines_enr[0] == "POTHYS SWARNA MAHAL"
    assert lines_enr[1] == "Daily New Enrollment Report — 29/10/2026"
    assert "₹" not in cap_enr
    assert "%" not in cap_enr
    assert "Revenue" not in cap_enr
    assert "Closed" not in cap_enr

    # 2. Scheme Rejoining
    cap_rej = format_telegram_caption(report_date=d, report_title="Scheme Rejoining Report")
    lines_rej = cap_rej.split("\n")
    assert len(lines_rej) == 2, f"Expected exactly 2 lines, got {len(lines_rej)}: {cap_rej}"
    assert lines_rej[0] == "POTHYS SWARNA MAHAL"
    assert lines_rej[1] == "Daily Scheme Rejoining Report — 29/10/2026"
    assert "%" not in cap_rej
    assert "Weight" not in cap_rej


def test_cli_send_telegram_two_photos_exactly(monkeypatch, tmp_path):
    """
    Verify cmd_send_telegram(target='all') sends exactly two photo messages in order:
    1. New Enrollment Report
    2. Scheme Rejoining Report
    with zero duplicate calls.
    """
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_DRY_RUN", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "8336617258:AAFhHeoWr8pYKxxMzk9FFTCAr0Cz7gLBdr0")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1688169109")

    # Create dummy images
    d_tag = "20261029"
    img_dir = tmp_path / "screenshots"
    img_dir.mkdir(parents=True)
    enr_img = img_dir / f"enrollment_report_{d_tag}.png"
    rej_img = img_dir / f"closing_rejoining_report_{d_tag}.png"
    enr_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    rej_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    from app.cli import cmd_send_telegram
    import argparse

    sent_photos = []

    def mock_send_photo(self, photo_path, caption, recipient, report_date=None, force=False):
        sent_photos.append({
            "path": Path(photo_path).name,
            "caption": caption,
            "recipient": recipient,
            "report_date": report_date,
        })
        return DeliveryReceipt(
            channel_name="Telegram",
            status=DeliveryStatus.SENT,
            recipient=recipient,
            message_id=f"msg_{len(sent_photos)}",
            attachments_sent=1,
        )

    monkeypatch.setattr(TelegramDeliveryChannel, "send_photo", mock_send_photo)

    args = argparse.Namespace(
        report_date="2026-10-29",
        recipient="1688169109",
        target="all",
        screenshot_path=None,
        dry_run=True,
        force=False,
        mock=False,
        output_dir=str(img_dir),
    )

    exit_code = cmd_send_telegram(args)
    assert exit_code == 0

    # Exactly TWO photo messages must have been delivered
    assert len(sent_photos) == 2, f"Expected exactly 2 photos, got {len(sent_photos)}"

    # Photo 1: New Enrollment Report
    assert sent_photos[0]["path"] == f"enrollment_report_{d_tag}.png"
    assert sent_photos[0]["caption"] == "POTHYS SWARNA MAHAL\nDaily New Enrollment Report — 29/10/2026"

    # Photo 2: Scheme Rejoining Report
    assert sent_photos[1]["path"] == f"closing_rejoining_report_{d_tag}.png"
    assert sent_photos[1]["caption"] == "POTHYS SWARNA MAHAL\nDaily Scheme Rejoining Report — 29/10/2026"


def test_spreadsheet_screenshot_boundaries_cover_all_tables():
    """Verify GoogleSheetsScreenshotEngine range detection covers all required tables."""
    from app.reporting.screenshots import detect_and_validate_enrollment_range, detect_and_validate_closing_range
    from app.google_sheets.client import MockGoogleSheetsService
    from app.cli import seed_mock_screenshot_data

    mock_svc = MockGoogleSheetsService()
    target_d = date(2026, 10, 14)
    seed_mock_screenshot_data(mock_svc, report_date=target_d)

    # 1. Enrollment range must cover both Table 1 (Revenue) and Table 2 (Counts)
    enr_val = detect_and_validate_enrollment_range(
        mock_svc,
        spreadsheet_id="mock_enrollment_id",
        worksheet_name="Consolidate Report - Oct",
        expected_date=target_d,
    )
    assert enr_val.is_valid
    assert enr_val.detected_range == "E3:L30"
    assert enr_val.total_row_found
    assert enr_val.row_count >= 20  # covers both tables

    # 2. Closing range must cover Section 1, Section 2 (Weight), and Section 3 (Count SS/SV)
    close_val = detect_and_validate_closing_range(
        mock_svc,
        spreadsheet_id="mock_closing_id",
        worksheet_name="October - 2026",
        expected_date=target_d,
    )
    assert close_val.is_valid
    assert close_val.detected_range == "L3:Q26"
    assert close_val.total_row_found
    assert "SS" in close_val.table_headers
    assert "SV" in close_val.table_headers
    assert "WEIGHT" in close_val.table_headers


def test_phase3_automatic_telegram_delivery_integration(monkeypatch, tmp_path):
    """
    Verify Phase 3 automatically executes Telegram delivery after screenshot generation:
    - Generates both screenshots with target='all'
    - Automatically dispatches Photo 1 (Enrollment) then Photo 2 (Closing)
    - Zero separate manual CLI commands required
    """
    from app.cli import run_phase3_consolidation_and_delivery
    import argparse
    import app.cli as cli_mod

    target_d = date(2026, 10, 29)
    d_tag = "20261029"
    img_dir = tmp_path / "screenshots"
    img_dir.mkdir(parents=True)
    enr_img = img_dir / f"enrollment_report_{d_tag}.png"
    rej_img = img_dir / f"closing_rejoining_report_{d_tag}.png"
    enr_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    rej_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # Mock Digi validation tracker
    monkeypatch.setattr("app.reporting.digi.DigiValidationTracker.is_already_validated", lambda self, dt: True)
    # Mock cmd_digi_sync
    monkeypatch.setattr(cli_mod, "cmd_digi_sync", lambda args: 0)
    # Mock cmd_screenshot to verify target='all'
    captured_targets = []
    def mock_cmd_screenshot(args):
        captured_targets.append(args.target)
        return 0
    monkeypatch.setattr(cli_mod, "cmd_screenshot", mock_cmd_screenshot)

    # Track automatic telegram sends
    sent_photos = []
    def mock_send_photo(self, photo_path, caption, recipient, report_date=None, force=False):
        sent_photos.append({
            "path": Path(photo_path).name,
            "caption": caption,
            "recipient": recipient,
            "report_date": report_date,
        })
        return DeliveryReceipt(
            channel_name="Telegram",
            status=DeliveryStatus.SENT,
            recipient=recipient,
            message_id=f"auto_msg_{len(sent_photos)}",
            attachments_sent=1,
        )
    monkeypatch.setattr(TelegramDeliveryChannel, "send_photo", mock_send_photo)

    args = argparse.Namespace(
        report_date="2026-10-29",
        recipient="1688169109",
        dry_run=True,
        mock=False,
        output_dir=str(img_dir),
        service_account=None,
    )

    exit_code = run_phase3_consolidation_and_delivery(args, target_date=target_d)
    assert exit_code == 0

    # Verify screenshot step was called with target='all'
    assert captured_targets == ["all"]

    # Verify automatic Telegram delivery sent exactly two photos in order
    assert len(sent_photos) == 2
    assert sent_photos[0]["path"] == f"enrollment_report_{d_tag}.png"
    assert sent_photos[0]["caption"] == "POTHYS SWARNA MAHAL\nDaily New Enrollment Report — 29/10/2026"
    assert sent_photos[1]["path"] == f"closing_rejoining_report_{d_tag}.png"
    assert sent_photos[1]["caption"] == "POTHYS SWARNA MAHAL\nDaily Scheme Rejoining Report — 29/10/2026"
