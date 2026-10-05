"""
WhatsApp delivery adapter (Placeholder - DISABLED).

IMPORTANT:
WhatsApp automation must remain strictly disabled until the approved
official enterprise messaging mechanism/API is confirmed in Phase 11.
"""

import logging
from pathlib import Path
from typing import List, Optional

from app.delivery.base import DeliveryChannel, DeliveryReceipt, DeliveryStatus

logger = logging.getLogger("pothys_reporting")


class WhatsAppDeliveryChannel(DeliveryChannel):
    """Placeholder adapter for WhatsApp delivery."""

    def __init__(self, enabled: bool = False):
        self._enabled = enabled

    def is_enabled(self) -> bool:
        return self._enabled

    def send_report(
        self,
        recipient: str,
        message: str,
        attachments: Optional[List[Path]] = None,
    ) -> DeliveryReceipt:
        if not self.is_enabled():
            logger.info("WhatsApp delivery is disabled. Skipping delivery.")
            return DeliveryReceipt(
                channel_name="WhatsApp",
                status=DeliveryStatus.DISABLED,
                recipient=recipient,
                error_message="Channel disabled by policy pending Phase 11 approval",
            )

        raise NotImplementedError("WhatsApp delivery is not implemented in Phase 0.")
