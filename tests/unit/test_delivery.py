"""Unit tests for WhatsApp delivery channel adapter and safety guards."""

from datetime import date
from pathlib import Path
import pytest

from app.core.config import WhatsAppConfig
from app.delivery.base import DeliveryReceipt, DeliveryStatus
from app.delivery.whatsapp import (
    MAX_ATTACHMENT_SIZE_BYTES,
    WhatsAppDeliveryChannel,
    format_executive_report_message,
    normalize_phone_number,
    validate_attachment,
)


def test_whatsapp_disabled_by_default():
    """Verify WhatsApp adapter is disabled by default and returns DISABLED receipt."""
    channel = WhatsAppDeliveryChannel()
    assert channel.is_enabled() is False

    receipt = channel.send_report(
        recipient="9840123456",
        message="Test Report",
    )
    assert receipt.channel_name == "WhatsApp"
    assert receipt.status == DeliveryStatus.DISABLED
    assert "disabled by policy" in receipt.error_message


def test_phone_number_normalization_valid():
    """Verify phone normalization for standard Indian and international formats."""
    # 10 digits
    assert normalize_phone_number("9840123456") == "+919840123456"
    assert normalize_phone_number("8123456789") == "+918123456789"
    assert normalize_phone_number("7012345678") == "+917012345678"
    assert normalize_phone_number("6312345678") == "+916312345678"

    # With leading zero
    assert normalize_phone_number("09840123456") == "+919840123456"

    # With 91 country code prefix without plus
    assert normalize_phone_number("919840123456") == "+919840123456"

    # Full E.164
    assert normalize_phone_number("+919840123456") == "+919840123456"

    # With dashes and whitespace
    assert normalize_phone_number(" +91 98401-23456 ") == "+919840123456"
    assert normalize_phone_number("(98401) 23456") == "+919840123456"


def test_phone_number_normalization_invalid():
    """Verify invalid phone numbers raise ValueError."""
    # Empty / whitespace
    with pytest.raises(ValueError, match="non-empty string"):
        normalize_phone_number("")
    with pytest.raises(ValueError, match="non-empty string"):
        normalize_phone_number("   ")

    # Non-numeric
    with pytest.raises(ValueError, match="Invalid non-numeric"):
        normalize_phone_number("abcdefghij")

    # Invalid Indian mobile start digit (e.g. 1, 2, 3, 4, 5)
    with pytest.raises(ValueError, match="must start with 6, 7, 8, or 9"):
        normalize_phone_number("5840123456")

    # Too short or too long
    with pytest.raises(ValueError):
        normalize_phone_number("98401")
    with pytest.raises(ValueError):
        normalize_phone_number("+919840123456789012")


def test_attachment_validation_valid(tmp_path: Path):
    """Verify validation passes for existing valid image file within size limits."""
    test_img = tmp_path / "report.png"
    test_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"mock png content")

    validated = validate_attachment(test_img)
    assert validated.is_file()
    assert validated.name == "report.png"


def test_attachment_validation_missing_file(tmp_path: Path):
    """Verify non-existent file raises FileNotFoundError."""
    missing = tmp_path / "non_existent.png"
    with pytest.raises(FileNotFoundError, match="Attachment file not found"):
        validate_attachment(missing)


def test_attachment_validation_unsupported_extension(tmp_path: Path):
    """Verify unsupported file format raises ValueError."""
    bad_ext = tmp_path / "report.exe"
    bad_ext.write_bytes(b"content")
    with pytest.raises(ValueError, match="Unsupported attachment type"):
        validate_attachment(bad_ext)


def test_attachment_validation_empty_file(tmp_path: Path):
    """Verify 0-byte file raises ValueError."""
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="is empty"):
        validate_attachment(empty)


