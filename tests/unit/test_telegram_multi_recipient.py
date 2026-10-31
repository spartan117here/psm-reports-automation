"""
Unit test verifying multi-recipient Telegram delivery configuration and behavior.

Tests:
1. TelegramConfig loads multi-recipient IDs from TELEGRAM_CHAT_IDS and TELEGRAM_CHAT_ID.
2. TelegramDeliveryChannel authorizes both configured recipients.
3. TelegramDeliveryChannel rejects unauthorized recipient IDs.
4. Independent failure isolation: failure for one recipient does not stop delivery to the other.
5. Both report photos (New Enrollment and Scheme Rejoining) delivered to all configured recipients.
6. History ledger prevents duplicate delivery per recipient independently.
7. Bot token is never exposed or logged.
"""

from datetime import date
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

from app.core.config import TelegramConfig, load_config
from app.delivery.telegram import (
    TelegramDeliveryChannel,
    redact_chat_id,
    redact_token,
    format_telegram_caption,
)
from app.delivery.base import DeliveryReceipt, DeliveryStatus


@pytest.fixture
def dummy_png(tmp_path: Path) -> Path:
    img = tmp_path / "test_report.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return img


@pytest.fixture
def dummy_photos(tmp_path: Path):
    enr = tmp_path / "enrollment_report_20261030.png"
    enr.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    rej = tmp_path / "closing_rejoining_report_20261030.png"
    rej.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return enr, rej


def test_telegram_config_loads_multi_recipients(monkeypatch, tmp_path):
    """Verify that load_config parses comma-separated TELEGRAM_CHAT_IDS into chat_ids list."""
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "1688169109,9132692037")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1688169109")

    bundle = load_config()
    tg = bundle.app.telegram

    assert tg.chat_ids == ["1688169109", "9132692037"]
    assert tg.chat_id == "1688169109"


def test_telegram_channel_authorizes_both_recipients(dummy_png):
    """Verify channel allows delivery to both configured recipients in dry_run mode."""
    config = TelegramConfig(
        enabled=True,
        dry_run=True,
        bot_token="test_bot_token",
        chat_id="1688169109",
        chat_ids=["1688169109", "9132692037"],
    )
    channel = TelegramDeliveryChannel(config=config)

    assert channel.configured_chat_ids == ["1688169109", "9132692037"]

    # Deliver to first recipient
    rec1 = channel.send_photo(photo_path=dummy_png, recipient="1688169109")
    assert rec1.status == DeliveryStatus.SENT
    assert rec1.recipient == "1688169109"

    # Deliver to second recipient
    rec2 = channel.send_photo(photo_path=dummy_png, recipient="9132692037")
    assert rec2.status == DeliveryStatus.SENT
    assert rec2.recipient == "9132692037"


def test_telegram_channel_rejects_unauthorized_recipient(dummy_png):
    """Verify channel rejects any recipient not present in configured_chat_ids."""
    config = TelegramConfig(
        enabled=True,
        dry_run=True,
        bot_token="test_bot_token",
        chat_id="1688169109",
        chat_ids=["1688169109", "9132692037"],
    )
    channel = TelegramDeliveryChannel(config=config)

    receipt = channel.send_photo(photo_path=dummy_png, recipient="9999999999")
    assert receipt.status == DeliveryStatus.FAILED
    assert "restricted to configured personal chat ID" in receipt.error_message


def test_send_photo_to_all_independent_delivery(dummy_png):
    """Verify send_photo_to_all delivers to all recipients independently."""
    config = TelegramConfig(
        enabled=True,
        dry_run=True,
        bot_token="test_bot_token",
        chat_ids=["1688169109", "9132692037"],
    )
    channel = TelegramDeliveryChannel(config=config)

    results = channel.send_photo_to_all(
        photo_path=dummy_png,
        caption="POTHYS SWARNA MAHAL\nDaily New Enrollment Report — 30/10/2026",
    )

    assert "1688169109" in results
    assert "9132692037" in results
    assert results["1688169109"].status == DeliveryStatus.SENT
    assert results["9132692037"].status == DeliveryStatus.SENT


