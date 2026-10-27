"""Unit tests for Telegram delivery channel adapter and security guards."""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock
import pytest
import requests

from app.core.config import TelegramConfig
from app.delivery.base import DeliveryReceipt, DeliveryStatus
from app.delivery.telegram import (
    MAX_PHOTO_SIZE_BYTES,
    TelegramDeliveryChannel,
    format_telegram_caption,
    normalize_chat_id,
    redact_chat_id,
    redact_token,
    resolve_telegram_screenshot,
    sanitize_log_message,
    validate_attachment,
)


@pytest.fixture
def sample_image(tmp_path: Path) -> Path:
    """Create a minimal valid PNG file for testing."""
    img_path = tmp_path / "enrollment_report_20261020.png"
    # Minimal 8-byte PNG header + some mock bytes
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"image data payload")
    return img_path


def test_telegram_disabled_by_default(sample_image: Path):
    """Verify Telegram adapter is disabled by default and returns DISABLED receipt."""
    channel = TelegramDeliveryChannel()
    assert channel.is_enabled() is False
    assert channel.dry_run is True

    receipt = channel.send_photo(
        photo_path=sample_image,
        recipient="1688169109",
    )
    assert receipt.channel_name == "Telegram"
    assert receipt.status == DeliveryStatus.DISABLED
    assert "disabled by policy" in receipt.error_message


def test_telegram_missing_configuration_missing_token(sample_image: Path, monkeypatch):
    """Verify delivery fails gracefully when bot token is not configured in live mode."""
    monkeypatch.setenv("STRICT_READ_ONLY", "false")
    channel = TelegramDeliveryChannel(
        config=TelegramConfig(enabled=True, dry_run=False, bot_token=None, chat_id="1688169109"),
        bot_token=None,
    )
    # Ensure env var is not leaking in
    channel._bot_token = None

    receipt = channel.send_photo(
        photo_path=sample_image,
        recipient="1688169109",
    )
    assert receipt.status == DeliveryStatus.FAILED
    assert "missing TELEGRAM_BOT_TOKEN" in receipt.error_message


def test_telegram_missing_configuration_missing_chat_id(sample_image: Path):
    """Verify delivery fails when chat_id is not configured and none provided."""
    channel = TelegramDeliveryChannel(
        config=TelegramConfig(enabled=True, dry_run=True, chat_id=None),
        chat_id=None,
    )
    channel._chat_id = None

    receipt = channel.send_photo(
        photo_path=sample_image,
        recipient=None,
    )
    assert receipt.status == DeliveryStatus.FAILED
    assert "missing TELEGRAM_CHAT_ID" in receipt.error_message


def test_telegram_missing_image_file(tmp_path: Path):
    """Verify delivery fails when screenshot image file does not exist."""
    channel = TelegramDeliveryChannel(
        config=TelegramConfig(enabled=True, dry_run=True, chat_id="1688169109"),
    )
    non_existent = tmp_path / "non_existent.png"
    receipt = channel.send_photo(
        photo_path=non_existent,
        recipient="1688169109",
    )
    assert receipt.status == DeliveryStatus.FAILED
    assert "Attachment validation failed" in receipt.error_message
    assert "not found" in receipt.error_message


def test_telegram_missing_image_empty_file(tmp_path: Path):
    """Verify delivery fails when screenshot image file is 0 bytes."""
    empty_file = tmp_path / "empty.png"
    empty_file.write_bytes(b"")

    channel = TelegramDeliveryChannel(
        config=TelegramConfig(enabled=True, dry_run=True, chat_id="1688169109"),
    )
    receipt = channel.send_photo(
        photo_path=empty_file,
        recipient="1688169109",
    )
    assert receipt.status == DeliveryStatus.FAILED
    assert "empty (0 bytes)" in receipt.error_message


