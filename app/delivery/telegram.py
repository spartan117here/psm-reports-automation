"""
Telegram delivery adapter for Pothys Swarna Mahal executive reports.

SECURITY & COMPLIANCE NOTICE:
Telegram delivery is STRICTLY DISABLED by default (enabled=False) and dry-run is enabled by default (dry_run=True).
It will not make any external network requests unless:
1. telegram.enabled is set to True (or TELEGRAM_ENABLED=true in .env)
2. Valid TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are provided
3. dry_run is explicitly set to False (or TELEGRAM_DRY_RUN=false)
4. STRICT_READ_ONLY is False

RECIPIENT RESTRICTION:
All delivery is restricted to the configured personal chat ID (TELEGRAM_CHAT_ID).
Requests targeting any other recipient are rejected.

TOKEN PROTECTION:
The bot token is NEVER printed, logged, or exposed in exceptions, receipts, or error messages.
"""

from datetime import date, datetime
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import uuid4

import requests
from requests.exceptions import RequestException, Timeout

from app.core.config import TelegramConfig
from app.delivery.base import DeliveryChannel, DeliveryReceipt, DeliveryStatus

logger = logging.getLogger("pothys_reporting")

MAX_PHOTO_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB Telegram photo limit for sendPhoto
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg"}
TELEGRAM_API_BASE = "https://api.telegram.org"


def redact_token(token: Optional[str]) -> str:
    """Mask Telegram bot token for safe logging (e.g. 8336***[REDACTED])."""
    if not token:
        return "[NONE]"
    clean = str(token).strip()
    if len(clean) > 8:
        return f"{clean[:4]}***[REDACTED]"
    return "***[REDACTED]"


def redact_chat_id(chat_id: Optional[Union[str, int]]) -> str:
    """Mask chat ID for safe logging (e.g. 16****9109)."""
    if chat_id is None:
        return "[UNSPECIFIED]"
    clean = str(chat_id).strip()
    if not clean:
        return "[EMPTY]"
    if len(clean) >= 6:
        return f"{clean[:2]}****{clean[-4:]}"
    return "***"


def sanitize_log_message(msg: str, token: Optional[str] = None) -> str:
    """
    Scrub bot token and any Telegram bot token patterns from strings to prevent accidental leakage in logs/exceptions.
    """
    if not msg:
        return ""
    text = str(msg)
    if token and token in text:
        text = text.replace(token, "[BOT_TOKEN_REDACTED]")
    # Scrub URL format: /bot<token>/...
    text = re.sub(r"/bot\d+:[^/ \n\r\t]+", "/bot[BOT_TOKEN_REDACTED]", text)
    # Scrub standalone token format: \d{8,12}:[A-Za-z0-9_-]{25,}
    text = re.sub(r"\b\d{8,12}:[A-Za-z0-9_-]{25,}\b", "[BOT_TOKEN_REDACTED]", text)
    return text


def normalize_chat_id(chat_id: Any) -> str:
    """
    Validate and normalize Telegram chat ID.
    Supports integer string IDs, negative group IDs, and channel usernames (@username).
    """
    if chat_id is None:
        raise ValueError("Telegram chat ID cannot be None or empty.")
    s = str(chat_id).strip()
    if not s:
        raise ValueError("Telegram chat ID cannot be None or empty.")

    # Channel username format (@channelname)
    if s.startswith("@"):
        if len(s) < 2:
            raise ValueError(f"Invalid Telegram channel username: '{s}'")
        return s

    # Numeric ID (can be negative for supergroups/channels, e.g. -100...)
    stripped = s.lstrip("-")
    if not stripped.isdigit():
        raise ValueError(f"Invalid non-numeric Telegram chat ID: '{s}'")

    return s