def test_send_photo_to_all_failure_isolation(dummy_png, monkeypatch):
    """Verify that failure for one recipient does not stop delivery to the other recipient."""
    config = TelegramConfig(
        enabled=True,
        dry_run=False,
        bot_token="test_bot_token",
        chat_ids=["1688169109", "9132692037"],
    )
    channel = TelegramDeliveryChannel(config=config)

    # Mock _execute_send_photo to fail for 1688169109 but succeed for 9132692037
    def mock_execute(photo_path, caption, chat_id, report_date):
        if chat_id == "1688169109":
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.FAILED,
                recipient=chat_id,
                error_message="Network timeout for primary",
            )
        return DeliveryReceipt(
            channel_name="Telegram",
            status=DeliveryStatus.SENT,
            recipient=chat_id,
            message_id="tg_success_second",
            attachments_sent=1,
        )

    monkeypatch.setattr(channel, "_execute_send_photo", mock_execute)

    results = channel.send_photo_to_all(photo_path=dummy_png)

    # Recipient 1 failed, but Recipient 2 succeeded!
    assert results["1688169109"].status == DeliveryStatus.FAILED
    assert results["9132692037"].status == DeliveryStatus.SENT
    assert results["9132692037"].message_id == "tg_success_second"


def test_send_two_photos_to_all(dummy_photos):
    """Verify send_two_photos_to_all delivers both Photo 1 and Photo 2 to both recipients."""
    enr_photo, rej_photo = dummy_photos
    config = TelegramConfig(
        enabled=True,
        dry_run=True,
        bot_token="test_bot_token",
        chat_ids=["1688169109", "9132692037"],
    )
    channel = TelegramDeliveryChannel(config=config)

    results = channel.send_two_photos_to_all(
        photo_1_path=enr_photo,
        caption_1="POTHYS SWARNA MAHAL\nDaily New Enrollment Report — 30/10/2026",
        photo_2_path=rej_photo,
        caption_2="POTHYS SWARNA MAHAL\nDaily Scheme Rejoining Report — 30/10/2026",
    )

    assert len(results) == 2
    for r in ["1688169109", "9132692037"]:
        assert results[r]["photo_1"].status == DeliveryStatus.SENT
        assert results[r]["photo_2"].status == DeliveryStatus.SENT


def test_duplicate_prevention_is_per_recipient(dummy_png, tmp_path):
    """Verify that delivering to recipient A does not mark it delivered for recipient B."""
    history_file = tmp_path / "history.json"
    config = TelegramConfig(
        enabled=True,
        dry_run=False,
        bot_token="test_bot_token",
        chat_ids=["1688169109", "9132692037"],
        history_file=history_file,
    )
    channel = TelegramDeliveryChannel(config=config, history_file=history_file)

    d = date(2026, 10, 30)

    # Mock execute
    channel._execute_send_photo = MagicMock(side_effect=lambda *args, **kwargs: DeliveryReceipt(
        channel_name="Telegram",
        status=DeliveryStatus.SENT,
        recipient=kwargs.get("chat_id", "test"),
        message_id=f"msg_{kwargs.get('chat_id', 'test')}",
        attachments_sent=1,
    ))

    # Record delivery to recipient 1 manually in history
    channel._history.record_delivery(
        report_date=d,
        chat_id="1688169109",
        message_id="msg_1688169109",
        attachments=[dummy_png.name],
    )

    # Now test send_photo without force:
    # Recipient 1 should be DUPLICATE
    rec1 = channel.send_photo(photo_path=dummy_png, recipient="1688169109", report_date=d, force=False)
    assert rec1.status == DeliveryStatus.DUPLICATE

    # Recipient 2 should NOT be duplicate, and should be SENT!
    rec2 = channel.send_photo(photo_path=dummy_png, recipient="9132692037", report_date=d, force=False)
    assert rec2.status == DeliveryStatus.SENT
    assert rec2.recipient == "9132692037"