def test_telegram_missing_image_in_send_report():
    """Verify send_report fails when attachments list is None or empty."""
    channel = TelegramDeliveryChannel(
        config=TelegramConfig(enabled=True, dry_run=True, chat_id="1688169109"),
    )
    receipt = channel.send_report(
        recipient="1688169109",
        message="Daily Report",
        attachments=[],
    )
    assert receipt.status == DeliveryStatus.FAILED
    assert "Missing image attachment" in receipt.error_message


def test_telegram_recipient_restriction(sample_image: Path):
    """Verify delivery is strictly restricted to configured personal chat ID."""
    channel = TelegramDeliveryChannel(
        config=TelegramConfig(enabled=True, dry_run=True, chat_id="1688169109"),
        chat_id="1688169109",
    )
    # Attempting to deliver to an unauthorized chat ID
    unauthorized_id = "9876543210"
    receipt = channel.send_photo(
        photo_path=sample_image,
        recipient=unauthorized_id,
    )
    assert receipt.status == DeliveryStatus.FAILED
    assert "restricted to configured personal chat ID" in receipt.error_message


def test_telegram_dry_run_success(sample_image: Path):
    """Verify dry-run mode returns SENT receipt with simulated ID and zero external requests."""
    channel = TelegramDeliveryChannel(
        config=TelegramConfig(enabled=True, dry_run=True, chat_id="1688169109"),
    )
    receipt = channel.send_photo(
        photo_path=sample_image,
        caption="Daily Executive Summary",
        recipient="1688169109",
    )
    assert receipt.status == DeliveryStatus.SENT
    assert receipt.channel_name == "Telegram"
    assert receipt.recipient == "1688169109"
    assert receipt.message_id.startswith("tg_sim_")
    assert receipt.attachments_sent == 1


def test_telegram_decoupled_authorization_gates(sample_image: Path):
    """
    Verify all three conditions required for live delivery:
    1. enabled=True
    2. dry_run=False
    3. recipient matches configured chat_id
    """
    # Gate 1: If disabled -> DISABLED
    channel_disabled = TelegramDeliveryChannel(
        config=TelegramConfig(enabled=False, dry_run=False, chat_id="1688169109", bot_token="fake_token"),
    )
    receipt1 = channel_disabled.send_photo(photo_path=sample_image, recipient="1688169109")
    assert receipt1.status == DeliveryStatus.DISABLED

    # Gate 2: If dry_run=True -> SIMULATED (no network call)
    channel_sim = TelegramDeliveryChannel(
        config=TelegramConfig(enabled=True, dry_run=True, chat_id="1688169109", bot_token="fake_token"),
    )
    receipt2 = channel_sim.send_photo(photo_path=sample_image, recipient="1688169109")
    assert receipt2.status == DeliveryStatus.SENT
    assert receipt2.message_id.startswith("tg_sim_")

    # Gate 3: If recipient mismatch -> FAILED (restricted)
    channel_mismatch = TelegramDeliveryChannel(
        config=TelegramConfig(enabled=True, dry_run=False, chat_id="1688169109", bot_token="fake_token"),
    )
    receipt3 = channel_mismatch.send_photo(photo_path=sample_image, recipient="9999999999")
    assert receipt3.status == DeliveryStatus.FAILED
    assert "restricted to configured personal chat ID" in receipt3.error_message