def test_attachment_validation_oversized_file(tmp_path: Path, monkeypatch):
    """Verify attachment exceeding max size raises ValueError."""
    test_img = tmp_path / "large.png"
    test_img.write_bytes(b"x" * 1024)

    # Mock stat to return size > limit
    monkeypatch.setattr(
        Path, "stat", lambda self, *args, **kwargs: type("Stat", (), {"st_size": MAX_ATTACHMENT_SIZE_BYTES + 100, "st_mode": 0o100644})()
    )
    with pytest.raises(ValueError, match="exceeds"):
        validate_attachment(test_img)


def test_format_executive_report_message():
    """Verify executive summary message structure and formatting."""
    msg = format_executive_report_message(
        report_date=date(2026, 10, 20),
        enrollment_total=450,
        closing_total=370,
        rejoining_rate_pct=21.45,
        additional_notes="All 9 showrooms fully reconciled.",
    )
    assert "POTHYS SWARNA MAHAL" in msg
    assert "20/10/2026" in msg
    assert "450" in msg
    assert "370" in msg
    assert "21.45%" in msg
    assert "All 9 showrooms fully reconciled." in msg
    assert "Confidential" in msg


def test_whatsapp_send_report_dry_run(tmp_path: Path):
    """Verify dry-run mode returns SENT receipt with simulated ID and zero live network requests."""
    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(enabled=True, dry_run=True)
    )
    assert channel.is_enabled() is True
    assert channel.dry_run is True

    test_img = tmp_path / "test.png"
    test_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"img data")

    receipt = channel.send_report(
        recipient="9840123456",
        message="Daily summary",
        attachments=[test_img],
    )
    assert receipt.channel_name == "WhatsApp"
    assert receipt.status == DeliveryStatus.SENT
    assert receipt.recipient == "+919840123456"
    assert receipt.message_id is not None
    assert receipt.message_id.startswith("wamid_sim_")


def test_whatsapp_send_report_invalid_recipient():
    """Verify delivery returns FAILED when recipient phone number is invalid."""
    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(enabled=True, dry_run=True)
    )
    receipt = channel.send_report(
        recipient="invalid_phone",
        message="Test",
    )
    assert receipt.status == DeliveryStatus.FAILED
    assert "Invalid" in receipt.error_message


def test_whatsapp_send_report_invalid_attachment(tmp_path: Path):
    """Verify delivery returns FAILED when attachment is missing or invalid."""
    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(enabled=True, dry_run=True)
    )
    receipt = channel.send_report(
        recipient="9840123456",
        message="Test",
        attachments=[tmp_path / "non_existent.png"],
    )
    assert receipt.status == DeliveryStatus.FAILED
    assert "Attachment validation failed" in receipt.error_message


def test_whatsapp_send_report_strict_read_only_guard(tmp_path: Path, monkeypatch):
    """Verify that STRICT_READ_ONLY=true forces dry-run simulation even if dry_run=False."""
    monkeypatch.setenv("STRICT_READ_ONLY", "true")
    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(enabled=True, dry_run=False)
    )

    test_img = tmp_path / "test.png"
    test_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"img data")

    receipt = channel.send_report(
        recipient="9840123456",
        message="Daily summary",
        attachments=[test_img],
    )
    assert receipt.status == DeliveryStatus.SENT
    assert receipt.message_id.startswith("wamid_sim_")


def test_whatsapp_send_report_missing_credentials_fails_closed(tmp_path: Path, monkeypatch):
    """Verify that live mode without credentials fails closed."""
    monkeypatch.setenv("STRICT_READ_ONLY", "false")
    monkeypatch.delenv("WHATSAPP_API_TOKEN", raising=False)
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)

    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(enabled=True, dry_run=False)
    )

    test_img = tmp_path / "test.png"
    test_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"img data")

    receipt = channel.send_report(
        recipient="9840123456",
        message="Daily summary",
        attachments=[test_img],
    )
    assert receipt.status == DeliveryStatus.FAILED
    assert "Missing WhatsApp API credentials" in receipt.error_message