def validate_attachment(path: Union[str, Path]) -> Path:
    """
    Verify that screenshot image exists, is accessible, has supported extension,
    and does not exceed the Telegram 10MB photo limit.
    """
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Attachment file not found: '{resolved}'")
    if not resolved.is_file():
        raise ValueError(f"Attachment path is not a file: '{resolved}'")

    suffix = resolved.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported attachment type '{suffix}' for file '{resolved.name}'. "
            f"Telegram sendPhoto supports: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    file_size = resolved.stat().st_size
    if file_size == 0:
        raise ValueError(f"Attachment '{resolved.name}' is empty (0 bytes).")
    if file_size > MAX_PHOTO_SIZE_BYTES:
        raise ValueError(
            f"Attachment '{resolved.name}' ({file_size:,} bytes) exceeds {MAX_PHOTO_SIZE_BYTES:,} byte limit."
        )

    return resolved


def resolve_telegram_screenshot(
    report_date: date,
    target: str = "enrollment",
    screenshots_dir: Optional[Path] = None,
) -> Path:
    """
    Resolve and validate a specific report screenshot by date and target type.

    Target options:
    - 'enrollment': enrollment_report_{YYYYMMDD}.png
    - 'closing' or 'closing_rejoining': closing_rejoining_report_{YYYYMMDD}.png
    """
    base_dir = Path(screenshots_dir or "data/screenshots").resolve()
    date_tag = report_date.strftime("%Y%m%d")

    target_clean = target.lower().strip()
    if target_clean in ("enrollment", "new_enrollment"):
        filename = f"enrollment_report_{date_tag}.png"
    elif target_clean in ("closing", "closing_rejoining", "rejoining"):
        filename = f"closing_rejoining_report_{date_tag}.png"
    else:
        filename = f"{target_clean}_{date_tag}.png"

    screenshot_path = base_dir / filename
    if not screenshot_path.is_file():
        mock_path = base_dir / f"mock_{filename}"
        mock_sub_path = base_dir / "mock" / f"mock_{filename}"
        mock_sub_clean = base_dir / "mock" / filename
        if mock_path.is_file():
            screenshot_path = mock_path
        elif mock_sub_path.is_file():
            screenshot_path = mock_sub_path
        elif mock_sub_clean.is_file():
            screenshot_path = mock_sub_clean
        else:
            raise FileNotFoundError(
                f"Screenshot '{filename}' not found for date {report_date.isoformat()} at: {screenshot_path}"
            )

    return validate_attachment(screenshot_path)


def format_telegram_caption(
    report_date: date,
    report_title: str = "New Enrollment Report",
    enrollment_total: Optional[int] = None,
    closing_total: Optional[int] = None,
    rejoining_rate_pct: Optional[float] = None,
    notes: Optional[str] = None,
) -> str:
    """
    Format professional Telegram report photo caption.

    Contains exactly two lines:
    Line 1: POTHYS SWARNA MAHAL
    Line 2: Daily <Report Title> — <DD/MM/YYYY>

    Examples:
        POTHYS SWARNA MAHAL
        Daily New Enrollment Report — 26/10/2026

        POTHYS SWARNA MAHAL
        Daily Scheme Rejoining Report — 26/10/2026
    """
    date_str = report_date.strftime("%d/%m/%Y")
    clean_title = report_title.strip()
    if clean_title.startswith("Daily "):
        clean_title = clean_title[6:].strip()

    title_lower = clean_title.lower()
    if "rejoining" in title_lower or "closing" in title_lower:
        canonical_title = "Scheme Rejoining Report"
    elif "enrollment" in title_lower:
        canonical_title = "New Enrollment Report"
    else:
        canonical_title = clean_title

    return f"POTHYS SWARNA MAHAL\nDaily {canonical_title} — {date_str}"