def test_strict_read_only_enterprise_protection_remains_intact_with_telegram_enabled(monkeypatch):
    """
    Verify that enabling Telegram delivery does NOT weaken or bypass STRICT_READ_ONLY=true
    for Google Sheets, SheetWriter, or core execution pipelines.
    """
    monkeypatch.setenv("STRICT_READ_ONLY", "true")
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_DRY_RUN", "false")

    # 1. GoogleApiSheetsService must still force read_only=True
    from unittest.mock import patch, MagicMock
    from app.google_sheets.client import GoogleApiSheetsService
    from app.core.exceptions import GoogleSheetsError

    with patch.object(GoogleApiSheetsService, "_init_service", return_value=MagicMock()):
        sheets_svc = GoogleApiSheetsService(read_only=False)
        assert sheets_svc.read_only is True
        with pytest.raises(GoogleSheetsError, match="READ-ONLY mode"):
            sheets_svc.append_rows("SID", "A1:G1", [["data"]])

    # 2. General pipeline commands must remain blocked
    import argparse
    from app.cli import cmd_daily_pipeline, cmd_run

    args_daily = argparse.Namespace(
        report_date="2026-10-20",
        dry_run=False,
        mock=False,
        force=False,
    )
    assert cmd_daily_pipeline(args_daily) == 1

    args_run = argparse.Namespace(
        report_date="2026-10-20",
        report_id="subhiksham",
        dry_run=False,
        force=False,
    )
    assert cmd_run(args_run) == 1


def test_telegram_live_success_mocked(sample_image: Path, monkeypatch):
    """Verify live delivery calls Telegram sendPhoto multipart endpoint and parses success response."""
    monkeypatch.setenv("STRICT_READ_ONLY", "false")
    test_token = "8336617258:AAFhHeoWr8pYKxxMzk9FFTCAr0Cz7gLBdr0"
    channel = TelegramDeliveryChannel(
        config=TelegramConfig(enabled=True, dry_run=False, chat_id="1688169109", bot_token=test_token),
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": True,
        "result": {
            "message_id": 78910,
            "chat": {"id": 1688169109, "type": "private"},
            "date": 1729570000,
        },
    }

    called_url = None
    called_files = None
    called_data = None

    def fake_post(url, data=None, files=None, timeout=None, **kwargs):
        nonlocal called_url, called_files, called_data
        called_url = url
        called_data = data
        called_files = files
        return mock_resp

    monkeypatch.setattr(requests, "post", fake_post)

    receipt = channel.send_photo(
        photo_path=sample_image,
        caption="Verified report screenshot",
        recipient="1688169109",
    )

    assert receipt.status == DeliveryStatus.SENT
    assert receipt.message_id == "tg_78910"
    assert receipt.attachments_sent == 1
    assert "sendPhoto" in called_url
    assert called_data["chat_id"] == "1688169109"
    assert "photo" in called_files
    # Verify multipart tuple has (filename, fileobj, mimetype)
    assert called_files["photo"][0] == sample_image.name
    assert called_files["photo"][2] == "image/png"


def test_telegram_api_error_bad_request(sample_image: Path, monkeypatch):
    """Verify Telegram 400 Bad Request error is handled gracefully and redacts sensitive data."""
    monkeypatch.setenv("STRICT_READ_ONLY", "false")
    channel = TelegramDeliveryChannel(
        config=TelegramConfig(
            enabled=True,
            dry_run=False,
            chat_id="1688169109",
            bot_token="8336617258:AAFhHeoWr8pYKxxMzk9FFTCAr0Cz7gLBdr0",
        ),
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {
        "ok": False,
        "error_code": 400,
        "description": "Bad Request: chat not found",
    }
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: mock_resp)

    receipt = channel.send_photo(
        photo_path=sample_image,
        recipient="1688169109",
    )
    assert receipt.status == DeliveryStatus.FAILED
    assert "Bad Request: chat not found" in receipt.error_message
    assert "8336617258" not in receipt.error_message


def test_telegram_api_error_unauthorized(sample_image: Path, monkeypatch):
    """Verify Telegram 401 Unauthorized error is handled cleanly."""
    monkeypatch.setenv("STRICT_READ_ONLY", "false")
    channel = TelegramDeliveryChannel(
        config=TelegramConfig(
            enabled=True,
            dry_run=False,
            chat_id="1688169109",
            bot_token="invalid_token_12345:ABCDE",
        ),
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json.return_value = {
        "ok": False,
        "error_code": 401,
        "description": "Unauthorized",
    }
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: mock_resp)

    receipt = channel.send_photo(
        photo_path=sample_image,
        recipient="1688169109",
    )
    assert receipt.status == DeliveryStatus.FAILED
    assert "Unauthorized" in receipt.error_message