def test_whatsapp_redact_phone_and_secret():
    """Verify phone and secret masking helper functions."""
    from app.delivery.whatsapp import redact_phone_number, redact_secret

    masked_phone = redact_phone_number("+919840123456")
    assert masked_phone == "+919******56"
    assert "401234" not in masked_phone

    masked_secret = redact_secret("EAABwzLxyz123456789")
    assert masked_secret.startswith("EAAB***")
    assert "[REDACTED]" in masked_secret
    assert "xyz123" not in masked_secret


def test_resolve_report_screenshots_success(tmp_path: Path):
    """Verify screenshot discovery and validation with exact date naming format."""
    from app.delivery.whatsapp import resolve_report_screenshots

    rep_date = date(2026, 10, 20)
    enr_img = tmp_path / "enrollment_report_20261020.png"
    cls_img = tmp_path / "closing_rejoining_report_20261020.png"
    enr_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"enr data")
    cls_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"cls data")

    val_enr, val_cls = resolve_report_screenshots(rep_date, screenshots_dir=tmp_path)
    assert val_enr == enr_img
    assert val_cls == cls_img


def test_resolve_report_screenshots_missing_file_raises(tmp_path: Path):
    """Verify FileNotFoundError is raised when either screenshot is missing."""
    from app.delivery.whatsapp import resolve_report_screenshots

    rep_date = date(2026, 10, 20)
    enr_img = tmp_path / "enrollment_report_20261020.png"
    enr_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"enr data")
    # closing image missing

    with pytest.raises(FileNotFoundError, match="Closing report screenshot not found"):
        resolve_report_screenshots(rep_date, screenshots_dir=tmp_path)


def test_whatsapp_duplicate_send_prevention(tmp_path: Path):
    """Verify idempotency blocks re-delivering the same report date to the same recipient unless force=True."""
    hist_file = tmp_path / "test_history.json"
    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(enabled=True, dry_run=True),
        history_file=hist_file,
    )

    test_img = tmp_path / "test.png"
    test_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"img data")
    rep_date = date(2026, 10, 20)
    recipient = "9840123456"

    # 1. First send: SUCCESS
    receipt1 = channel.send_report(
        recipient=recipient,
        message="Daily report",
        attachments=[test_img],
        report_date=rep_date,
    )
    assert receipt1.status == DeliveryStatus.SENT

    # 2. Duplicate send: BLOCKED with DUPLICATE status
    receipt2 = channel.send_report(
        recipient=recipient,
        message="Daily report",
        attachments=[test_img],
        report_date=rep_date,
        force=False,
    )
    assert receipt2.status == DeliveryStatus.DUPLICATE
    assert "Duplicate delivery blocked" in receipt2.error_message

    # 3. Forced resend: SUCCESS
    receipt3 = channel.send_report(
        recipient=recipient,
        message="Daily report",
        attachments=[test_img],
        report_date=rep_date,
        force=True,
    )
    assert receipt3.status == DeliveryStatus.SENT


def test_send_daily_screenshots_missing_recipient(tmp_path: Path):
    """Verify unified send_daily_screenshots fails if no recipient is provided or configured."""
    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(enabled=True, dry_run=True, recipient=None),
        history_file=tmp_path / "hist.json",
    )
    receipt = channel.send_daily_screenshots(
        report_date=date(2026, 10, 20),
        recipient="",
        screenshots_dir=tmp_path,
    )
    assert receipt.status == DeliveryStatus.FAILED
    assert "Phone number must be a non-empty string" in receipt.error_message


def test_send_daily_screenshots_dry_run_workflow(tmp_path: Path):
    """Verify unified send_daily_screenshots works end-to-end in dry-run mode."""
    hist_file = tmp_path / "hist.json"
    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(enabled=True, dry_run=True, recipient="9840123456"),
        history_file=hist_file,
    )

    rep_date = date(2026, 10, 20)
    enr_img = tmp_path / "enrollment_report_20261020.png"
    cls_img = tmp_path / "closing_rejoining_report_20261020.png"
    enr_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"enr")
    cls_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"cls")

    receipt = channel.send_daily_screenshots(
        report_date=rep_date,
        screenshots_dir=tmp_path,
        summary_notes="All 9 showrooms matched.",
    )
    assert receipt.status == DeliveryStatus.SENT
    assert receipt.recipient == "+919840123456"
    assert receipt.attachments_sent == 2
    assert receipt.message_id.startswith("wamid_sim_")


