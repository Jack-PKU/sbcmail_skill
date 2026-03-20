"""SBCMail data models."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SendResult(BaseModel):
    message_id: str
    created_at: str


class DeliveryInfo(BaseModel):
    delivery_id: str
    recipient: str
    status: str
    retry_count: int = 0
    delivered_at: Optional[str] = None
    created_at: Optional[str] = None


class MessageDetail(BaseModel):
    message_id: str
    sender: str
    intent: str
    payload: dict
    thread_id: Optional[str] = None
    reply_to: Optional[str] = None
    priority: str = "normal"
    ttl: Optional[int] = None
    created_at: Optional[str] = None
    signature: Optional[str] = None
    delivery_receipt: bool = True
    deliveries: list[DeliveryInfo] = []


class PollMessage(BaseModel):
    message_id: str
    sender: str
    intent: str
    payload: dict
    thread_id: Optional[str] = None
    reply_to: Optional[str] = None
    priority: str = "normal"
    created_at: Optional[datetime] = None
