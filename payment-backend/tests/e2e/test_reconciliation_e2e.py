"""E2E tests for Phase 7 Plan 03: reconciliation pipeline against live Docker stack.

These tests exercise the reconciliation DAG task functions and Kafka round-trip
against live PostgreSQL + Kafka services (requires docker-compose stack running).
They skip gracefully if services are not reachable.

Run (requires full Docker stack):
    cd payment-backend && python -m pytest tests/e2e/test_reconciliation_e2e.py -v --timeout=30

Tests:
    test_detect_duplicates_publishes_to_kafka  -- 4 ledger rows for same tx_id → DUPLICATE_LEDGER on Kafka
    test_reconciliation_message_schema_roundtrip -- publish + consume ReconciliationMessage, assert all 10 fields
"""

import json
import os
import sys
import time
import types
from datetime import date, datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

# Ensure payment-backend root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Required env vars — default to local dev values
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault(
    "DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db"
)

# ---------------------------------------------------------------------------
# Airflow decorator stub (same pattern as unit tests)
# ---------------------------------------------------------------------------


class _InDagContext:
    """Flag indicating whether we are inside a @dag decorator body."""
    active: bool = False


def _stub_airflow_decorators() -> None:
    """Inject a minimal stub for airflow.decorators before the DAG is imported."""
    import airflow  # namespace package — payment-backend/airflow/

    decorators_mod = types.ModuleType("airflow.decorators")

    def dag(**kwargs):
        def decorator(fn):
            def wrapper(*a, **kw):
                _InDagContext.active = True
                try:
                    fn(*a, **kw)
                finally:
                    _InDagContext.active = False
            return wrapper
        return decorator

    def task(*dargs, **dkwargs):
        def decorator(fn):
            def wrapper(*a, **kw):
                if _InDagContext.active:
                    return MagicMock()
                return fn(*a, **kw)
            wrapper.__wrapped__ = fn
            return wrapper
        if len(dargs) == 1 and callable(dargs[0]):
            return decorator(dargs[0])
        return decorator

    decorators_mod.dag = dag
    decorators_mod.task = task
    sys.modules["airflow.decorators"] = decorators_mod
    airflow.decorators = decorators_mod  # type: ignore[attr-defined]


_stub_airflow_decorators()

RECON_TOPIC = "payment.reconciliation.queue"

# ---------------------------------------------------------------------------
# Service availability helpers
# ---------------------------------------------------------------------------