def test_whatsapp_meta_api_success_mocked(tmp_path: Path, monkeypatch):
    """Verify Meta Cloud API live request construction with mocked HTTP responses."""
    monkeypatch.setenv("STRICT_READ_ONLY", "false")

    test_img = tmp_path / "img.png"
    test_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test data")

    posted_urls = []
    posted_headers = []

    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json = json_data
            self.text = str(json_data)

        def json(self):
            return self._json

    def mock_post(url, headers=None, files=None, data=None, json=None, timeout=None):
        posted_urls.append(url)
        posted_headers.append(headers)
        if "/media" in url:
            return MockResponse(200, {"id": "meta_media_id_999"})
        elif "/messages" in url:
            return MockResponse(200, {"messages": [{"id": "wamid.HBgLMmocklive123"}]})
        return MockResponse(404, {})

    import requests
    monkeypatch.setattr(requests, "post", mock_post)

    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(
            enabled=True,
            dry_run=False,
            api_token="test_token_secret",
            phone_number_id="1234567890",
        ),
        history_file=tmp_path / "hist.json",
    )

    receipt = channel.send_report(
        recipient="+919840123456",
        message="Executive test",
        attachments=[test_img],
    )

    assert receipt.status == DeliveryStatus.SENT
    assert receipt.message_id == "wamid.HBgLMmocklive123"
    assert receipt.attachments_sent == 1
    assert any("/media" in u for u in posted_urls)
    assert any("/messages" in u for u in posted_urls)
    # Verify Authorization header used token properly
    assert any(h and h.get("Authorization") == "Bearer test_token_secret" for h in posted_headers)


def test_whatsapp_meta_api_error_response_handled_safely(tmp_path: Path, monkeypatch):
    """Verify Meta API error response is caught safely without leaking tokens."""
    monkeypatch.setenv("STRICT_READ_ONLY", "false")

    test_img = tmp_path / "img.png"
    test_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"data")

    class MockErrorResponse:
        def __init__(self):
            self.status_code = 400

        def json(self):
            return {
                "error": {
                    "message": "(#100) Param recipient invalid",
                    "type": "OAuthException",
                    "code": 100,
                    "error_subcode": 2388001,
                }
            }

    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **kw: MockErrorResponse())

    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(
            enabled=True,
            dry_run=False,
            api_token="super_secret_token_123",
            phone_number_id="123456",
        ),
        history_file=tmp_path / "hist.json",
    )

    receipt = channel.send_report(
        recipient="+919840123456",
        message="Test",
        attachments=[test_img],
    )

    assert receipt.status == DeliveryStatus.FAILED
    assert "Code 100" in receipt.error_message
    assert "super_secret_token" not in receipt.error_message


def test_whatsapp_meta_api_timeout_handled(tmp_path: Path, monkeypatch):
    """Verify timeout is caught and returns FAILED receipt gracefully."""
    monkeypatch.setenv("STRICT_READ_ONLY", "false")

    test_img = tmp_path / "img.png"
    test_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"data")

    from requests.exceptions import Timeout
    import requests

    def mock_timeout(*a, **kw):
        raise Timeout("Connection timed out")

    monkeypatch.setattr(requests, "post", mock_timeout)

    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(
            enabled=True,
            dry_run=False,
            api_token="token",
            phone_number_id="123456",
            timeout_seconds=5,
        ),
        history_file=tmp_path / "hist.json",
    )

    receipt = channel.send_report(
        recipient="+919840123456",
        message="Test",
        attachments=[test_img],
    )

    assert receipt.status == DeliveryStatus.FAILED
    assert "timed out" in receipt.error_message