def test_telegram_timeout(sample_image: Path, monkeypatch):
    """Verify request timeout is caught and returns FAILED receipt."""
    monkeypatch.setenv("STRICT_READ_ONLY", "false")
    channel = TelegramDeliveryChannel(
        config=TelegramConfig(
            enabled=True,
            dry_run=False,
            chat_id="1688169109",
            bot_token="8336617258:AAFhHeoWr8pYKxxMzk9FFTCAr0Cz7gLBdr0",
            timeout_seconds=5,
        ),
    )

    def fake_timeout(*args, **kwargs):
        raise requests.exceptions.Timeout("Connection timed out after 5 seconds")

    monkeypatch.setattr(requests, "post", fake_timeout)

    receipt = channel.send_photo(
        photo_path=sample_image,
        recipient="1688169109",
    )
    assert receipt.status == DeliveryStatus.FAILED
    assert "timed out after 5s" in receipt.error_message


def test_telegram_network_exception_token_redacted(sample_image: Path, monkeypatch):
    """Verify network exceptions that include the URL with bot token are strictly scrubbed."""
    monkeypatch.setenv("STRICT_READ_ONLY", "false")
    raw_token = "8336617258:AAFhHeoWr8pYKxxMzk9FFTCAr0Cz7gLBdr0"
    channel = TelegramDeliveryChannel(
        config=TelegramConfig(
            enabled=True,
            dry_run=False,
            chat_id="1688169109",
            bot_token=raw_token,
        ),
    )

    def fake_error(*args, **kwargs):
        raise requests.exceptions.RequestException(
            f"HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded with url: /bot{raw_token}/sendPhoto"
        )

    monkeypatch.setattr(requests, "post", fake_error)

    receipt = channel.send_photo(
        photo_path=sample_image,
        recipient="1688169109",
    )
    assert receipt.status == DeliveryStatus.FAILED
    # Crucial security assertion: the raw bot token must never appear in the error message
    assert raw_token not in receipt.error_message
    assert "[BOT_TOKEN_REDACTED]" in receipt.error_message


def test_token_redaction_utilities():
    """Verify token and chat ID masking helper functions."""
    # Redact token
    assert redact_token(None) == "[NONE]"
    assert redact_token("") == "[NONE]"
    token = "8336617258:AAFhHeoWr8pYKxxMzk9FFTCAr0Cz7gLBdr0"
    masked = redact_token(token)
    assert masked.startswith("8336***")
    assert token not in masked

    # Redact chat ID
    assert redact_chat_id(None) == "[UNSPECIFIED]"
    assert redact_chat_id("1688169109") == "16****9109"

    # Sanitize message
    msg = f"Failed calling https://api.telegram.org/bot{token}/sendPhoto"
    sanitized = sanitize_log_message(msg, token)
    assert token not in sanitized
    assert "[BOT_TOKEN_REDACTED]" in sanitized


def test_normalize_chat_id():
    """Verify chat ID normalization for valid and invalid formats."""
    assert normalize_chat_id("1688169109") == "1688169109"
    assert normalize_chat_id("  1688169109  ") == "1688169109"
    assert normalize_chat_id(1688169109) == "1688169109"
    assert normalize_chat_id("-1001234567890") == "-1001234567890"
    assert normalize_chat_id("@pothys_channel") == "@pothys_channel"

    with pytest.raises(ValueError, match="cannot be None"):
        normalize_chat_id(None)
    with pytest.raises(ValueError, match="cannot be None or empty"):
        normalize_chat_id("")
    with pytest.raises(ValueError, match="Invalid non-numeric"):
        normalize_chat_id("invalid_id")


