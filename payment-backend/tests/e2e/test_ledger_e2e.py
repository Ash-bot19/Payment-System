"""E2E tests for Phase 06 Plan 02: Ledger consumer pipeline.

These tests exercise the ledger write path end-to-end against live services
(Kafka, PostgreSQL). They skip if the ledger-consumer service is not running.

Tests:
    test_ledger_consumer_health           -- GET /health returns 200 {"status": "ok"}
    test_ledger_write_creates_balanced_entries -- Produce → wait → assert 2 rows (DEBIT+CREDIT)
    test_ledger_write_transitions_to_settled   -- After ledger write, SETTLED row in state log
    test_duplicate_ledger_message_skipped      -- Send same event twice → still only 2 rows

Run (requires full Docker stack):
    cd payment-backend && python -m pytest tests/e2e/test_ledger_e2e.py -v --timeout=30
"""

import json
import os
import sys
import time
from uuid import uuid4

import pytest

# Ensure payment-backend root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Required env vars — default to local dev values for standalone execution
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

LEDGER_CONSUMER_URL = os.getenv("LEDGER_CONSUMER_URL", "http://localhost:8004")
LEDGER_ENTRY_TOPIC = "payment.ledger.entry"


# ---------------------------------------------------------------------------
# Skip helpers
# ---------------------------------------------------------------------------


def _services_available() -> bool:
    """Return True if ledger-consumer /health returns 200."""
    try:
        import urllib.request
        with urllib.request.urlopen(f"{LEDGER_CONSUMER_URL}/health", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _get_db_engine():
    """Return a live SQLAlchemy engine or skip the test."""
    from sqlalchemy import create_engine, text

    url = os.getenv("DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db")
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:
        pytest.skip("PostgreSQL not available — skipping E2E test")


def _get_kafka_producer():
    """Return a live Kafka Producer or skip the test."""
    try:
        from confluent_kafka import Producer
        bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        producer = Producer({"bootstrap.servers": bootstrap})
        return producer
    except Exception:
        pytest.skip("Kafka not available — skipping E2E test")


def _produce_ledger_event(producer, event_id: str, amount_cents: int = 5000) -> None:
    """Produce a single ledger entry event to the Kafka topic."""
    payload = {
        "event_id": event_id,
        "event_type": "payment_intent.succeeded",
        "amount_cents": amount_cents,
        "currency": "usd",
        "merchant_id": "e2e_test_merchant",
        "source_event_id": event_id,
        "stripe_customer_id": "cus_e2e_test",
        "risk_score": 0.12,
    }
    producer.produce(
        topic=LEDGER_ENTRY_TOPIC,
        key=event_id.encode("utf-8"),
        value=json.dumps(payload).encode("utf-8"),
    )
    producer.flush(timeout=10)


def _wait_for_ledger_entries(engine, event_id: str, expected_count: int = 2, timeout: int = 10) -> list:
    """Poll ledger_entries table until expected_count rows appear or timeout."""
    from sqlalchemy import text

    deadline = time.time() + timeout
    while time.time() < deadline:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT entry_type, amount_cents FROM ledger_entries "
                    "WHERE transaction_id = :tid ORDER BY id ASC"
                ),
                {"tid": event_id},
            ).fetchall()
        if len(rows) >= expected_count:
            return rows
        time.sleep(0.5)
    return []


def _wait_for_settled_state(engine, event_id: str, timeout: int = 10) -> bool:
    """Poll payment_state_log until SETTLED row appears or timeout."""
    from sqlalchemy import text

    deadline = time.time() + timeout
    while time.time() < deadline:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT 1 FROM payment_state_log "
                    "WHERE event_id = :eid AND to_state = 'SETTLED' LIMIT 1"
                ),
                {"eid": event_id},
            ).fetchone()
        if row is not None:
            return True
        time.sleep(0.5)
    return False


requires_services = pytest.mark.skipif(
    not _services_available(),
    reason="ledger-consumer not running at localhost:8004 — skipping E2E tests",
)


# ---------------------------------------------------------------------------
# Test 1: Health endpoint
# ---------------------------------------------------------------------------


@requires_services
def test_ledger_consumer_health():
    """GET /health must return 200 with {"status": "ok"}."""
    import urllib.request
    import json as json_lib

    with urllib.request.urlopen(f"{LEDGER_CONSUMER_URL}/health", timeout=5) as resp:
        assert resp.status == 200, f"Expected 200, got {resp.status}"
        body = json_lib.loads(resp.read().decode())
        assert body.get("status") == "ok", f"Expected status=ok, got: {body}"


# ---------------------------------------------------------------------------
# Test 2: Balanced ledger entries written
# ---------------------------------------------------------------------------


@requires_services
def test_ledger_write_creates_balanced_entries():
    """Produce ledger entry event → assert exactly 2 rows (1 DEBIT + 1 CREDIT)."""
    engine = _get_db_engine()
    producer = _get_kafka_producer()

    event_id = f"e2e_ledger_{uuid4().hex[:12]}"

    _produce_ledger_event(producer, event_id, amount_cents=7500)

    rows = _wait_for_ledger_entries(engine, event_id, expected_count=2, timeout=10)

    assert len(rows) == 2, (
        f"Expected exactly 2 ledger_entries rows for event_id={event_id}, got {len(rows)}"
    )

    entry_types = {r.entry_type for r in rows}
    assert "DEBIT" in entry_types, f"Expected DEBIT row, got entry_types={entry_types}"
    assert "CREDIT" in entry_types, f"Expected CREDIT row, got entry_types={entry_types}"

    for row in rows:
        assert row.amount_cents == 7500, (
            f"Expected amount_cents=7500, got {row.amount_cents}"
        )


# ---------------------------------------------------------------------------
# Test 3: State transitions to SETTLED
# ---------------------------------------------------------------------------


@requires_services
def test_ledger_write_transitions_to_settled():
    """After ledger write, payment_state_log must contain a SETTLED row."""
    engine = _get_db_engine()
    producer = _get_kafka_producer()

    event_id = f"e2e_settle_{uuid4().hex[:12]}"

    _produce_ledger_event(producer, event_id)

    settled = _wait_for_settled_state(engine, event_id, timeout=10)

    assert settled, (
        f"Expected SETTLED row in payment_state_log for event_id={event_id} within 10s"
    )


# ---------------------------------------------------------------------------
# Test 4: Duplicate message idempotency
# ---------------------------------------------------------------------------


@requires_services
def test_duplicate_ledger_message_skipped():
    """Sending the same event_id twice must still result in exactly 2 ledger rows."""
    engine = _get_db_engine()
    producer = _get_kafka_producer()

    event_id = f"e2e_dedup_{uuid4().hex[:12]}"

    # Produce same event twice
    _produce_ledger_event(producer, event_id, amount_cents=3000)
    time.sleep(0.5)  # brief pause to allow first message to be processed
    _produce_ledger_event(producer, event_id, amount_cents=3000)

    # Wait for processing to complete
    rows = _wait_for_ledger_entries(engine, event_id, expected_count=2, timeout=15)

    assert len(rows) == 2, (
        f"Idempotency failed: expected 2 rows after duplicate message, got {len(rows)}"
    )