def test_template_delivery_dry_run_success(tmp_path: Path):
    """Verify business-initiated template delivery works end-to-end in dry-run mode."""
    hist_file = tmp_path / "hist.json"
    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(
            enabled=True,
            dry_run=True,
            recipient="9840123456",
            delivery_mode="template",
            template_enrollment_name="psm_enrollment_report",
            template_closing_name="psm_closing_report",
        ),
        history_file=hist_file,
    )

    rep_date = date(2026, 10, 20)
    enr_img = tmp_path / "enrollment_report_20261020.png"
    cls_img = tmp_path / "closing_rejoining_report_20261020.png"
    enr_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"enr")
    cls_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"cls")

    receipt = channel.send_daily_screenshots(
        report_date=rep_date,
        screenshots_dir=tmp_path,
    )

    assert receipt.status == DeliveryStatus.SENT
    assert receipt.recipient == "+919840123456"
    assert receipt.attachments_sent == 2
    assert "wamid_sim_tmpl1_" in receipt.message_id
    assert "wamid_sim_tmpl2_" in receipt.message_id


def test_template_delivery_missing_templates_fails(tmp_path: Path):
    """Verify template delivery fails closed when template names are not configured."""
    hist_file = tmp_path / "hist.json"
    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(
            enabled=True,
            dry_run=True,
            recipient="9840123456",
            delivery_mode="template",
            template_enrollment_name="",
            template_closing_name="",
        ),
        history_file=hist_file,
    )

    rep_date = date(2026, 10, 20)
    enr_img = tmp_path / "enrollment_report_20261020.png"
    cls_img = tmp_path / "closing_rejoining_report_20261020.png"
    enr_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"enr")
    cls_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"cls")

    receipt = channel.send_daily_screenshots(
        report_date=rep_date,
        screenshots_dir=tmp_path,
    )

    assert receipt.status == DeliveryStatus.FAILED
    assert "Template delivery mode requires both template_enrollment_name and template_closing_name" in receipt.error_message


def test_template_delivery_live_mocked_meta_api(tmp_path: Path, monkeypatch):
    """Verify template delivery live request construction for both image-header templates."""
    monkeypatch.setenv("STRICT_READ_ONLY", "false")

    enr_img = tmp_path / "enrollment_report_20261020.png"
    cls_img = tmp_path / "closing_rejoining_report_20261020.png"
    enr_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"enr")
    cls_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"cls")

    posted_payloads = []
    posted_urls = []

    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json = json_data
            self.text = str(json_data)

        def json(self):
            return self._json

    media_counter = 0
    def mock_post(url, headers=None, files=None, data=None, json=None, timeout=None):
        nonlocal media_counter
        posted_urls.append(url)
        if json:
            posted_payloads.append(json)

        if "/media" in url:
            media_counter += 1
            return MockResponse(200, {"id": f"media_meta_id_{media_counter}"})
        elif "/messages" in url:
            tmpl_name = json.get("template", {}).get("name", "unknown")
            return MockResponse(200, {"messages": [{"id": f"wamid.tmpl_{tmpl_name}_ok"}]})
        return MockResponse(404, {})

    import requests
    monkeypatch.setattr(requests, "post", mock_post)

    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(
            enabled=True,
            dry_run=False,
            recipient="9840123456",
            api_token="test_secret_token",
            phone_number_id="1234567890",
            delivery_mode="template",
            template_enrollment_name="psm_enrollment_report",
            template_closing_name="psm_closing_report",
        ),
        history_file=tmp_path / "hist.json",
    )

    receipt = channel.send_daily_screenshots(
        report_date=date(2026, 10, 20),
        screenshots_dir=tmp_path,
    )

    assert receipt.status == DeliveryStatus.SENT
    assert receipt.attachments_sent == 2
    assert "wamid.tmpl_psm_enrollment_report_ok" in receipt.message_id
    assert "wamid.tmpl_psm_closing_report_ok" in receipt.message_id

    # Verify template payloads were constructed accurately
    assert len(posted_payloads) == 2
    enr_payload = posted_payloads[0]
    assert enr_payload["type"] == "template"
    assert enr_payload["template"]["name"] == "psm_enrollment_report"
    assert enr_payload["template"]["components"][0]["parameters"][0]["image"]["id"] == "media_meta_id_1"

    cls_payload = posted_payloads[1]
    assert cls_payload["type"] == "template"
    assert cls_payload["template"]["name"] == "psm_closing_report"
    assert cls_payload["template"]["components"][0]["parameters"][0]["image"]["id"] == "media_meta_id_2"


