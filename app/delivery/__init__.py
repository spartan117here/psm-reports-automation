"""Delivery abstraction package for notification and report distribution."""

from app.delivery.base import DeliveryChannel, DeliveryReceipt, DeliveryStatus
from app.delivery.email import EmailDeliveryChannel
from app.delivery.whatsapp import WhatsAppDeliveryChannel
from app.delivery.telegram import TelegramDeliveryChannel

__all__ = [
    "DeliveryChannel",
    "DeliveryReceipt",
    "DeliveryStatus",
    "EmailDeliveryChannel",
    "WhatsAppDeliveryChannel",
    "TelegramDeliveryChannel",
]
