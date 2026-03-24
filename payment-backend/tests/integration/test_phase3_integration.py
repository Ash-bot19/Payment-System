"""Integration tests for Phase 3: state machine, rate limiting, downstream publish.

Requires docker-compose services running: PostgreSQL, Redis, Kafka.
Run: cd payment-backend && python -m pytest tests/integration/test_phase3_integration.py -v

Coverage:
- SM-01: append-only enforcement (UPDATE/DELETE rejected by DB trigger)
- SM-02: happy path — valid event writes INITIATED then VALIDATED
- SM-03: failure path — invalid event writes INITIATED then FAILED
- SM-04: downstream publish — validated event appears on payment.transaction.validated
- RATELIMIT-01: rate limiter allows <=100, blocks >100 per minute bucket
- D-07: validate_event propagates merchant_id into ValidatedPaymentEvent
"""

import json
import os
import time
from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from confluent_kafka import Consumer
from confluent_kafka.admin import AdminClient, NewTopic
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_raw_event(**overrides: Any) -> dict[str, Any]:
    """Return a valid raw event dict matching WebhookReceivedMessage shape."""
    base: dict[str, Any] = {
        "stripe_event_id": f"evt_test_{uuid4().hex[:12]}",
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


def _bootstrap_servers() -> str:
    return os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


def _db_engine():
    """Create a standalone SQLAlchemy engine for tests that need direct queries."""
    url = os.getenv(
        "DATABASE_URL_SYNC",
        "postgresql://payment:payment@localhost:5432/payment_db",
    )
    return create_engine(url)


# ---------------------------------------------------------------------------
# SM-02: Happy path — INITIATED → VALIDATED
# ---------------------------------------------------------------------------


def test_state_machine_valid_event_writes_initiated_then_validated(db_engine):
    """Valid event writes INITIATED then VALIDATED transitions to payment_state_log.

    Verifies:
    - Exactly 2 rows written for the transaction
    - Row 0: from_state IS NULL, to_state = 'INITIATED'
    - Row 1: from_state = 'INITIATED', to_state = 'VALIDATED'
    """
    from services.state_machine import PaymentStateMachine

    transaction_id = f"test_{uuid4().hex[:8]}"
    event_id = f"evt_test_{uuid4().hex[:8]}"
    merchant_id = "test_merchant_sm02"

    sm = PaymentStateMachine(db_engine)
    sm.record_initiated(transaction_id, event_id, merchant_id)
    sm.record_validated(transaction_id, event_id, merchant_id)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT from_state, to_state FROM payment_state_log "
                "WHERE transaction_id = :tid ORDER BY id ASC"
            ),
            {"tid": transaction_id},
        ).fetchall()

    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    assert rows[0].from_state is None, f"Row 0 from_state should be NULL, got {rows[0].from_state!r}"
    assert rows[0].to_state == "INITIATED", f"Row 0 to_state should be INITIATED, got {rows[0].to_state!r}"
    assert rows[1].from_state == "INITIATED", f"Row 1 from_state should be INITIATED, got {rows[1].from_state!r}"
    assert rows[1].to_state == "VALIDATED", f"Row 1 to_state should be VALIDATED, got {rows[1].to_state!r}"


# ---------------------------------------------------------------------------
# SM-03: Failure path — INITIATED → FAILED
# ---------------------------------------------------------------------------


def test_state_machine_failed_event_writes_initiated_then_failed(db_engine):
    """Invalid/rate-limited event writes INITIATED then FAILED to payment_state_log.

    Verifies:
    - Exactly 2 rows written for the transaction
    - Row 0: from_state IS NULL, to_state = 'INITIATED'
    - Row 1: from_state = 'INITIATED', to_state = 'FAILED'
    """
    from services.state_machine import PaymentStateMachine

    transaction_id = f"test_{uuid4().hex[:8]}"
    event_id = f"evt_test_{uuid4().hex[:8]}"
    merchant_id = "test_merchant_sm03"

    sm = PaymentStateMachine(db_engine)
    sm.record_initiated(transaction_id, event_id, merchant_id)
    sm.record_failed(transaction_id, event_id, merchant_id)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT from_state, to_state FROM payment_state_log "
                "WHERE transaction_id = :tid ORDER BY id ASC"
            ),
            {"tid": transaction_id},
        ).fetchall()

    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    assert rows[0].from_state is None
    assert rows[0].to_state == "INITIATED"
    assert rows[1].from_state == "INITIATED"
    assert rows[1].to_state == "FAILED"