def test_template_delivery_mocked_meta_api_failure_handled(tmp_path: Path, monkeypatch):
    """Verify template API rejection (e.g. template unapproved) fails closed safely."""
    monkeypatch.setenv("STRICT_READ_ONLY", "false")

    enr_img = tmp_path / "enrollment_report_20261020.png"
    cls_img = tmp_path / "closing_rejoining_report_20261020.png"
    enr_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"enr")
    cls_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"cls")

    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json = json_data
            self.text = str(json_data)

        def json(self):
            return self._json

    def mock_post(url, headers=None, files=None, data=None, json=None, timeout=None):
        if "/media" in url:
            return MockResponse(200, {"id": "media_meta_id_1"})
        elif "/messages" in url:
            return MockResponse(400, {
                "error": {
                    "message": "Template 'psm_enrollment_report' is not approved in language en_US",
                    "type": "OAuthException",
                    "code": 132001,
                }
            })
        return MockResponse(404, {})

    import requests
    monkeypatch.setattr(requests, "post", mock_post)

    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(
            enabled=True,
            dry_run=False,
            recipient="9840123456",
            api_token="super_secret_token_123",
            phone_number_id="123456",
            delivery_mode="template",
        ),
        history_file=tmp_path / "hist.json",
    )

    receipt = channel.send_daily_screenshots(
        report_date=date(2026, 10, 20),
        screenshots_dir=tmp_path,
    )

    assert receipt.status == DeliveryStatus.FAILED
    assert "Code 132001" in receipt.error_message
    assert "super_secret_token_123" not in receipt.error_message


def test_template_delivery_duplicate_prevention(tmp_path: Path):
    """Verify template delivery records history and prevents duplicate sends."""
    hist_file = tmp_path / "hist.json"
    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(
            enabled=True,
            dry_run=True,
            recipient="9840123456",
            delivery_mode="template",
        ),
        history_file=hist_file,
    )

    rep_date = date(2026, 10, 20)
    enr_img = tmp_path / "enrollment_report_20261020.png"
    cls_img = tmp_path / "closing_rejoining_report_20261020.png"
    enr_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"enr")
    cls_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"cls")

    # 1. First delivery
    receipt1 = channel.send_daily_screenshots(report_date=rep_date, screenshots_dir=tmp_path)
    assert receipt1.status == DeliveryStatus.SENT

    # 2. Second delivery without force -> DUPLICATE
    receipt2 = channel.send_daily_screenshots(report_date=rep_date, screenshots_dir=tmp_path, force=False)
    assert receipt2.status == DeliveryStatus.DUPLICATE
    assert "Duplicate delivery blocked" in receipt2.error_message

    # 3. Third delivery with force -> SENT
    receipt3 = channel.send_daily_screenshots(report_date=rep_date, screenshots_dir=tmp_path, force=True)
    assert receipt3.status == DeliveryStatus.SENT