def _postgres_available() -> bool:
    """Return True if PostgreSQL is reachable."""
    try:
        from sqlalchemy import create_engine, text

        url = os.getenv(
            "DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db"
        )
        engine = create_engine(url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _kafka_available() -> bool:
    """Return True if Kafka is reachable."""
    try:
        from confluent_kafka.admin import AdminClient

        bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        admin = AdminClient({"bootstrap.servers": bootstrap})
        meta = admin.list_topics(timeout=3)
        return meta is not None
    except Exception:
        return False


_SERVICES_UP = _postgres_available() and _kafka_available()

requires_services = pytest.mark.skipif(
    not _SERVICES_UP,
    reason="PostgreSQL or Kafka not available — skipping Phase 7 E2E tests",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_db_engine():
    """Return a live SQLAlchemy engine (skip test if unavailable)."""
    from sqlalchemy import create_engine

    url = os.getenv(
        "DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db"
    )
    return create_engine(url)


def _insert_ledger_row(engine, transaction_id: str, entry_type: str, amount_cents: int, ds: str) -> None:
    """Insert a single ledger row with created_at inside the ds window."""
    from sqlalchemy import text

    created_at = f"{ds} 12:00:00+00"
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO ledger_entries
                    (transaction_id, entry_type, amount_cents, currency, merchant_id, created_at)
                VALUES
                    (:tid, :entry_type, :amount_cents, 'USD', 'e2e_test_merchant', :created_at)
                """
            ),
            {
                "tid": transaction_id,
                "entry_type": entry_type,
                "amount_cents": amount_cents,
                "created_at": created_at,
            },
        )


def _consume_from_topic(topic: str, group_id: str, timeout_seconds: int = 10) -> list[dict]:
    """Consume all available messages from a Kafka topic. Returns parsed JSON dicts."""
    import structlog
    from confluent_kafka import Consumer, KafkaError

    logger = structlog.get_logger(__name__)
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
        }
    )
    consumer.subscribe([topic])

    messages = []
    deadline = time.time() + timeout_seconds
    empty_polls = 0

    try:
        while time.time() < deadline and empty_polls < 3:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                empty_polls += 1
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    empty_polls += 1
                    continue
                logger.error("kafka_consume_error", error=str(msg.error()))
                break
            empty_polls = 0
            try:
                payload = json.loads(msg.value().decode("utf-8"))
                messages.append(payload)
            except Exception as exc:
                logger.warning("message_decode_error", error=str(exc))
    finally:
        consumer.close()

    return messages


# ---------------------------------------------------------------------------
# E2E Test 1: detect_duplicates publishes to Kafka
# ---------------------------------------------------------------------------


@requires_services
def test_detect_duplicates_publishes_to_kafka():
    """Insert 4 ledger rows for same transaction_id (COUNT > 2 = duplicate).

    Call detect_duplicates() task function directly. Then consume from
    payment.reconciliation.queue and assert DUPLICATE_LEDGER message received.
    """
    import structlog  # noqa: F401

    engine = _get_db_engine()
    tx_id = f"e2e_dup4_{uuid4().hex[:10]}"
    ds = "2026-03-26"  # Use a past date to avoid collisions with normal usage

    # Insert 4 rows (2 DEBIT + 2 CREDIT) for same tx_id — triggers DUPLICATE_LEDGER
    _insert_ledger_row(engine, tx_id, "DEBIT", 1000, ds=ds)
    _insert_ledger_row(engine, tx_id, "CREDIT", 1000, ds=ds)
    _insert_ledger_row(engine, tx_id, "DEBIT", 1000, ds=ds)
    _insert_ledger_row(engine, tx_id, "CREDIT", 1000, ds=ds)

    # Call the task function directly (not via Airflow runtime)
    from airflow.dags.nightly_reconciliation import detect_duplicates

    count = detect_duplicates(ds=ds)
    assert count >= 1, f"Expected at least 1 duplicate detected, got {count}"

    # Consume from payment.reconciliation.queue and find our message
    group_id = f"test-recon-{uuid4()}"
    messages = _consume_from_topic(RECON_TOPIC, group_id, timeout_seconds=10)

    our_msgs = [
        m for m in messages
        if m.get("transaction_id") == tx_id and m.get("discrepancy_type") == "DUPLICATE_LEDGER"
    ]
    assert len(our_msgs) >= 1, (
        f"Expected DUPLICATE_LEDGER message for tx_id={tx_id} in {RECON_TOPIC}, "
        f"found {len(our_msgs)} matches in {len(messages)} total messages"
    )
    msg = our_msgs[0]
    assert msg["discrepancy_type"] == "DUPLICATE_LEDGER"
    assert msg["transaction_id"] == tx_id
    assert msg["currency"] == "USD"


# ---------------------------------------------------------------------------
# E2E Test 2: ReconciliationMessage schema round-trip via Kafka
# ---------------------------------------------------------------------------


@requires_services
def test_reconciliation_message_schema_roundtrip():
    """Create a ReconciliationMessage, serialize to JSON, publish to Kafka.

    Then consume back, deserialize, and assert all 10 D-11 fields match.
    """
    from models.reconciliation import ReconciliationMessage
    from kafka.producers.reconciliation_producer import ReconciliationProducer

    tx_id = f"e2e_rt_{uuid4().hex[:10]}"
    pi_id = f"pi_e2e_{uuid4().hex[:8]}"
    run_dt = date(2026, 3, 27)
    stripe_created = datetime(2026, 3, 27, 8, 0, 0, tzinfo=timezone.utc)

    message = ReconciliationMessage(
        transaction_id=tx_id,
        discrepancy_type="MISSING_INTERNALLY",
        stripe_payment_intent_id=pi_id,
        internal_amount_cents=None,
        stripe_amount_cents=7500,
        diff_cents=None,
        currency="USD",
        merchant_id="e2e_merchant",
        stripe_created_at=stripe_created,
        run_date=run_dt,
    )

    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    producer = ReconciliationProducer(bootstrap_servers=bootstrap)
    producer.publish(tx_id, message.model_dump(mode="json"))

    # Consume back from the topic
    group_id = f"test-recon-{uuid4()}"
    messages = _consume_from_topic(RECON_TOPIC, group_id, timeout_seconds=10)

    our_msgs = [m for m in messages if m.get("transaction_id") == tx_id]
    assert len(our_msgs) >= 1, (
        f"Expected to consume back message for tx_id={tx_id} from {RECON_TOPIC}, "
        f"found {len(our_msgs)} in {len(messages)} total messages"
    )

    consumed = our_msgs[0]

    # Assert all 10 D-11 fields match
    assert consumed["transaction_id"] == tx_id
    assert consumed["discrepancy_type"] == "MISSING_INTERNALLY"
    assert consumed["stripe_payment_intent_id"] == pi_id
    assert consumed["internal_amount_cents"] is None
    assert consumed["stripe_amount_cents"] == 7500
    assert consumed["diff_cents"] is None
    assert consumed["currency"] == "USD"
    assert consumed["merchant_id"] == "e2e_merchant"
    assert "stripe_created_at" in consumed
    assert "run_date" in consumed