# ---------------------------------------------------------------------------
# SM-01: Append-only enforcement — UPDATE rejected
# ---------------------------------------------------------------------------


def test_state_log_rejects_update(db_engine):
    """DB trigger prevents UPDATE on payment_state_log (append-only enforcement).

    Verifies that attempting to UPDATE an existing row raises an exception
    with 'append-only' in the message (PL/pgSQL RAISE EXCEPTION from trigger).
    """
    import sqlalchemy.exc

    from services.state_machine import PaymentStateMachine

    transaction_id = f"test_{uuid4().hex[:8]}"
    event_id = f"evt_test_{uuid4().hex[:8]}"

    sm = PaymentStateMachine(db_engine)
    sm.record_initiated(transaction_id, event_id, "test_merchant_sm01_upd")

    with pytest.raises((sqlalchemy.exc.InternalError, sqlalchemy.exc.ProgrammingError)) as exc_info:
        with db_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE payment_state_log SET to_state = 'HACKED' "
                    "WHERE transaction_id = :tid"
                ),
                {"tid": transaction_id},
            )

    error_msg = str(exc_info.value).lower()
    assert "append-only" in error_msg, (
        f"Expected 'append-only' in error message, got: {error_msg}"
    )


# ---------------------------------------------------------------------------
# SM-01: Append-only enforcement — DELETE rejected
# ---------------------------------------------------------------------------


def test_state_log_rejects_delete(db_engine):
    """DB trigger prevents DELETE on payment_state_log (append-only enforcement).

    Verifies that attempting to DELETE a row raises an exception with
    'append-only' in the message.
    """
    import sqlalchemy.exc

    from services.state_machine import PaymentStateMachine

    transaction_id = f"test_{uuid4().hex[:8]}"
    event_id = f"evt_test_{uuid4().hex[:8]}"

    sm = PaymentStateMachine(db_engine)
    sm.record_initiated(transaction_id, event_id, "test_merchant_sm01_del")

    with pytest.raises((sqlalchemy.exc.InternalError, sqlalchemy.exc.ProgrammingError)) as exc_info:
        with db_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM payment_state_log WHERE transaction_id = :tid"
                ),
                {"tid": transaction_id},
            )

    error_msg = str(exc_info.value).lower()
    assert "append-only" in error_msg, (
        f"Expected 'append-only' in error message, got: {error_msg}"
    )


# ---------------------------------------------------------------------------
# RATELIMIT-01: Rate limiter allows under the limit
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_under_limit(rate_limiter, clean_rate_limit_keys):
    """MerchantRateLimiter allows 100 requests in the same minute bucket.

    Verifies that all 100 calls return False (not rate-limited).
    """
    merchant_id = "test_merchant_under"
    results = [rate_limiter.is_rate_limited(merchant_id) for _ in range(100)]
    assert all(r is False for r in results), (
        f"Expected all 100 calls to return False; got {sum(results)} blocked"
    )


# ---------------------------------------------------------------------------
# RATELIMIT-01: Rate limiter blocks over the limit (D-22)
# ---------------------------------------------------------------------------


def test_rate_limiter_blocks_over_limit(rate_limiter, clean_rate_limit_keys):
    """MerchantRateLimiter blocks the 101st request in the same minute bucket.

    Verifies:
    - Calls 1-100 return False (allowed)
    - Call 101 returns True (rate-limited)
    """
    merchant_id = "test_merchant_over"
    results = [rate_limiter.is_rate_limited(merchant_id) for _ in range(101)]
    assert results[-1] is True, (
        f"Expected 101st call to return True (blocked); got {results[-1]}"
    )
    assert all(r is False for r in results[:100]), (
        f"Expected first 100 calls to return False; {sum(results[:100])} were blocked"
    )