def test_attachment_validation(tmp_path: Path):
    """Verify attachment validation rules."""
    # Valid file
    valid_file = tmp_path / "valid.png"
    valid_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"data")
    assert validate_attachment(valid_file) == valid_file.resolve()

    # Unsupported extension
    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4")
    with pytest.raises(ValueError, match="Unsupported attachment type"):
        validate_attachment(pdf_file)

    # Empty file
    empty_file = tmp_path / "empty.jpg"
    empty_file.write_bytes(b"")
    with pytest.raises(ValueError, match="is empty"):
        validate_attachment(empty_file)

    # Missing file
    missing = tmp_path / "not_there.png"
    with pytest.raises(FileNotFoundError, match="not found"):
        validate_attachment(missing)


def test_idempotency_duplicate_block(sample_image: Path, tmp_path: Path, monkeypatch):
    """Verify idempotency tracking prevents duplicate sends for confirmed live deliveries unless forced."""
    monkeypatch.setenv("STRICT_READ_ONLY", "false")
    history_file = tmp_path / "tg_history.json"
    channel = TelegramDeliveryChannel(
        config=TelegramConfig(
            enabled=True,
            dry_run=False,
            chat_id="1688169109",
            bot_token="8336617258:AAFhHeoWr8pYKxxMzk9FFTCAr0Cz7gLBdr0",
            history_file=history_file,
        ),
    )
    report_date = date(2026, 10, 20)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": True,
        "result": {
            "message_id": 999123,
            "chat": {"id": 1688169109},
        },
    }
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: mock_resp)

    # First send: Confirmed live delivery
    receipt1 = channel.send_photo(
        photo_path=sample_image,
        recipient="1688169109",
        report_date=report_date,
    )
    assert receipt1.status == DeliveryStatus.SENT
    assert receipt1.message_id == "tg_999123"

    # Second send: Duplicate blocked
    receipt2 = channel.send_photo(
        photo_path=sample_image,
        recipient="1688169109",
        report_date=report_date,
        force=False,
    )
    assert receipt2.status == DeliveryStatus.DUPLICATE
    assert "Duplicate delivery blocked" in receipt2.error_message

    # Third send with force=True: Allowed
    receipt3 = channel.send_photo(
        photo_path=sample_image,
        recipient="1688169109",
        report_date=report_date,
        force=True,
    )
    assert receipt3.status == DeliveryStatus.SENT


def test_dry_run_does_not_poison_delivery_history(sample_image: Path, tmp_path: Path):
    """
    Regression test: Dry-run simulations must never record to persistent delivery history
    and must never block subsequent runs as duplicates.
    """
    history_file = tmp_path / "tg_history.json"
    channel = TelegramDeliveryChannel(
        config=TelegramConfig(
            enabled=True,
            dry_run=True,
            chat_id="1688169109",
            history_file=history_file,
        ),
    )
    report_date = date(2026, 10, 20)

    # First dry run
    receipt1 = channel.send_photo(
        photo_path=sample_image,
        recipient="1688169109",
        report_date=report_date,
    )
    assert receipt1.status == DeliveryStatus.SENT
    assert receipt1.message_id.startswith("tg_sim_")
    # Verify history file was not created or has no entries
    assert not history_file.is_file() or len(channel._history._load()) == 0

    # Second dry run: Must NOT be blocked as duplicate
    receipt2 = channel.send_photo(
        photo_path=sample_image,
        recipient="1688169109",
        report_date=report_date,
        force=False,
    )
    assert receipt2.status == DeliveryStatus.SENT
    assert receipt2.message_id.startswith("tg_sim_")


