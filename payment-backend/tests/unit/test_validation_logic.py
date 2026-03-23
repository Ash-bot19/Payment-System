"""Unit tests for validation logic — Task 1, Phase 02, Plan 01."""

from datetime import datetime
from typing import Any

import pytest

from kafka.consumers.validation_logic import (
    SUPPORTED_EVENT_TYPES,
    ValidationError,
    validate_event,
)
from models.validation import DLQMessage, ValidatedPaymentEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_raw_event(**overrides: Any) -> dict[str, Any]:
    """Return a valid raw event dict matching WebhookReceivedMessage shape."""
    base: dict[str, Any] = {
        "stripe_event_id": "evt_test_123",
        "event_type": "payment_intent.succeeded",
        "payload": {
            "data": {
                "object": {
                    "amount": 1000,
                    "currency": "usd",
                    "customer": "cus_test_456",
                }
            }
        },
        "received_at": "2026-03-23T00:00:00Z",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_valid_succeeded_event_returns_validated_payment_event() -> None:
    """Valid payment_intent.succeeded event returns ValidatedPaymentEvent."""
    raw = make_raw_event()
    result = validate_event(raw)

    assert isinstance(result, ValidatedPaymentEvent)
    assert result.event_id == "evt_test_123"
    assert result.event_type == "payment_intent.succeeded"
    assert result.amount_cents == 1000
    assert result.currency == "usd"
    assert result.stripe_customer_id == "cus_test_456"


def test_valid_canceled_event_with_amount_zero_passes() -> None:
    """payment_intent.canceled with amount=0 passes — positivity not enforced."""
    raw = make_raw_event(
        event_type="payment_intent.canceled",
        payload={
            "data": {
                "object": {
                    "amount": 0,
                    "currency": "usd",
                    "customer": "",
                }
            }
        },
    )
    result = validate_event(raw)

    assert isinstance(result, ValidatedPaymentEvent)
    assert result.event_type == "payment_intent.canceled"
    assert result.amount_cents == 0


def test_valid_payment_failed_event_passes() -> None:
    """payment_intent.payment_failed is a supported event type and passes."""
    raw = make_raw_event(
        event_type="payment_intent.payment_failed",
        payload={
            "data": {
                "object": {
                    "amount": 500,
                    "currency": "usd",
                }
            }
        },
    )
    result = validate_event(raw)

    assert isinstance(result, ValidatedPaymentEvent)
    assert result.event_type == "payment_intent.payment_failed"


# ---------------------------------------------------------------------------
# Schema failure tests
# ---------------------------------------------------------------------------


def test_missing_stripe_event_id_raises_schema_invalid() -> None:
    """Missing stripe_event_id raises ValidationError with SCHEMA_INVALID."""
    raw = make_raw_event()
    del raw["stripe_event_id"]

    with pytest.raises(ValidationError) as exc_info:
        validate_event(raw)

    assert exc_info.value.reason == "SCHEMA_INVALID"


def test_unsupported_event_type_raises_schema_invalid() -> None:
    """Unsupported event type 'charge.succeeded' raises SCHEMA_INVALID."""
    raw = make_raw_event(event_type="charge.succeeded")

    with pytest.raises(ValidationError) as exc_info:
        validate_event(raw)

    assert exc_info.value.reason == "SCHEMA_INVALID"


def test_missing_amount_field_raises_schema_invalid() -> None:
    """Missing data.object.amount raises SCHEMA_INVALID — no fallback."""
    raw = make_raw_event(
        payload={
            "data": {
                "object": {
                    "currency": "usd",
                    "customer": "cus_test_456",
                    # amount intentionally omitted
                }
            }
        }
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_event(raw)

    assert exc_info.value.reason == "SCHEMA_INVALID"


# ---------------------------------------------------------------------------
# Business rule failure tests (amount rules for payment_intent.succeeded)
# ---------------------------------------------------------------------------


def test_succeeded_with_amount_zero_raises_schema_invalid() -> None:
    """payment_intent.succeeded with amount=0 raises SCHEMA_INVALID."""
    raw = make_raw_event(
        payload={
            "data": {
                "object": {
                    "amount": 0,
                    "currency": "usd",
                    "customer": "cus_test_456",
                }
            }
        }
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_event(raw)

    assert exc_info.value.reason == "SCHEMA_INVALID"


def test_succeeded_with_negative_amount_raises_schema_invalid() -> None:
    """payment_intent.succeeded with amount=-500 raises SCHEMA_INVALID."""
    raw = make_raw_event(
        payload={
            "data": {
                "object": {
                    "amount": -500,
                    "currency": "usd",
                    "customer": "cus_test_456",
                }
            }
        }
    )

    with pytest.raises(ValidationError) as exc_info:
        validate_event(raw)

    assert exc_info.value.reason == "SCHEMA_INVALID"


# ---------------------------------------------------------------------------
# DLQMessage model tests
# ---------------------------------------------------------------------------


def test_dlq_message_requires_all_six_fields() -> None:
    """DLQMessage enforces all 6 required contract fields."""
    now = datetime.utcnow()
    msg = DLQMessage(
        original_topic="payment.webhook.received",
        original_offset=42,
        failure_reason="SCHEMA_INVALID",
        retry_count=0,
        first_failure_ts=now,
        payload={"stripe_event_id": "evt_test_123"},
    )

    assert msg.original_topic == "payment.webhook.received"
    assert msg.original_offset == 42
    assert msg.failure_reason == "SCHEMA_INVALID"
    assert msg.retry_count == 0
    assert msg.first_failure_ts == now
    assert msg.payload == {"stripe_event_id": "evt_test_123"}


def test_dlq_message_missing_field_raises_validation_error() -> None:
    """DLQMessage raises pydantic.ValidationError when a required field is missing."""
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        DLQMessage(  # type: ignore[call-arg]
            original_topic="payment.webhook.received",
            # original_offset intentionally omitted
            failure_reason="UNKNOWN",
            retry_count=0,
            first_failure_ts=datetime.utcnow(),
            payload={},
        )


# ---------------------------------------------------------------------------
# SUPPORTED_EVENT_TYPES tests
# ---------------------------------------------------------------------------


def test_supported_event_types_is_frozenset_with_three_entries() -> None:
    """SUPPORTED_EVENT_TYPES is a frozenset with exactly 3 entries."""
    assert isinstance(SUPPORTED_EVENT_TYPES, frozenset)
    assert len(SUPPORTED_EVENT_TYPES) == 3
    assert "payment_intent.succeeded" in SUPPORTED_EVENT_TYPES
    assert "payment_intent.payment_failed" in SUPPORTED_EVENT_TYPES
    assert "payment_intent.canceled" in SUPPORTED_EVENT_TYPES