# ---------------------------------------------------------------------------
# SM-04: Downstream Kafka publish (D-21)
# ---------------------------------------------------------------------------


def test_validated_event_published_to_kafka():
    """ValidatedEventProducer publishes events consumable from payment.transaction.validated.

    Uses a UUID-suffixed topic name to avoid cross-test contamination.
    Creates the test topic via AdminClient (auto-create is disabled in docker-compose).
    Publishes one event, then polls a Consumer for up to 15 seconds to confirm receipt.
    """
    import kafka.producers.validated_event_producer as vep_module

    bootstrap = _bootstrap_servers()
    test_topic = f"payment.transaction.validated.{uuid4().hex[:8]}"
    stripe_event_id = f"evt_test_{uuid4().hex[:8]}"

    # Create test topic via AdminClient
    admin = AdminClient({"bootstrap.servers": bootstrap})
    fs = admin.create_topics([NewTopic(test_topic, num_partitions=1, replication_factor=1)])
    for topic, future in fs.items():
        try:
            future.result()
        except Exception as exc:
            pytest.fail(f"Failed to create test topic {topic}: {exc}")

    # Give broker a moment to propagate topic metadata
    time.sleep(1)

    # Monkeypatch TOPIC constant so ValidatedEventProducer writes to test topic
    original_topic = vep_module.TOPIC
    vep_module.TOPIC = test_topic

    validated_event = {
        "event_id": stripe_event_id,
        "event_type": "payment_intent.succeeded",
        "amount_cents": 2500,
        "currency": "usd",
        "stripe_customer_id": "cus_test_789",
        "merchant_id": "test_merchant_kafka",
        "received_at": datetime.utcnow().isoformat(),
    }

    from kafka.producers.validated_event_producer import ValidatedEventProducer

    producer = ValidatedEventProducer(bootstrap_servers=bootstrap)
    try:
        producer.publish(stripe_event_id, validated_event)
    finally:
        producer.close()
        vep_module.TOPIC = original_topic  # restore regardless of outcome

    # Consume from the test topic
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": f"test-consumer-{uuid4().hex[:8]}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([test_topic])

    received_message = None
    deadline = time.time() + 15  # 15-second poll window
    try:
        while time.time() < deadline:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                continue
            received_message = msg
            break
    finally:
        consumer.close()

    assert received_message is not None, (
        f"No message received from topic {test_topic} within 15 seconds"
    )

    # Verify payload
    payload = json.loads(received_message.value().decode("utf-8"))
    assert payload["event_id"] == stripe_event_id, (
        f"event_id mismatch: expected {stripe_event_id!r}, got {payload.get('event_id')!r}"
    )
    assert payload["amount_cents"] == 2500
    assert payload["merchant_id"] == "test_merchant_kafka"

    # Verify partition key
    key = received_message.key().decode("utf-8")
    assert key == stripe_event_id, (
        f"Kafka key mismatch: expected {stripe_event_id!r}, got {key!r}"
    )


# ---------------------------------------------------------------------------
# D-07: validate_event propagates merchant_id
# ---------------------------------------------------------------------------


def test_validate_event_with_merchant_id():
    """validate_event() passes merchant_id through to ValidatedPaymentEvent.

    Verifies that the merchant_id parameter (D-07) is correctly set on the
    returned ValidatedPaymentEvent instance.
    """
    from kafka.consumers.validation_logic import validate_event
    from models.validation import ValidatedPaymentEvent

    raw = make_raw_event()
    result = validate_event(raw, merchant_id="test_merchant_abc")

    assert isinstance(result, ValidatedPaymentEvent), (
        f"Expected ValidatedPaymentEvent, got {type(result)}"
    )
    assert result.merchant_id == "test_merchant_abc", (
        f"merchant_id mismatch: expected 'test_merchant_abc', got {result.merchant_id!r}"
    )
