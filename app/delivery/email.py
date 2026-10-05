"""
Email delivery adapter (Placeholder - DISABLED).
"""

import logging
from pathlib import Path
from typing import List, Optional

from app.delivery.base import DeliveryChannel, DeliveryReceipt, DeliveryStatus

logger = logging.getLogger("pothys_reporting")


class EmailDeliveryChannel(DeliveryChannel):
    """Placeholder adapter for Email delivery."""

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
            logger.info("Email delivery is disabled. Skipping delivery.")
            return DeliveryReceipt(
                channel_name="Email",
                status=DeliveryStatus.DISABLED,
                recipient=recipient,
                error_message="Channel disabled by configuration",
            )

        raise NotImplementedError("Email delivery is not enabled in Phase 0.")