def test_template_delivery_partial_failure_and_recovery_prevents_duplicate_send(tmp_path: Path, monkeypatch):
    """
    Verify delivery history safety if the first image sends successfully but the second fails.
    During recovery retry:
    - Template 1 (already sent) must NOT be re-dispatched.
    - Template 2 (previously failed) must be sent.
    - Delivery history completes with status SENT and combined message IDs.
    - Subsequent runs are blocked as DUPLICATE.
    """
    monkeypatch.setenv("STRICT_READ_ONLY", "false")
    hist_file = tmp_path / "hist.json"

    rep_date = date(2026, 10, 20)
    enr_img = tmp_path / "enrollment_report_20261020.png"
    cls_img = tmp_path / "closing_rejoining_report_20261020.png"
    enr_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"enr")
    cls_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"cls")

    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json = json_data
            self.text = str(json_data)

        def json(self):
            return self._json

    media_counter = 0
    fail_second_template = True
    dispatched_templates = []

    def mock_post(url, headers=None, files=None, data=None, json=None, timeout=None):
        nonlocal media_counter, fail_second_template
        if "/media" in url:
            media_counter += 1
            return MockResponse(200, {"id": f"media_meta_id_{media_counter}"})
        elif "/messages" in url:
            tmpl_name = json.get("template", {}).get("name", "unknown")
            if tmpl_name == "psm_closing_report" and fail_second_template:
                return MockResponse(500, {"error": {"message": "Meta internal error on closing report", "code": 500}})
            dispatched_templates.append(tmpl_name)
            return MockResponse(200, {"messages": [{"id": f"wamid.{tmpl_name}_ok"}]})
        return MockResponse(404, {})

    import requests
    monkeypatch.setattr(requests, "post", mock_post)

    channel = WhatsAppDeliveryChannel(
        config=WhatsAppConfig(
            enabled=True,
            dry_run=False,
            recipient="9840123456",
            api_token="test_token",
            phone_number_id="123456",
            delivery_mode="template",
            template_enrollment_name="psm_enrollment_report",
            template_closing_name="psm_closing_report",
        ),
        history_file=hist_file,
    )

    # 1. First attempt: Template 1 succeeds, Template 2 fails
    receipt1 = channel.send_daily_screenshots(report_date=rep_date, screenshots_dir=tmp_path)
    assert receipt1.status == DeliveryStatus.FAILED
    assert "Closing template send failed" in receipt1.error_message
    assert dispatched_templates == ["psm_enrollment_report"]

    # Verify history recorded partial progress
    entry = channel._history.get_latest_delivery(rep_date, "+919840123456")
    assert entry is not None
    assert entry["status"] == "PARTIAL"
    assert entry["attachments"] == ["enrollment_report_20261020.png"]
    assert "wamid.psm_enrollment_report_ok" in entry["message_id"]

    # Verify is_already_delivered is False (recovery should be permitted)
    assert channel._history.is_already_delivered(rep_date, "+919840123456") is False

    # 2. Recovery attempt: Meta server recovered
    fail_second_template = False
    dispatched_templates.clear()

    receipt2 = channel.send_daily_screenshots(report_date=rep_date, screenshots_dir=tmp_path)
    assert receipt2.status == DeliveryStatus.SENT
    # CRITICAL CHECK: Template 1 was NOT re-sent! Only Template 2 was dispatched.
    assert dispatched_templates == ["psm_closing_report"]
    assert "wamid.psm_enrollment_report_ok" in receipt2.message_id
    assert "wamid.psm_closing_report_ok" in receipt2.message_id

    # Verify history is now marked fully SENT
    entry_final = channel._history.get_latest_delivery(rep_date, "+919840123456")
    assert entry_final["status"] == DeliveryStatus.SENT.value
    assert set(entry_final["attachments"]) == {"enrollment_report_20261020.png", "closing_rejoining_report_20261020.png"}
    assert channel._history.is_already_delivered(rep_date, "+919840123456") is True

    # 3. Third attempt without force: blocked as DUPLICATE
    receipt3 = channel.send_daily_screenshots(report_date=rep_date, screenshots_dir=tmp_path)
    assert receipt3.status == DeliveryStatus.DUPLICATE



