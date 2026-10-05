"""Abstract DeliveryChannel interface and delivery models."""

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field


class DeliveryStatus(str, Enum):
    """Delivery status enum."""
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    DISABLED = "DISABLED"


class DeliveryReceipt(BaseModel):
    """Result receipt returned by a delivery channel."""
    channel_name: str
    status: DeliveryStatus
    recipient: str
    message_id: Optional[str] = None
    error_message: Optional[str] = None


class DeliveryChannel(ABC):
    """Abstract base class for all report distribution channels."""

    @abstractmethod
    def is_enabled(self) -> bool:
        """Check if this delivery channel is currently active and configured."""
        pass

    @abstractmethod
    def send_report(
        self,
        recipient: str,
        message: str,
        attachments: Optional[List[Path]] = None,
    ) -> DeliveryReceipt:
        """Deliver report text and optional image attachments to a recipient."""
        pass