def test_simulated_records_in_history_ignored(sample_image: Path, tmp_path: Path):
    """
    Regression test: Any existing simulated record (tg_sim_...) in delivery history
    must be ignored by is_already_delivered and never block delivery.
    """
    import json
    history_file = tmp_path / "tg_history.json"
    # Seed history file with a simulated attempt
    history_file.write_text(
        json.dumps({
            "deliveries": [
                {
                    "report_date": "2026-10-20",
                    "chat_id": "1688169109",
                    "channel": "Telegram",
                    "status": "SENT",
                    "message_id": "tg_sim_ee0094f759df",
                    "attachments": ["enrollment_report_20261020.png"],
                    "timestamp": "2026-10-22T10:33:23.254356",
                }
            ]
        }),
        encoding="utf-8",
    )

    channel = TelegramDeliveryChannel(
        config=TelegramConfig(
            enabled=True,
            dry_run=True,
            chat_id="1688169109",
            history_file=history_file,
        ),
    )
    report_date = date(2026, 10, 20)

    # is_already_delivered must return False for simulated records
    assert channel._history.is_already_delivered(report_date, "1688169109", "enrollment_report_20261020.png") is False

    # Dry run send must NOT be blocked
    receipt = channel.send_photo(
        photo_path=sample_image,
        recipient="1688169109",
        report_date=report_date,
        force=False,
    )
    assert receipt.status == DeliveryStatus.SENT


def test_caption_formatting():
    """Verify caption contains exactly two lines with professional header and dynamic date."""
    caption = format_telegram_caption(
        report_date=date(2026, 10, 20),
        report_title="New Enrollment Report",
        enrollment_total=450,
        closing_total=320,
        rejoining_rate_pct=21.45,
        notes="All branches verified.",
    )
    expected = "POTHYS SWARNA MAHAL\nDaily New Enrollment Report — 20/10/2026"
    assert caption == expected
    lines = caption.split("\n")
    assert len(lines) == 2
    assert lines[0] == "POTHYS SWARNA MAHAL"
    assert lines[1] == "Daily New Enrollment Report — 20/10/2026"


def test_caption_exact_new_enrollment_report_26_oct():
    """Verify exact two-line caption for New Enrollment Report on 26/10/2026."""
    caption = format_telegram_caption(
        report_date=date(2026, 10, 26),
        report_title="New Enrollment Report",
    )
    expected = "POTHYS SWARNA MAHAL\nDaily New Enrollment Report — 26/10/2026"
    assert caption == expected
    assert caption.split("\n") == [
        "POTHYS SWARNA MAHAL",
        "Daily New Enrollment Report — 26/10/2026",
    ]


def test_caption_exact_scheme_rejoining_report_26_oct():
    """Verify exact two-line caption for Scheme Rejoining Report on 26/10/2026."""
    caption = format_telegram_caption(
        report_date=date(2026, 10, 26),
        report_title="Scheme Rejoining Report",
    )
    expected = "POTHYS SWARNA MAHAL\nDaily Scheme Rejoining Report — 26/10/2026"
    assert caption == expected
    assert caption.split("\n") == [
        "POTHYS SWARNA MAHAL",
        "Daily Scheme Rejoining Report — 26/10/2026",
    ]


def test_caption_removes_all_emojis_metrics_separators_and_footers():
    """
    Verify that no emoji, metrics (count, revenue, rejoining %), separators,
    or confidentiality footer remain in either caption.
    """
    caption_enr = format_telegram_caption(
        report_date=date(2026, 10, 26),
        report_title="New Enrollment Report",
        enrollment_total=30737,
        notes="Revenue: INR 354,754,611.00",
    )
    caption_cl = format_telegram_caption(
        report_date=date(2026, 10, 26),
        report_title="Scheme Rejoining Report",
        closing_total=4780,
        rejoining_rate_pct=20.86,
        notes="Closed Weight: 34204.01g",
    )

    for cap in (caption_enr, caption_cl):
        # Must be exactly 2 lines
        lines = cap.split("\n")
        assert len(lines) == 2
        # No emojis
        assert "📊" not in cap
        assert "🔒" not in cap
        assert "ℹ️" not in cap
        assert "•" not in cap
        # No separators
        assert "━" not in cap
        assert "---" not in cap
        # No confidentiality footer
        assert "Confidential" not in cap
        assert "Internal Management Distribution" not in cap
        # No metric lines
        assert "New Scheme Enrollments" not in cap
        assert "Scheme Closures" not in cap
        assert "Overall Rejoining Rate" not in cap
        assert "30737" not in cap
        assert "4780" not in cap
        assert "20.86%" not in cap
        assert "Revenue" not in cap
        assert "Closed Weight" not in cap


