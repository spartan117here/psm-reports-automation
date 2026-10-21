"""
WhatsApp delivery adapter for Pothys Swarna Mahal executive reports.

SECURITY NOTICE:
WhatsApp delivery is STRICTLY DISABLED by default (enabled=False).
It cannot send any external messages unless:
1. whatsapp.enabled is set to true in config or WHATSAPP_ENABLED=true in .env
2. Valid Meta WhatsApp Cloud API credentials are provided in environment
3. dry_run is explicitly set to false
4. STRICT_READ_ONLY is not active

DELIVERY MODES:
1. "template" (DEFAULT for Meta Business Cloud API outbound messages):
   Required for business-initiated messages outside the 24-hour customer service window.
   Since one template image header cannot carry multiple images, delivers two pre-approved
   image-header templates:
   - Template 1: Enrollment Report (header: enrollment_report_YYYYMMDD.png)
   - Template 2: Closing & Rejoining Report (header: closing_rejoining_report_YYYYMMDD.png)
2. "session" (Free-form media messages):
   Only permitted if the recipient initiated a conversation within the last 24 hours.
"""

from datetime import date, datetime
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import requests
from requests.exceptions import RequestException, Timeout

from app.core.config import WhatsAppConfig
from app.delivery.base import DeliveryChannel, DeliveryReceipt, DeliveryStatus

logger = logging.getLogger("pothys_reporting")

MAX_ATTACHMENT_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB Meta Cloud API limit
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}


def redact_phone_number(phone: str) -> str:
    """Mask phone number for safe logging (e.g. +919840123456 -> +9198******56)."""
    if not phone or not isinstance(phone, str):
        return "[UNSPECIFIED]"
    clean = phone.strip()
    if len(clean) >= 8:
        return f"{clean[:4]}******{clean[-2:]}"
    return "***"


def redact_secret(secret: Optional[str]) -> str:
    """Mask secret/token for safe logging."""
    if not secret:
        return "[NONE]"
    if len(secret) > 8:
        return f"{secret[:4]}***[REDACTED]"
    return "***[REDACTED]"


def normalize_phone_number(phone: str) -> str:
    """
    Validate and normalize recipient phone number to E.164 format.

    Supports:
    - 10-digit Indian mobile numbers (e.g., 9840123456) -> +919840123456
    - 11-digit with leading zero (e.g., 09840123456) -> +919840123456
    - 12-digit with country code (e.g., 919840123456) -> +919840123456
    - International E.164 (e.g., +919840123456) -> +919840123456
    """
    if not phone or not isinstance(phone, str) or not phone.strip():
        raise ValueError("Phone number must be a non-empty string.")

    cleaned = re.sub(r"[\s\-\(\)\.]+", "", phone.strip())

    if cleaned.startswith("+"):
        digits = cleaned[1:]
        if not digits.isdigit():
            raise ValueError(f"Invalid characters in phone number: '{phone}'")
        if len(digits) < 10 or len(digits) > 15:
            raise ValueError(f"Phone number length {len(digits)} out of E.164 range (10-15 digits): '{phone}'")
        return cleaned

    if not cleaned.isdigit():
        raise ValueError(f"Invalid non-numeric phone number: '{phone}'")

    if len(cleaned) == 10:
        if cleaned[0] not in "6789":
            raise ValueError(f"Indian mobile numbers must start with 6, 7, 8, or 9: '{phone}'")
        return f"+91{cleaned}"
    elif len(cleaned) == 11 and cleaned.startswith("0"):
        mobile = cleaned[1:]
        if mobile[0] not in "6789":
            raise ValueError(f"Indian mobile numbers must start with 6, 7, 8, or 9: '{phone}'")
        return f"+91{mobile}"
    elif len(cleaned) == 12 and cleaned.startswith("91"):
        mobile = cleaned[2:]
        if mobile[0] not in "6789":
            raise ValueError(f"Indian mobile numbers must start with 6, 7, 8, or 9: '{phone}'")
        return f"+{cleaned}"
    else:
        raise ValueError(f"Unrecognized phone number format: '{phone}' (expected 10 digits or E.164)")