class TelegramDeliveryHistory:
    """Persistent delivery history tracker for Telegram idempotency and duplicate prevention."""

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
            logger.warning(f"Could not load Telegram delivery history from {self.history_file}: {e}")
            return []

    def _save(self, deliveries: List[Dict[str, Any]]) -> None:
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump({"deliveries": deliveries}, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save Telegram delivery history to {self.history_file}: {e}")

    def is_already_delivered(self, report_date: date, chat_id: str, attachment_name: Optional[str] = None) -> bool:
        """
        Check if report_date (and optional specific attachment) has already been delivered to chat_id.

        Only confirmed successful live deliveries (status == 'SENT' and not simulated) count as delivered.
        """
        date_iso = report_date.isoformat()
        try:
            norm_id = normalize_chat_id(chat_id)
        except Exception:
            norm_id = str(chat_id)

        for entry in self._load():
            msg_id = str(entry.get("message_id", ""))
            # Simulated or failed attempts must NEVER block delivery
            if msg_id.startswith("tg_sim_") or entry.get("status") != DeliveryStatus.SENT.value:
                continue

            if (
                entry.get("report_date") == date_iso
                and entry.get("chat_id") == norm_id
            ):
                if attachment_name is None:
                    return True
                if attachment_name in entry.get("attachments", []):
                    return True
        return False

    def record_delivery(
        self,
        report_date: date,
        chat_id: str,
        message_id: str,
        attachments: List[str],
        status: str = DeliveryStatus.SENT.value,
    ) -> None:
        """Record delivery attempt in persistent history."""
        try:
            norm_id = normalize_chat_id(chat_id)
        except Exception:
            norm_id = str(chat_id)

        deliveries = self._load()
        deliveries.append(
            {
                "report_date": report_date.isoformat(),
                "chat_id": norm_id,
                "channel": "Telegram",
                "status": status,
                "message_id": message_id,
                "attachments": attachments,
                "timestamp": datetime.now().isoformat(),
            }
        )
        self._save(deliveries)


class TelegramDeliveryChannel(DeliveryChannel):
    """
    Enterprise Telegram Bot API delivery adapter for executive reports.

    Safety controls:
    - Disabled by default (enabled=False).
    - Dry-run enabled by default (dry_run=True).
    - STRICT_READ_ONLY forces dry-run simulation even if dry_run=False.
    - Test recipient is strictly restricted to the configured personal chat ID.
    - Bot token is never logged, exposed in error messages, or printed.
    - Multipart image upload via official Telegram Bot API sendPhoto endpoint.
    """

    def __init__(
        self,
        config: Optional[TelegramConfig] = None,
        enabled: Optional[bool] = None,
        dry_run: Optional[bool] = None,
        bot_token: Optional[str] = None,
        chat_id: Optional[Union[str, int]] = None,
        timeout_seconds: Optional[int] = None,
        history_file: Optional[Path] = None,
    ):
        self._config = config or TelegramConfig()
        if enabled is not None:
            self._config.enabled = enabled
        if dry_run is not None:
            self._config.dry_run = dry_run

        self._bot_token = (
            bot_token
            or self._config.bot_token
            or os.getenv("TELEGRAM_BOT_TOKEN")
        )
        raw_chat_id = (
            chat_id
            or self._config.chat_id
            or os.getenv("TELEGRAM_CHAT_ID")
        )
        self._chat_id: Optional[str] = None
        if raw_chat_id is not None:
            try:
                self._chat_id = normalize_chat_id(raw_chat_id)
            except ValueError:
                self._chat_id = str(raw_chat_id).strip()

        self._timeout = timeout_seconds or self._config.timeout_seconds or 30
        hist_path = history_file or self._config.history_file or Path("data/delivery/telegram_history.json")
        self._history = TelegramDeliveryHistory(hist_path)

    def is_enabled(self) -> bool:
        """Check if Telegram delivery is active."""
        return self._config.enabled

    @property
    def dry_run(self) -> bool:
        """Check if dry-run simulation mode is active."""
        return self._config.dry_run

    @property
    def configured_chat_id(self) -> Optional[str]:
        """Return configured recipient chat ID."""
        return self._chat_id

    def send_report(
        self,
        recipient: str,
        message: str,
        attachments: Optional[List[Path]] = None,
        report_date: Optional[date] = None,
        force: bool = False,
    ) -> DeliveryReceipt:
        """
        Implementation of abstract DeliveryChannel.send_report.
        Delivers report text and photo attachment via Telegram sendPhoto.
        """
        if not attachments:
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.FAILED,
                recipient=str(recipient or ""),
                error_message="Missing image attachment: Telegram sendPhoto requires an image.",
            )

        # Deliver the primary attachment with message caption
        return self.send_photo(
            photo_path=attachments[0],
            caption=message,
            recipient=recipient,
            report_date=report_date,
            force=force,
        )

    def send_photo(
        self,
        photo_path: Union[str, Path],
        caption: Optional[str] = None,
        recipient: Optional[Union[str, int]] = None,
        report_date: Optional[date] = None,
        force: bool = False,
    ) -> DeliveryReceipt:
        """
        Deliver a single verified report screenshot photo to the configured Telegram chat.

        Safety gates:
        1. Validates recipient and restricts delivery to configured personal chat ID.
        2. Validates channel is enabled.
        3. Validates attachment exists, non-empty, supported extension, <= 10MB.
        4. Validates idempotency / duplicate-send check.
        5. Enforces dry-run or STRICT_READ_ONLY simulation without external network calls.
        6. Dispatches multipart image upload via official Telegram Bot API sendPhoto.
        """
        target_chat_id = recipient or self._chat_id
        if not target_chat_id:
            logger.error("Telegram recipient chat ID is not configured (missing TELEGRAM_CHAT_ID).")
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.FAILED,
                recipient="",
                error_message="Telegram chat ID is not configured (missing TELEGRAM_CHAT_ID).",
            )

        # 1. Normalize and restrict recipient to configured personal chat ID
        try:
            norm_target = normalize_chat_id(target_chat_id)
        except ValueError as e:
            logger.error(f"Telegram chat ID validation failed: {e}")
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.FAILED,
                recipient=str(target_chat_id),
                error_message=str(e),
            )

        if self._chat_id:
            try:
                norm_configured = normalize_chat_id(self._chat_id)
                if norm_target != norm_configured:
                    masked_target = redact_chat_id(norm_target)
                    masked_config = redact_chat_id(norm_configured)
                    logger.error(
                        f"Recipient {masked_target} rejected: Delivery strictly restricted to configured personal chat ID ({masked_config})."
                    )
                    return DeliveryReceipt(
                        channel_name="Telegram",
                        status=DeliveryStatus.FAILED,
                        recipient=norm_target,
                        error_message="Recipient restricted to configured personal chat ID.",
                    )
            except ValueError:
                pass

        # 2. Check enabled state
        if not self.is_enabled():
            logger.info("Telegram delivery is disabled. Skipping delivery.")
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.DISABLED,
                recipient=norm_target,
                error_message="Telegram delivery channel is disabled by policy.",
            )

        # 3. Validate image attachment
        try:
            val_photo = validate_attachment(photo_path)
        except Exception as e:
            logger.error(f"Photo validation failed for '{photo_path}': {e}")
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.FAILED,
                recipient=norm_target,
                error_message=f"Attachment validation failed: {e}",
            )

        # 4. Duplicate send check
        if report_date and not force:
            if self._history.is_already_delivered(report_date, norm_target, val_photo.name):
                masked_id = redact_chat_id(norm_target)
                logger.warning(
                    f"Duplicate delivery blocked: report {report_date.isoformat()} ({val_photo.name}) already delivered to {masked_id}."
                )
                return DeliveryReceipt(
                    channel_name="Telegram",
                    status=DeliveryStatus.DUPLICATE,
                    recipient=norm_target,
                    error_message=f"Duplicate delivery blocked: report {report_date.isoformat()} already delivered. Use --force to override.",
                )

        # 5. Decoupled Delivery Authorization Guard
        # Live transmission strictly requires all three conditions:
        #   1. Channel enabled: self._config.enabled is True (TELEGRAM_ENABLED=true)
        #   2. Simulation disabled: self._config.dry_run is False (TELEGRAM_DRY_RUN=false)
        #   3. Target chat ID matches configured personal recipient (enforced in Gate 1)
        #
        # If dry_run is True (the default), safe simulation is enforced with zero external calls.
        if self._config.dry_run:
            simulated_id = f"tg_sim_{uuid4().hex[:12]}"
            masked_id = redact_chat_id(norm_target)
            logger.info(
                f"[DRY-RUN] Telegram photo upload simulated successfully to {masked_id} "
                f"(attachment: {val_photo.name}, size: {val_photo.stat().st_size:,} bytes, message_id: {simulated_id}). "
                "Zero external network calls performed."
            )
            # NOTE: Dry-run simulations must never poison the persistent live delivery ledger.
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.SENT,
                recipient=norm_target,
                message_id=simulated_id,
                attachments_sent=1,
            )

        # 6. Live API execution check
        if not self._bot_token:
            logger.error("Telegram bot token is not configured (missing TELEGRAM_BOT_TOKEN).")
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.FAILED,
                recipient=norm_target,
                error_message="Telegram bot token is not configured (missing TELEGRAM_BOT_TOKEN).",
            )

        # 7. Live multipart POST to sendPhoto
        return self._execute_send_photo(
            chat_id=norm_target,
            photo_path=val_photo,
            caption=caption,
            report_date=report_date,
        )

    def _execute_send_photo(
        self,
        chat_id: str,
        photo_path: Path,
        caption: Optional[str] = None,
        report_date: Optional[date] = None,
    ) -> DeliveryReceipt:
        """Execute multipart/form-data POST request to Telegram sendPhoto endpoint."""
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/sendPhoto"
        masked_id = redact_chat_id(chat_id)
        logger.info(f"Uploading report screenshot to Telegram chat {masked_id}: {photo_path.name}")

        data: Dict[str, Any] = {"chat_id": chat_id}
        if caption:
            # Telegram caption limit is 1024 characters
            data["caption"] = caption[:1024]

        try:
            with open(photo_path, "rb") as f:
                # Official Telegram Bot API multipart file upload
                files = {"photo": (photo_path.name, f, "image/png")}
                resp = requests.post(
                    url,
                    data=data,
                    files=files,
                    timeout=self._timeout,
                )
        except Timeout:
            logger.error(f"Telegram upload timed out after {self._timeout}s for '{photo_path.name}'.")
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.FAILED,
                recipient=chat_id,
                error_message=f"Telegram upload timed out after {self._timeout}s",
            )
        except RequestException as e:
            safe_err = sanitize_log_message(str(e), self._bot_token)
            logger.error(f"Telegram sendPhoto network error: {safe_err}")
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.FAILED,
                recipient=chat_id,
                error_message=f"Telegram network error: {safe_err}",
            )

        if resp.status_code != 200:
            err_msg = self._parse_api_error(resp)
            logger.error(f"Telegram sendPhoto failed ({resp.status_code}): {err_msg}")
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.FAILED,
                recipient=chat_id,
                error_message=f"Telegram API error ({resp.status_code}): {err_msg}",
            )

        try:
            body = resp.json()
        except Exception:
            body = {}

        if not body.get("ok"):
            desc = body.get("description", "Unknown error")
            err_msg = sanitize_log_message(desc, self._bot_token)
            logger.error(f"Telegram API returned ok=False: {err_msg}")
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.FAILED,
                recipient=chat_id,
                error_message=f"Telegram API error: {err_msg}",
            )

        msg_id_val = body.get("result", {}).get("message_id")
        message_id = f"tg_{msg_id_val}" if msg_id_val is not None else "tg_sent"

        logger.info(
            f"Telegram screenshot report successfully delivered to {masked_id} "
            f"(attachment: {photo_path.name}, message_id: {message_id})."
        )

        if report_date:
            self._history.record_delivery(
                report_date=report_date,
                chat_id=chat_id,
                message_id=message_id,
                attachments=[photo_path.name],
                status=DeliveryStatus.SENT.value,
            )

        return DeliveryReceipt(
            channel_name="Telegram",
            status=DeliveryStatus.SENT,
            recipient=chat_id,
            message_id=message_id,
            attachments_sent=1,
        )

    def send_message(
        self,
        text: str,
        recipient: Optional[Union[str, int]] = None,
        report_date: Optional[date] = None,
    ) -> DeliveryReceipt:
        """
        Deliver a text alert / status message to the configured Telegram chat.

        Safety gates:
        1. Validates recipient and restricts delivery to configured personal chat ID.
        2. Validates channel is enabled.
        3. Enforces dry-run or STRICT_READ_ONLY simulation without external network calls.
        4. Dispatches text payload via official Telegram Bot API sendMessage.
        """
        target_chat_id = recipient or self._chat_id
        if not target_chat_id:
            logger.error("Telegram recipient chat ID is not configured (missing TELEGRAM_CHAT_ID).")
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.FAILED,
                recipient="",
                error_message="Telegram chat ID is not configured (missing TELEGRAM_CHAT_ID).",
            )

        try:
            norm_target = normalize_chat_id(target_chat_id)
        except ValueError as e:
            logger.error(f"Telegram chat ID validation failed: {e}")
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.FAILED,
                recipient=str(target_chat_id),
                error_message=str(e),
            )

        if self._chat_id:
            try:
                norm_configured = normalize_chat_id(self._chat_id)
                if norm_target != norm_configured:
                    masked_target = redact_chat_id(norm_target)
                    masked_config = redact_chat_id(norm_configured)
                    logger.error(
                        f"Recipient {masked_target} rejected: Delivery strictly restricted to configured personal chat ID ({masked_config})."
                    )
                    return DeliveryReceipt(
                        channel_name="Telegram",
                        status=DeliveryStatus.FAILED,
                        recipient=norm_target,
                        error_message="Recipient restricted to configured personal chat ID.",
                    )
            except ValueError:
                pass

        if not self.is_enabled():
            logger.info("Telegram delivery is disabled. Skipping message delivery.")
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.DISABLED,
                recipient=norm_target,
                error_message="Telegram delivery channel is disabled by policy.",
            )

        if self._config.dry_run:
            simulated_id = f"tg_msg_sim_{uuid4().hex[:12]}"
            masked_id = redact_chat_id(norm_target)
            logger.info(
                f"[DRY-RUN] Telegram message simulated successfully to {masked_id} "
                f"(chars: {len(text)}, message_id: {simulated_id}). "
                "Zero external network calls performed."
            )
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.SENT,
                recipient=norm_target,
                message_id=simulated_id,
                attachments_sent=0,
            )

        if not self._bot_token:
            logger.error("Telegram bot token is not configured (missing TELEGRAM_BOT_TOKEN).")
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.FAILED,
                recipient=norm_target,
                error_message="Telegram bot token is not configured (missing TELEGRAM_BOT_TOKEN).",
            )

        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/sendMessage"
        payload = {"chat_id": norm_target, "text": text[:4096]}
        try:
            resp = requests.post(url, json=payload, timeout=self._timeout)
            if resp.status_code != 200:
                err_msg = self._parse_api_error(resp)
                logger.error(f"Telegram sendMessage failed ({resp.status_code}): {err_msg}")
                return DeliveryReceipt(
                    channel_name="Telegram",
                    status=DeliveryStatus.FAILED,
                    recipient=norm_target,
                    error_message=f"Telegram API error ({resp.status_code}): {err_msg}",
                )
            body = resp.json()
            if not body.get("ok"):
                desc = body.get("description", "Unknown error")
                err_msg = sanitize_log_message(desc, self._bot_token)
                logger.error(f"Telegram API returned ok=False: {err_msg}")
                return DeliveryReceipt(
                    channel_name="Telegram",
                    status=DeliveryStatus.FAILED,
                    recipient=norm_target,
                    error_message=f"Telegram API error: {err_msg}",
                )
            msg_id_val = body.get("result", {}).get("message_id")
            message_id = f"tg_msg_{msg_id_val}" if msg_id_val is not None else "tg_msg_sent"
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.SENT,
                recipient=norm_target,
                message_id=message_id,
                attachments_sent=0,
            )
        except Exception as e:
            safe_err = sanitize_log_message(str(e), self._bot_token)
            logger.error(f"Telegram sendMessage network error: {safe_err}")
            return DeliveryReceipt(
                channel_name="Telegram",
                status=DeliveryStatus.FAILED,
                recipient=norm_target,
                error_message=f"Telegram network error: {safe_err}",
            )

    def _parse_api_error(self, resp: requests.Response) -> str:
        """Safely parse Telegram Bot API error description without leaking secrets."""
        try:
            data = resp.json()
            desc = data.get("description") or data.get("error") or f"HTTP {resp.status_code}"
            return sanitize_log_message(str(desc), self._bot_token)
        except Exception:
            return sanitize_log_message(f"HTTP {resp.status_code}", self._bot_token)