def test_caption_dynamic_date_generation():
    """Verify date is generated dynamically from report_date parameter."""
    cap1 = format_telegram_caption(report_date=date(2026, 10, 25), report_title="New Enrollment Report")
    assert cap1.endswith("25/10/2026")

    cap2 = format_telegram_caption(report_date=date(2026, 10, 26), report_title="New Enrollment Report")
    assert cap2.endswith("26/10/2026")

    cap3 = format_telegram_caption(report_date=date(2026, 11, 4), report_title="Scheme Rejoining Report")
    assert cap3.endswith("04/11/2026")



def test_cli_send_telegram_disabled_fails(monkeypatch):
    """Verify CLI send-telegram exits with 1 when channel is disabled."""
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
    from app.cli import cmd_send_telegram
    import argparse
    args = argparse.Namespace(
        report_date="2026-10-20",
        dry_run=False,
        recipient=None,
        target="enrollment",
        screenshot_path=None,
        force=False,
        output_dir=None,
    )
    exit_code = cmd_send_telegram(args)
    assert exit_code == 1


def test_cli_send_telegram_dry_run_flag_succeeds(monkeypatch, sample_image):
    """Verify CLI send-telegram with --dry-run simulates successfully."""
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
    from app.cli import cmd_send_telegram
    import argparse
    args = argparse.Namespace(
        report_date="2026-10-20",
        dry_run=True,
        recipient="1688169109",
        target="enrollment",
        screenshot_path=str(sample_image),
        force=False,
        output_dir=None,
    )
    exit_code = cmd_send_telegram(args)
    assert exit_code == 0


def test_cli_send_telegram_live_authorized_all_conditions_met(monkeypatch, sample_image, tmp_path):
    """Verify CLI send-telegram executes live when all 3 conditions are satisfied under permanent STRICT_READ_ONLY."""
    monkeypatch.setenv("STRICT_READ_ONLY", "true")  # STRICT_READ_ONLY remains active in environment!
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_DRY_RUN", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "8336617258:AAFhHeoWr8pYKxxMzk9FFTCAr0Cz7gLBdr0")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1688169109")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": True,
        "result": {
            "message_id": 888777,
            "chat": {"id": 1688169109},
        },
    }
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: mock_resp)

    # Isolate history file to tmp_path so the real ledger is never touched by unit tests
    from app.core.config import load_config as orig_load_config
    def fake_load_config(*a, **kw):
        bundle = orig_load_config(*a, **kw)
        bundle.app.telegram.history_file = tmp_path / "tg_test_hist.json"
        return bundle
    monkeypatch.setattr("app.cli.load_config", fake_load_config)

    from app.cli import cmd_send_telegram
    import argparse
    args = argparse.Namespace(
        report_date="2026-10-20",
        dry_run=False,
        recipient="1688169109",
        target="enrollment",
        screenshot_path=str(sample_image),
        force=False,
        output_dir=None,
    )
    exit_code = cmd_send_telegram(args)
    assert exit_code == 0


def test_cli_send_telegram_recipient_mismatch_fails(monkeypatch, sample_image):
    """Verify CLI send-telegram rejects recipient that does not match authorized chat ID."""
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_DRY_RUN", "false")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1688169109")

    from app.cli import cmd_send_telegram
    import argparse
    args = argparse.Namespace(
        report_date="2026-10-20",
        dry_run=False,
        recipient="9999999999",  # Unauthorized recipient
        target="enrollment",
        screenshot_path=str(sample_image),
        force=False,
        output_dir=None,
    )
    exit_code = cmd_send_telegram(args)
    assert exit_code == 1