def validate_attachment(path: Path) -> Path:
    """Validate that attachment exists, has supported format, and is within size limits."""
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Attachment file not found: {resolved}")

    suffix = resolved.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported attachment type '{suffix}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    file_size = resolved.stat().st_size
    if file_size > MAX_ATTACHMENT_SIZE_BYTES:
        raise ValueError(
            f"Attachment '{resolved.name}' ({file_size:,} bytes) exceeds {MAX_ATTACHMENT_SIZE_BYTES:,} byte limit."
        )
    if file_size == 0:
        raise ValueError(f"Attachment '{resolved.name}' is empty (0 bytes).")

    return resolved


def resolve_report_screenshots(
    report_date: date,
    screenshots_dir: Optional[Path] = None,
) -> Tuple[Path, Path]:
    """
    Resolve and validate the two required daily executive report screenshots.

    Naming convention:
    - enrollment_report_{YYYYMMDD}.png
    - closing_rejoining_report_{YYYYMMDD}.png

    Raises FileNotFoundError if either screenshot is missing or not a file.
    """
    base_dir = Path(screenshots_dir or "data/screenshots").resolve()
    date_tag = report_date.strftime("%Y%m%d")

    enrollment_path = base_dir / f"enrollment_report_{date_tag}.png"
    closing_path = base_dir / f"closing_rejoining_report_{date_tag}.png"

    if not enrollment_path.is_file():
        raise FileNotFoundError(
            f"Enrollment report screenshot not found for date {report_date.isoformat()} at: {enrollment_path}"
        )
    if not closing_path.is_file():
        raise FileNotFoundError(
            f"Closing report screenshot not found for date {report_date.isoformat()} at: {closing_path}"
        )

    val_enrollment = validate_attachment(enrollment_path)
    val_closing = validate_attachment(closing_path)

    return val_enrollment, val_closing


def format_executive_report_message(
    report_date: date,
    enrollment_total: Optional[int] = None,
    closing_total: Optional[int] = None,
    rejoining_rate_pct: Optional[float] = None,
    additional_notes: Optional[str] = None,
) -> str:
    """Format standard Pothys Swarna Mahal WhatsApp executive summary text."""
    date_str = report_date.strftime("%d/%m/%Y")
    lines = [
        "📊 *POTHYS SWARNA MAHAL*",
        f"*Daily Executive Performance Report — {date_str}*",
        "━" * 32,
    ]

    if enrollment_total is not None:
        lines.append(f"• *New Scheme Enrollments:* {enrollment_total:,}")
    if closing_total is not None:
        lines.append(f"• *Scheme Closures:* {closing_total:,}")
    if rejoining_rate_pct is not None:
        lines.append(f"• *Overall Rejoining Rate:* {rejoining_rate_pct:.2f}%")

    if additional_notes:
        lines.append("")
        lines.append(f"ℹ️ {additional_notes}")

    lines.append("━" * 32)
    lines.append("📎 *Verified Executive Screenshots Attached Below*")
    lines.append("🔒 _Confidential — Internal Management Distribution Only_")

    return "\n".join(lines)


