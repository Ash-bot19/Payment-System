"""Pydantic v2 models for validation layer — Phase 02."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ValidatedPaymentEvent(BaseModel):
    """Output of successful schema + business-rule validation.

    Produced by validate_event() and published to payment.transaction.validated.
    Per D-14: this is the single canonical output model for all downstream consumers.
    """

    event_id: str           # from stripe_event_id
    event_type: str         # one of SUPPORTED_EVENT_TYPES
    amount_cents: int       # from payload.data.object.amount
    currency: str           # from payload.data.object.currency
    stripe_customer_id: str # from payload.data.object.customer or ""
    received_at: datetime


class DLQMessage(BaseModel):
    """Dead Letter Queue message per LOCKED DLQ contract.

    All 6 fields are required. Consumers of payment.dlq must be idempotent.
    failure_reason enum: SCHEMA_INVALID | IDEMPOTENCY_COLLISION | ML_TIMEOUT |
                          LEDGER_WRITE_FAIL | UNKNOWN
    """

    original_topic: str         # "payment.webhook.received"
    original_offset: int        # Kafka message offset
    failure_reason: str         # enum string per DLQ contract
    retry_count: int            # starts at 0
    first_failure_ts: datetime  # UTC timestamp of first failure
    payload: dict[str, Any]     # original message verbatim
