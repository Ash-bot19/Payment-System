from datetime import datetime
from typing import Any

from pydantic import BaseModel


class WebhookReceivedMessage(BaseModel):
    """Message published to payment.webhook.received Kafka topic."""

    stripe_event_id: str
    event_type: str
    payload: dict[str, Any]
    received_at: datetime


class DuplicateEventResponse(BaseModel):
    status: str = "duplicate"
    message: str = "Event already processed"


class WebhookAcceptedResponse(BaseModel):
    status: str = "accepted"
    stripe_event_id: str