class DeliveryHistory:
    """Persistent delivery history tracker for idempotency and duplicate prevention."""

    def __init__(self, history_file: Path):
        self.history_file = Path(history_file)

    def _load(self) -> List[Dict[str, Any]]:
        if not self.history_file.is_file():
            return []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("deliveries", [])
        except Exception as e:
            logger.warning(f"Could not load delivery history from {self.history_file}: {e}")
            return []

    def _save(self, deliveries: List[Dict[str, Any]]) -> None:
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump({"deliveries": deliveries}, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save delivery history to {self.history_file}: {e}")

    def is_already_delivered(self, report_date: date, recipient: str) -> bool:
        """Check if report_date has already been successfully delivered to recipient."""
        date_iso = report_date.isoformat()
        try:
            norm_recip = normalize_phone_number(recipient)
        except Exception:
            norm_recip = recipient

        for entry in self._load():
            if (
                entry.get("report_date") == date_iso
                and entry.get("recipient") == norm_recip
                and entry.get("status") == DeliveryStatus.SENT.value
            ):
                return True
        return False

    def get_latest_delivery(self, report_date: date, recipient: str) -> Optional[Dict[str, Any]]:
        """Retrieve latest delivery attempt for report_date and recipient."""
        date_iso = report_date.isoformat()
        try:
            norm_recip = normalize_phone_number(recipient)
        except Exception:
            norm_recip = recipient

        for entry in reversed(self._load()):
            if entry.get("report_date") == date_iso and entry.get("recipient") == norm_recip:
                return entry
        return None

    def record_delivery(
        self,
        report_date: date,
        recipient: str,
        message_id: str,
        attachments: List[str],
        status: str = DeliveryStatus.SENT.value,
        channel: str = "WhatsApp",
    ) -> None:
        """Record delivery attempt in persistent history."""
        try:
            norm_recip = normalize_phone_number(recipient)
        except Exception:
            norm_recip = recipient

        deliveries = self._load()
        deliveries.append(
            {
                "report_date": report_date.isoformat(),
                "recipient": norm_recip,
                "channel": channel,
                "status": status,
                "message_id": message_id,
                "attachments": attachments,
                "timestamp": datetime.now().isoformat(),
            }
        )
        self._save(deliveries)


class WhatsAppDeliveryChannel(DeliveryChannel):
    """
    Enterprise WhatsApp delivery adapter for Meta WhatsApp Business Cloud API.

    Strictly disabled by default (enabled=False).
    Supports dry-run simulations, phone number normalization, attachment verification,
    template-based delivery (business-initiated), session-based delivery (within 24h window),
    and Meta Cloud API integration behind strict safety guards.
    """

    def __init__(
        self,
        config: Optional[WhatsAppConfig] = None,
        enabled: Optional[bool] = None,
        dry_run: Optional[bool] = None,
        recipient: Optional[str] = None,
        api_token: Optional[str] = None,
        phone_number_id: Optional[str] = None,
        delivery_mode: Optional[str] = None,
        template_enrollment_name: Optional[str] = None,
        template_closing_name: Optional[str] = None,
        template_language: Optional[str] = None,
        history_file: Optional[Path] = None,
    ):
        self._config = config or WhatsAppConfig()
        if enabled is not None:
            self._config.enabled = enabled
        if dry_run is not None:
            self._config.dry_run = dry_run
        if recipient is not None:
            self._config.recipient = recipient

        self._phone_number_id = (
            phone_number_id
            or self._config.phone_number_id
            or os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        )
        self._api_token = (
            api_token
            or self._config.api_token
            or os.getenv("WHATSAPP_API_TOKEN")
        )
        self._api_version = self._config.api_version or "v21.0"
        self._timeout = self._config.timeout_seconds or 30

        # Delivery mode: "template" (default) or "session"
        self._delivery_mode = (
            delivery_mode
            or os.getenv("WHATSAPP_DELIVERY_MODE")
            or self._config.delivery_mode
            or "template"
        ).lower()

        # Two distinct templates for Enrollment and Closing report images
        if template_enrollment_name is not None:
            self._template_enrollment_name = template_enrollment_name
        elif os.getenv("WHATSAPP_ENROLLMENT_TEMPLATE") is not None:
            self._template_enrollment_name = os.getenv("WHATSAPP_ENROLLMENT_TEMPLATE")
        elif getattr(self._config, "template_enrollment_name", None) is not None:
            self._template_enrollment_name = self._config.template_enrollment_name
        elif self._config.template_name is not None:
            self._template_enrollment_name = self._config.template_name
        else:
            self._template_enrollment_name = "psm_enrollment_report"

        if template_closing_name is not None:
            self._template_closing_name = template_closing_name
        elif os.getenv("WHATSAPP_CLOSING_TEMPLATE") is not None:
            self._template_closing_name = os.getenv("WHATSAPP_CLOSING_TEMPLATE")
        elif getattr(self._config, "template_closing_name", None) is not None:
            self._template_closing_name = self._config.template_closing_name
        else:
            self._template_closing_name = "psm_closing_report"

        self._template_language = (
            template_language
            or os.getenv("WHATSAPP_TEMPLATE_LANGUAGE")
            or self._config.template_language
            or "en_US"
        )

        hist_path = history_file or self._config.history_file or Path("data/delivery/delivery_history.json")
        self._history = DeliveryHistory(hist_path)

    def is_enabled(self) -> bool:
        """Check if WhatsApp delivery is active."""
        return self._config.enabled

    @property
    def dry_run(self) -> bool:
        """Check if dry-run mode is active."""
        return self._config.dry_run

    @property
    def delivery_mode(self) -> str:
        """Active delivery mode: 'template' or 'session'."""
        return self._delivery_mode

    def send_report(
        self,
        recipient: str,
        message: str,
        attachments: Optional[List[Path]] = None,
        report_date: Optional[date] = None,
        force: bool = False,
    ) -> DeliveryReceipt:
        """
        Deliver executive report text and screenshot attachments to recipient.

        Safety gates:
        1. If not enabled -> returns DISABLED receipt.
        2. Validates recipient phone number format.
        3. Validates all attachment files.
        4. If report_date provided and not force: checks duplicate-send history.
        5. If dry_run=True or STRICT_READ_ONLY=True -> returns simulated SENT receipt without external calls.
        6. Live mode -> dispatches Meta Cloud API calls.
        """
        if not self.is_enabled():
            logger.info("WhatsApp delivery is disabled. Skipping delivery.")
            return DeliveryReceipt(
                channel_name="WhatsApp",
                status=DeliveryStatus.DISABLED,
                recipient=recipient,
                error_message="Channel disabled by policy pending Phase 11 approval",
            )

        # 1. Normalize and validate recipient
        if not recipient or not str(recipient).strip():
            logger.error("Recipient phone validation failed: Recipient is empty or not configured.")
            return DeliveryReceipt(
                channel_name="WhatsApp",
                status=DeliveryStatus.FAILED,
                recipient="",
                error_message="Phone number must be a non-empty string.",
            )

        try:
            normalized_recipient = normalize_phone_number(recipient)
        except ValueError as e:
            logger.error(f"Recipient phone validation failed: {e}")
            return DeliveryReceipt(
                channel_name="WhatsApp",
                status=DeliveryStatus.FAILED,
                recipient=recipient,
                error_message=str(e),
            )

        # 2. Validate attachments
        validated_attachments: List[Path] = []
        if attachments:
            for att in attachments:
                try:
                    val_path = validate_attachment(att)
                    validated_attachments.append(val_path)
                except Exception as e:
                    logger.error(f"Attachment validation failed for '{att}': {e}")
                    return DeliveryReceipt(
                        channel_name="WhatsApp",
                        status=DeliveryStatus.FAILED,
                        recipient=normalized_recipient,
                        error_message=f"Attachment validation failed: {e}",
                    )

        # 3. Duplicate-send check
        if report_date and not force:
            if self._history.is_already_delivered(report_date, normalized_recipient):
                masked_phone = redact_phone_number(normalized_recipient)
                logger.warning(
                    f"Duplicate delivery blocked: report {report_date.isoformat()} already delivered to {masked_phone}."
                )
                return DeliveryReceipt(
                    channel_name="WhatsApp",
                    status=DeliveryStatus.DUPLICATE,
                    recipient=normalized_recipient,
                    error_message=f"Duplicate delivery blocked: report {report_date.isoformat()} already delivered to recipient. Use --force to override.",
                )

        # 4. Dry-run guard or strict read-only guard
        strict_read_only = os.getenv("STRICT_READ_ONLY", "false").lower() in ("true", "1", "yes")
        if self._config.dry_run or strict_read_only:
            simulated_id = f"wamid_sim_{uuid4().hex[:16]}"
            masked_phone = redact_phone_number(normalized_recipient)
            att_names = [a.name for a in validated_attachments]
            logger.info(
                f"[DRY-RUN] WhatsApp message simulated successfully to {masked_phone} "
                f"(mode: {self._delivery_mode}, attachments: {len(validated_attachments)}, message_id: {simulated_id}). "
                "Zero external network calls performed."
            )
            if report_date:
                self._history.record_delivery(
                    report_date=report_date,
                    recipient=normalized_recipient,
                    message_id=simulated_id,
                    attachments=att_names,
                    status=DeliveryStatus.SENT.value,
                )
            return DeliveryReceipt(
                channel_name="WhatsApp",
                status=DeliveryStatus.SENT,
                recipient=normalized_recipient,
                message_id=simulated_id,
                attachments_sent=len(validated_attachments),
            )

        # 5. Live Production Meta Cloud API execution
        if not self._api_token or not self._phone_number_id:
            logger.error("WhatsApp credentials missing in environment (WHATSAPP_API_TOKEN / WHATSAPP_PHONE_NUMBER_ID).")
            return DeliveryReceipt(
                channel_name="WhatsApp",
                status=DeliveryStatus.FAILED,
                recipient=normalized_recipient,
                error_message="Missing WhatsApp API credentials in environment",
            )

        try:
            return self._execute_session_delivery(
                recipient=normalized_recipient,
                message=message,
                attachments=validated_attachments,
                report_date=report_date,
            )
        except Exception as e:
            logger.error(f"WhatsApp Meta Cloud API delivery failed: {e}")
            return DeliveryReceipt(
                channel_name="WhatsApp",
                status=DeliveryStatus.FAILED,
                recipient=normalized_recipient,
                error_message=f"Meta Cloud API error: {e}",
            )

    def _execute_session_delivery(
        self,
        recipient: str,
        message: str,
        attachments: List[Path],
        report_date: Optional[date] = None,
    ) -> DeliveryReceipt:
        """Dispatch free-form media message delivery within an active 24h customer session window."""
        digits_to = recipient.lstrip("+")
        base_url = f"https://graph.facebook.com/{self._api_version}/{self._phone_number_id}"
        headers = {
            "Authorization": f"Bearer {self._api_token}",
        }

        sent_message_ids: List[str] = []

        if attachments:
            for idx, att in enumerate(attachments):
                # 1. Upload media
                media_id = self._upload_media(att)

                # 2. Send image message (attach executive message as caption of the first image)
                caption = message if idx == 0 else f"📎 Verified Report Screenshot: {att.name}"
                msg_url = f"{base_url}/messages"
                msg_payload = {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": digits_to,
                    "type": "image",
                    "image": {
                        "id": media_id,
                        "caption": caption,
                    },
                }

                try:
                    resp = requests.post(
                        msg_url,
                        headers={"Authorization": f"Bearer {self._api_token}", "Content-Type": "application/json"},
                        json=msg_payload,
                        timeout=self._timeout,
                    )
                except Timeout:
                    raise TimeoutError(f"Message send timed out for '{att.name}' after {self._timeout}s")
                except RequestException as e:
                    raise RuntimeError(f"Message send request failed: {e}")

                if resp.status_code not in (200, 201):
                    err_detail = self._parse_api_error(resp)
                    raise RuntimeError(f"Message send failed ({resp.status_code}): {err_detail}")

                msg_id = resp.json().get("messages", [{}])[0].get("id")
                if msg_id:
                    sent_message_ids.append(msg_id)
        else:
            # Text-only message
            msg_url = f"{base_url}/messages"
            msg_payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": digits_to,
                "type": "text",
                "text": {"preview_url": False, "body": message},
            }
            try:
                resp = requests.post(
                    msg_url,
                    headers={"Authorization": f"Bearer {self._api_token}", "Content-Type": "application/json"},
                    json=msg_payload,
                    timeout=self._timeout,
                )
            except Timeout:
                raise TimeoutError(f"Text message send timed out after {self._timeout}s")
            except RequestException as e:
                raise RuntimeError(f"Text message send request failed: {e}")

            if resp.status_code not in (200, 201):
                err_detail = self._parse_api_error(resp)
                raise RuntimeError(f"Text message send failed ({resp.status_code}): {err_detail}")

            msg_id = resp.json().get("messages", [{}])[0].get("id")
            if msg_id:
                sent_message_ids.append(msg_id)

        primary_id = sent_message_ids[0] if sent_message_ids else "wamid_live_unknown"
        masked_phone = redact_phone_number(recipient)
        logger.info(
            f"WhatsApp session report successfully delivered to {masked_phone} "
            f"(attachments: {len(attachments)}, primary_message_id: {primary_id})."
        )

        if report_date:
            self._history.record_delivery(
                report_date=report_date,
                recipient=recipient,
                message_id=primary_id,
                attachments=[a.name for a in attachments],
                status=DeliveryStatus.SENT.value,
            )

        return DeliveryReceipt(
            channel_name="WhatsApp",
            status=DeliveryStatus.SENT,
            recipient=recipient,
            message_id=primary_id,
            attachments_sent=len(attachments),
        )

    def _execute_template_delivery(
        self,
        recipient: str,
        enrollment_img: Path,
        closing_img: Path,
        report_date: date,
        parameters: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> DeliveryReceipt:
        """
        Dispatch two pre-approved image-header template messages for business-initiated delivery.

        Template 1: Enrollment Report (template_enrollment_name)
        Template 2: Closing Report (template_closing_name)

        Resilience & Duplicate Prevention during Recovery:
        If Template 1 previously succeeded but Template 2 failed (recorded as status='PARTIAL'),
        recovery will skip re-sending Template 1 and only dispatch Template 2.
        """
        digits_to = recipient.lstrip("+")
        base_url = f"https://graph.facebook.com/{self._api_version}/{self._phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
        }

        date_str = report_date.strftime("%d/%m/%Y")
        sent_message_ids: List[str] = []
        already_sent_attachments: List[str] = []

        if not force:
            prev_entry = self._history.get_latest_delivery(report_date, recipient)
            if prev_entry and prev_entry.get("status") == "PARTIAL":
                already_sent_attachments = prev_entry.get("attachments", [])
                if prev_entry.get("message_id"):
                    sent_message_ids.append(prev_entry["message_id"])

        # 1. Upload Enrollment media and send Template 1
        if enrollment_img.name in already_sent_attachments:
            logger.info(
                f"Skipping Template 1 ({self._template_enrollment_name}): '{enrollment_img.name}' was already "
                f"successfully dispatched in a prior attempt (preventing duplicate sends during recovery)."
            )
        else:
            logger.info(f"Uploading enrollment image header to Meta: {enrollment_img.name}")
            media_id_enr = self._upload_media(enrollment_img)

            payload_enr = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": digits_to,
                "type": "template",
                "template": {
                    "name": self._template_enrollment_name,
                    "language": {"code": self._template_language},
                    "components": [
                        {
                            "type": "header",
                            "parameters": [
                                {"type": "image", "image": {"id": media_id_enr}}
                            ],
                        },
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": date_str}
                            ],
                        },
                    ],
                },
            }

            try:
                resp1 = requests.post(base_url, headers=headers, json=payload_enr, timeout=self._timeout)
            except Timeout:
                raise TimeoutError(f"Template send timed out for Enrollment Report after {self._timeout}s")
            except RequestException as e:
                raise RuntimeError(f"Enrollment template request failed: {e}")

            if resp1.status_code not in (200, 201):
                err_detail = self._parse_api_error(resp1)
                raise RuntimeError(f"Enrollment template send failed ({resp1.status_code}): {err_detail}")

            msg_id_enr = resp1.json().get("messages", [{}])[0].get("id")
            if msg_id_enr:
                sent_message_ids.append(msg_id_enr)
                # Immediately persist partial delivery so that if Template 2 fails, Template 1 is not resent
                self._history.record_delivery(
                    report_date=report_date,
                    recipient=recipient,
                    message_id=msg_id_enr,
                    attachments=[enrollment_img.name],
                    status="PARTIAL",
                )

        # 2. Upload Closing media and send Template 2
        if closing_img.name in already_sent_attachments and not force:
            logger.info(
                f"Skipping Template 2 ({self._template_closing_name}): '{closing_img.name}' was already "
                f"successfully dispatched in a prior attempt."
            )
        else:
            logger.info(f"Uploading closing image header to Meta: {closing_img.name}")
            media_id_cls = self._upload_media(closing_img)

            payload_cls = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": digits_to,
                "type": "template",
                "template": {
                    "name": self._template_closing_name,
                    "language": {"code": self._template_language},
                    "components": [
                        {
                            "type": "header",
                            "parameters": [
                                {"type": "image", "image": {"id": media_id_cls}}
                            ],
                        },
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": date_str}
                            ],
                        },
                    ],
                },
            }

            try:
                resp2 = requests.post(base_url, headers=headers, json=payload_cls, timeout=self._timeout)
            except Timeout:
                raise TimeoutError(f"Template send timed out for Closing Report after {self._timeout}s")
            except RequestException as e:
                raise RuntimeError(f"Closing template request failed: {e}")

            if resp2.status_code not in (200, 201):
                err_detail = self._parse_api_error(resp2)
                raise RuntimeError(f"Closing template send failed ({resp2.status_code}): {err_detail}")

            msg_id_cls = resp2.json().get("messages", [{}])[0].get("id")
            if msg_id_cls:
                sent_message_ids.append(msg_id_cls)

        combined_id = ",".join(sent_message_ids)
        masked_phone = redact_phone_number(recipient)
        logger.info(
            f"WhatsApp template reports successfully delivered to {masked_phone} "
            f"(templates: '{self._template_enrollment_name}', '{self._template_closing_name}', message_ids: {combined_id})."
        )

        self._history.record_delivery(
            report_date=report_date,
            recipient=recipient,
            message_id=combined_id,
            attachments=[enrollment_img.name, closing_img.name],
            status=DeliveryStatus.SENT.value,
        )

        return DeliveryReceipt(
            channel_name="WhatsApp",
            status=DeliveryStatus.SENT,
            recipient=recipient,
            message_id=combined_id,
            attachments_sent=2,
        )

    def _upload_media(self, file_path: Path) -> str:
        """Upload image to Meta media server and return media id."""
        media_url = f"https://graph.facebook.com/{self._api_version}/{self._phone_number_id}/media"
        headers = {"Authorization": f"Bearer {self._api_token}"}
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, "image/png")}
            data = {"messaging_product": "whatsapp", "type": "image/png"}
            try:
                resp = requests.post(media_url, headers=headers, files=files, data=data, timeout=self._timeout)
            except Timeout:
                raise TimeoutError(f"Upload timed out for attachment '{file_path.name}' after {self._timeout}s")
            except RequestException as e:
                raise RuntimeError(f"Media upload request failed: {e}")

        if resp.status_code not in (200, 201):
            err_detail = self._parse_api_error(resp)
            raise RuntimeError(f"Media upload failed ({resp.status_code}): {err_detail}")

        media_id = resp.json().get("id")
        if not media_id:
            raise RuntimeError(f"Meta API response missing media id: {resp.text}")
        return media_id

    def _parse_api_error(self, resp: requests.Response) -> str:
        """Safely extract error message from Meta API response without leaking secrets."""
        try:
            data = resp.json()
            err = data.get("error", {})
            msg = err.get("message", "Unknown error")
            code = err.get("code", resp.status_code)
            subcode = err.get("error_subcode", "")
            subcode_str = f", subcode: {subcode}" if subcode else ""
            return f"Code {code}{subcode_str}: {msg}"
        except Exception:
            return f"HTTP {resp.status_code}"

    def send_daily_screenshots(
        self,
        report_date: date,
        recipient: Optional[str] = None,
        force: bool = False,
        screenshots_dir: Optional[Path] = None,
        summary_notes: Optional[str] = None,
    ) -> DeliveryReceipt:
        """
        Unified workflow to discover, validate, and deliver the two daily report screenshots.

        Compliance behavior:
        - If delivery_mode == 'template' (default):
          Delivers two pre-approved image-header template messages (enrollment + closing).
          Validates that template names are configured.
        - If delivery_mode == 'session':
          Delivers free-form media messages with captions (only valid within 24h window).
        """
        target_recipient = recipient or self._config.recipient
        if not target_recipient and self._config.default_recipients:
            target_recipient = self._config.default_recipients[0]

        if not target_recipient or not str(target_recipient).strip():
            logger.error("No WhatsApp recipient configured for report delivery.")
            return DeliveryReceipt(
                channel_name="WhatsApp",
                status=DeliveryStatus.FAILED,
                recipient="",
                error_message="Phone number must be a non-empty string.",
            )

        if not self.is_enabled():
            return DeliveryReceipt(
                channel_name="WhatsApp",
                status=DeliveryStatus.DISABLED,
                recipient=target_recipient,
                error_message="Channel disabled by policy pending Phase 11 approval",
            )

        try:
            normalized_recipient = normalize_phone_number(target_recipient)
        except ValueError as e:
            logger.error(f"Recipient phone validation failed: {e}")
            return DeliveryReceipt(
                channel_name="WhatsApp",
                status=DeliveryStatus.FAILED,
                recipient=target_recipient,
                error_message=str(e),
            )

        # 1. Discover and validate screenshots
        try:
            enrollment_img, closing_img = resolve_report_screenshots(
                report_date=report_date,
                screenshots_dir=screenshots_dir,
            )
        except FileNotFoundError as e:
            logger.error(f"Cannot deliver report: {e}")
            return DeliveryReceipt(
                channel_name="WhatsApp",
                status=DeliveryStatus.FAILED,
                recipient=normalized_recipient,
                error_message=str(e),
            )

        # 2. Check duplicate send
        if not force:
            if self._history.is_already_delivered(report_date, normalized_recipient):
                masked_phone = redact_phone_number(normalized_recipient)
                logger.warning(
                    f"Duplicate delivery blocked: report {report_date.isoformat()} already delivered to {masked_phone}."
                )
                return DeliveryReceipt(
                    channel_name="WhatsApp",
                    status=DeliveryStatus.DUPLICATE,
                    recipient=normalized_recipient,
                    error_message=f"Duplicate delivery blocked: report {report_date.isoformat()} already delivered to recipient. Use --force to override.",
                )

        # 3. Dry-run guard or strict read-only guard
        strict_read_only = os.getenv("STRICT_READ_ONLY", "false").lower() in ("true", "1", "yes")
        if self._config.dry_run or strict_read_only:
            masked_phone = redact_phone_number(normalized_recipient)

            if self._delivery_mode == "template":
                # Validate template configuration
                if not self._template_enrollment_name or not self._template_closing_name:
                    err = (
                        "Template delivery mode requires both template_enrollment_name and "
                        "template_closing_name to be configured."
                    )
                    logger.error(err)
                    return DeliveryReceipt(
                        channel_name="WhatsApp",
                        status=DeliveryStatus.FAILED,
                        recipient=normalized_recipient,
                        error_message=err,
                    )

                sim_id_1 = f"wamid_sim_tmpl1_{uuid4().hex[:12]}"
                sim_id_2 = f"wamid_sim_tmpl2_{uuid4().hex[:12]}"
                combined_sim_id = f"{sim_id_1},{sim_id_2}"

                logger.info(
                    f"[DRY-RUN] Template delivery simulated successfully to {masked_phone}:\n"
                    f"  Template 1: '{self._template_enrollment_name}' (Header: {enrollment_img.name})\n"
                    f"  Template 2: '{self._template_closing_name}' (Header: {closing_img.name})\n"
                    f"  Simulated Message IDs: {combined_sim_id}\n"
                    "  Zero external network calls performed."
                )
                self._history.record_delivery(
                    report_date=report_date,
                    recipient=normalized_recipient,
                    message_id=combined_sim_id,
                    attachments=[enrollment_img.name, closing_img.name],
                    status=DeliveryStatus.SENT.value,
                )
                return DeliveryReceipt(
                    channel_name="WhatsApp",
                    status=DeliveryStatus.SENT,
                    recipient=normalized_recipient,
                    message_id=combined_sim_id,
                    attachments_sent=2,
                )
            else:
                simulated_id = f"wamid_sim_session_{uuid4().hex[:16]}"
                logger.info(
                    f"[DRY-RUN] Session media message delivery simulated successfully to {masked_phone} "
                    f"(attachments: 2, message_id: {simulated_id}). Zero external network calls performed."
                )
                self._history.record_delivery(
                    report_date=report_date,
                    recipient=normalized_recipient,
                    message_id=simulated_id,
                    attachments=[enrollment_img.name, closing_img.name],
                    status=DeliveryStatus.SENT.value,
                )
                return DeliveryReceipt(
                    channel_name="WhatsApp",
                    status=DeliveryStatus.SENT,
                    recipient=normalized_recipient,
                    message_id=simulated_id,
                    attachments_sent=2,
                )

        # 4. Live execution
        if not self._api_token or not self._phone_number_id:
            logger.error("WhatsApp credentials missing in environment (WHATSAPP_API_TOKEN / WHATSAPP_PHONE_NUMBER_ID).")
            return DeliveryReceipt(
                channel_name="WhatsApp",
                status=DeliveryStatus.FAILED,
                recipient=normalized_recipient,
                error_message="Missing WhatsApp API credentials in environment",
            )

        if self._delivery_mode == "template":
            if not self._template_enrollment_name or not self._template_closing_name:
                err = (
                    "Template delivery mode requires both template_enrollment_name and "
                    "template_closing_name to be configured."
                )
                logger.error(err)
                return DeliveryReceipt(
                    channel_name="WhatsApp",
                    status=DeliveryStatus.FAILED,
                    recipient=normalized_recipient,
                    error_message=err,
                )
            try:
                return self._execute_template_delivery(
                    recipient=normalized_recipient,
                    enrollment_img=enrollment_img,
                    closing_img=closing_img,
                    report_date=report_date,
                    force=force,
                )
            except Exception as e:
                logger.error(f"WhatsApp template delivery failed: {e}")
                return DeliveryReceipt(
                    channel_name="WhatsApp",
                    status=DeliveryStatus.FAILED,
                    recipient=normalized_recipient,
                    error_message=f"Meta Cloud API template error: {e}",
                )
        else:
            message_text = format_executive_report_message(
                report_date=report_date,
                additional_notes=summary_notes,
            )
            try:
                return self._execute_session_delivery(
                    recipient=normalized_recipient,
                    message=message_text,
                    attachments=[enrollment_img, closing_img],
                    report_date=report_date,
                )
            except Exception as e:
                logger.error(f"WhatsApp session delivery failed: {e}")
                return DeliveryReceipt(
                    channel_name="WhatsApp",
                    status=DeliveryStatus.FAILED,
                    recipient=normalized_recipient,
                    error_message=f"Meta Cloud API session error: {e}",
                )
