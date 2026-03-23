"""Schema and business-rule validation logic for payment events — Phase 02.

Consumes raw messages from payment.webhook.received, validates them, and
returns ValidatedPaymentEvent on success. Raises ValidationError on failure
so the consumer can route to the DLQ with the appropriate reason code.
"""

from typing import Any

import structlog
from pydantic import ValidationError as PydanticValidationError

from models.validation import ValidatedPaymentEvent
from models.webhook import WebhookReceivedMessage

logger = structlog.get_logger(__name__)

# Per D-07 and D-08: exactly three supported event types, locked as frozenset.
SUPPORTED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "payment_intent.canceled",
    }
)


class ValidationError(Exception):
    """Raised when an event fails schema or business-rule validation.

    Attributes:
        reason: DLQ failure_reason enum string (e.g. "SCHEMA_INVALID").
        detail: Human-readable explanation for logging.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def validate_event(raw_message: dict[str, Any]) -> ValidatedPaymentEvent:
    """Validate a raw Kafka message from payment.webhook.received.

    Steps:
    1. Deserialize into WebhookReceivedMessage (Pydantic schema check).
    2. Check event_type is in SUPPORTED_EVENT_TYPES.
    3. Extract and type-check amount from payload.data.object.amount.
    4. Enforce amount > 0 for payment_intent.succeeded only.
    5. Extract currency (default "usd") and stripe_customer_id (default "").
    6. Return ValidatedPaymentEvent.

    Raises:
        ValidationError: with reason="SCHEMA_INVALID" on any failure.
    """
    # Step 1: deserialize and validate top-level schema.
    try:
        message = WebhookReceivedMessage.model_validate(raw_message)
    except PydanticValidationError as exc:
        raise ValidationError(
            reason="SCHEMA_INVALID",
            detail=f"Top-level message schema invalid: {exc}",
        ) from exc

    # Step 2: event type allowlist (per D-09).
    if message.event_type not in SUPPORTED_EVENT_TYPES:
        raise ValidationError(
            reason="SCHEMA_INVALID",
            detail=f"Unsupported event_type '{message.event_type}'. "
            f"Allowed: {sorted(SUPPORTED_EVENT_TYPES)}",
        )

    # Step 3: extract amount (per D-10, D-11 — no fallback, fail hard).
    try:
        data_object: dict[str, Any] = message.payload["data"]["object"]
    except (KeyError, TypeError) as exc:
        raise ValidationError(
            reason="SCHEMA_INVALID",
            detail=f"Missing payload.data.object: {exc}",
        ) from exc

    if "amount" not in data_object:
        raise ValidationError(
            reason="SCHEMA_INVALID",
            detail="Missing required field payload.data.object.amount",
        )

    amount = data_object["amount"]
    if not isinstance(amount, int):
        raise ValidationError(
            reason="SCHEMA_INVALID",
            detail=f"payload.data.object.amount must be int, got {type(amount).__name__}",
        )

    # Step 4: amount positivity rule applies to payment_intent.succeeded only (per D-12).
    if message.event_type == "payment_intent.succeeded":
        if amount < 0:
            # Per D-13: log a warning before raising.
            logger.warning(
                "negative_amount_received",
                stripe_event_id=message.stripe_event_id,
                event_type=message.event_type,
                amount=amount,
            )
            raise ValidationError(
                reason="SCHEMA_INVALID",
                detail=f"Negative amount {amount} not allowed for payment_intent.succeeded",
            )
        if amount == 0:
            raise ValidationError(
                reason="SCHEMA_INVALID",
                detail="Zero amount not allowed for payment_intent.succeeded",
            )

    # Step 5: extract optional fields with safe defaults.
    currency: str = data_object.get("currency", "usd")
    stripe_customer_id: str = data_object.get("customer") or ""

    logger.info(
        "event_validated",
        stripe_event_id=message.stripe_event_id,
        event_type=message.event_type,
        amount_cents=amount,
    )

    return ValidatedPaymentEvent(
        event_id=message.stripe_event_id,
        event_type=message.event_type,
        amount_cents=amount,
        currency=currency,
        stripe_customer_id=stripe_customer_id,
        received_at=message.received_at,
    )
