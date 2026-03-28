"""Integration tests for Phase 7 Plan 03: nightly_reconciliation DAG tasks.

Tests run against REAL PostgreSQL (requires docker-compose postgres running).
Stripe API and Kafka are MOCKED — no external service credentials needed.

Run (requires postgres running):
    cd payment-backend && python -m pytest tests/integration/test_phase7_integration.py -v

Tests:
    test_detect_duplicates_finds_tripled_entries    -- 3 DEBIT+CREDIT pairs → DUPLICATE_LEDGER published
    test_detect_duplicates_ignores_normal_pairs     -- 2 rows per tx (1 DEBIT+1 CREDIT) → no publish
    test_compare_and_publish_type1_missing_internally -- no ledger rows, Stripe has PI → MISSING_INTERNALLY
    test_compare_and_publish_type3_amount_mismatch  -- DEBIT=1000, Stripe=1500 → AMOUNT_MISMATCH diff=500
    test_compare_and_publish_clean_match            -- DEBIT=1000, Stripe=1000 → no discrepancy published
"""

import os
import sys
from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import types

import pytest
from sqlalchemy import create_engine, text

# Ensure payment-backend root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Required env vars for local dev
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault(
    "DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db"
)

# ---------------------------------------------------------------------------
# Airflow decorator stub
#
# airflow resolves to the local payment-backend/airflow/ namespace package.
# We must stub airflow.decorators before importing the DAG module so that
# @dag and @task are no-ops. Same approach as the unit tests.
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

# ---------------------------------------------------------------------------
# PostgreSQL availability check
# ---------------------------------------------------------------------------

_DB_URL = os.getenv(
    "DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db"
)


def _postgres_available() -> bool:
    """Return True if PostgreSQL is reachable at DATABASE_URL_SYNC."""
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(_DB_URL, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="PostgreSQL not available — skipping Phase 7 integration tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def db_engine():
    """Session-scoped SQLAlchemy engine for real PostgreSQL."""
    engine = create_engine(_DB_URL)
    yield engine
    engine.dispose()


def _insert_ledger_pair(
    engine,
    transaction_id: str,
    amount_cents: int,
    merchant_id: str = "test_merchant",
    ds: str = "2026-03-27",
) -> None:
    """Insert a balanced DEBIT+CREDIT pair into ledger_entries in one transaction.

    The balance trigger (DEFERRABLE INITIALLY DEFERRED) fires at COMMIT, so both
    rows must be inserted in the same transaction to satisfy SUM(amount_cents) = 0.
    compare_and_publish only queries DEBIT rows, so this is safe for all test cases.
    """
    created_at = f"{ds} 12:00:00+00"
    insert_sql = text(
        """
        INSERT INTO ledger_entries
            (transaction_id, entry_type, amount_cents, currency, merchant_id, source_event_id, created_at)
        VALUES
            (:tid, :entry_type, :amount_cents, 'USD', :merchant_id, :source_event_id, :created_at)
        """
    )
    with engine.begin() as conn:
        for entry_type in ("DEBIT", "CREDIT"):
            conn.execute(
                insert_sql,
                {
                    "tid": transaction_id,
                    "entry_type": entry_type,
                    "amount_cents": amount_cents,
                    "merchant_id": merchant_id,
                    "source_event_id": transaction_id,
                    "created_at": created_at,
                },
            )


# ---------------------------------------------------------------------------
# Test 1: detect_duplicates finds tripled entries
# ---------------------------------------------------------------------------


@requires_postgres
def test_detect_duplicates_finds_tripled_entries(db_engine):
    """Insert 3 DEBIT+CREDIT pairs for same transaction_id.

    detect_duplicates should detect COUNT(*) > 2 and publish
    exactly 1 DUPLICATE_LEDGER message via ReconciliationProducer.
    """
    import structlog  # noqa: F401 — must be importable

    # Use a unique transaction_id for isolation (append-only: no DELETE)
    tx_id = f"integ_dup3_{uuid4().hex[:10]}"
    ds = "2026-03-27"

    # Insert 6 rows = 3 pairs (all same transaction_id, violating the 2-row contract)
    for _ in range(3):
        _insert_ledger_pair(db_engine, tx_id, 1000, ds=ds)

    mock_producer = MagicMock()

    with patch(
        "airflow.dags.nightly_reconciliation.ReconciliationProducer",
        return_value=mock_producer,
    ):
        from airflow.dags.nightly_reconciliation import detect_duplicates

        result = detect_duplicates(ds=ds)

    assert result >= 1, f"Expected at least 1 duplicate transaction, got {result}"
    mock_producer.publish_batch.assert_called_once()

    published = mock_producer.publish_batch.call_args[0][0]
    # Find the message for our transaction_id
    our_msgs = [m for m in published if m["transaction_id"] == tx_id]
    assert len(our_msgs) == 1, (
        f"Expected 1 DUPLICATE_LEDGER for tx_id={tx_id}, got {len(our_msgs)}"
    )
    assert our_msgs[0]["discrepancy_type"] == "DUPLICATE_LEDGER"


# ---------------------------------------------------------------------------
# Test 2: detect_duplicates ignores normal pairs (exactly 2 rows)
# ---------------------------------------------------------------------------


@requires_postgres
def test_detect_duplicates_ignores_normal_pairs(db_engine):
    """Insert exactly 2 rows (1 DEBIT + 1 CREDIT) for a fresh transaction_id.

    detect_duplicates should NOT publish any message for this transaction.
    """
    tx_id = f"integ_ok_pair_{uuid4().hex[:10]}"
    ds = "2026-03-27"

    _insert_ledger_pair(db_engine, tx_id, 2000, ds=ds)

    mock_producer = MagicMock()

    with patch(
        "airflow.dags.nightly_reconciliation.ReconciliationProducer",
        return_value=mock_producer,
    ):
        from airflow.dags.nightly_reconciliation import detect_duplicates

        detect_duplicates(ds=ds)

    # If publish_batch was called at all, our tx_id must NOT be in the published list
    if mock_producer.publish_batch.called:
        published = mock_producer.publish_batch.call_args[0][0]
        tx_ids = [m["transaction_id"] for m in published]
        assert tx_id not in tx_ids, (
            f"Normal pair {tx_id} should not appear in DUPLICATE_LEDGER messages"
        )


# ---------------------------------------------------------------------------
# Test 3: compare_and_publish — MISSING_INTERNALLY (Type 1)
# ---------------------------------------------------------------------------


@requires_postgres
def test_compare_and_publish_type1_missing_internally(db_engine):
    """No ledger rows for a transaction_id; Stripe has a succeeded PI.

    compare_and_publish should publish 1 MISSING_INTERNALLY message.
    """
    pi_id = f"pi_missing_{uuid4().hex[:10]}"
    ds = "2026-03-27"

    # No ledger rows inserted for pi_id — it is missing internally

    # Mock Stripe data: PI exists on Stripe side
    stripe_intents = {
        pi_id: {
            "amount": 5000,
            "currency": "usd",
            "created": int(datetime(2026, 3, 27, 10, 0, 0, tzinfo=timezone.utc).timestamp()),
            "metadata": {"merchant_id": "test_merchant"},
        }
    }

    mock_producer = MagicMock()

    with patch(
        "airflow.dags.nightly_reconciliation.ReconciliationProducer",
        return_value=mock_producer,
    ):
        from airflow.dags.nightly_reconciliation import compare_and_publish

        compare_and_publish(stripe_intents=stripe_intents, ds=ds)

    mock_producer.publish_batch.assert_called_once()
    published = mock_producer.publish_batch.call_args[0][0]

    our_msgs = [m for m in published if m["transaction_id"] == pi_id]
    assert len(our_msgs) == 1, (
        f"Expected 1 MISSING_INTERNALLY message for {pi_id}, got {len(our_msgs)}"
    )
    msg = our_msgs[0]
    assert msg["discrepancy_type"] == "MISSING_INTERNALLY"
    assert msg["stripe_amount_cents"] == 5000
    assert msg["internal_amount_cents"] is None


# ---------------------------------------------------------------------------
# Test 4: compare_and_publish — AMOUNT_MISMATCH (Type 3)
# ---------------------------------------------------------------------------


@requires_postgres
def test_compare_and_publish_type3_amount_mismatch(db_engine):
    """Insert DEBIT row with amount_cents=1000; Stripe has amount=1500.

    compare_and_publish should publish 1 AMOUNT_MISMATCH message with diff_cents=500.
    """
    pi_id = f"pi_mismatch_{uuid4().hex[:10]}"
    ds = "2026-03-27"

    # Balanced pair with DEBIT(1000) — compare_and_publish queries only DEBIT rows
    _insert_ledger_pair(db_engine, pi_id, 1000, ds=ds)

    # Stripe says 1500 cents — mismatch of 500
    stripe_intents = {
        pi_id: {
            "amount": 1500,
            "currency": "usd",
            "created": int(datetime(2026, 3, 27, 10, 0, 0, tzinfo=timezone.utc).timestamp()),
            "metadata": {"merchant_id": "test_merchant"},
        }
    }

    mock_producer = MagicMock()

    with patch(
        "airflow.dags.nightly_reconciliation.ReconciliationProducer",
        return_value=mock_producer,
    ):
        from airflow.dags.nightly_reconciliation import compare_and_publish

        compare_and_publish(stripe_intents=stripe_intents, ds=ds)

    mock_producer.publish_batch.assert_called_once()
    published = mock_producer.publish_batch.call_args[0][0]

    our_msgs = [m for m in published if m["transaction_id"] == pi_id]
    assert len(our_msgs) == 1, (
        f"Expected 1 AMOUNT_MISMATCH message for {pi_id}, got {len(our_msgs)}"
    )
    msg = our_msgs[0]
    assert msg["discrepancy_type"] == "AMOUNT_MISMATCH"
    assert msg["internal_amount_cents"] == 1000
    assert msg["diff_cents"] == 500  # stripe(1500) - internal(1000)
    assert msg["stripe_amount_cents"] is None  # per D-11


# ---------------------------------------------------------------------------
# Test 5: compare_and_publish — clean match (no discrepancy)
# ---------------------------------------------------------------------------


@requires_postgres
def test_compare_and_publish_clean_match(db_engine):
    """Insert DEBIT row with amount_cents=1000; Stripe also has amount=1000.

    compare_and_publish should NOT publish any discrepancy.
    """
    pi_id = f"pi_clean_{uuid4().hex[:10]}"
    ds = "2026-03-27"

    # Balanced pair with DEBIT(1000) — compare_and_publish queries only DEBIT rows
    _insert_ledger_pair(db_engine, pi_id, 1000, ds=ds)

    # Stripe agrees: also 1000 cents
    stripe_intents = {
        pi_id: {
            "amount": 1000,
            "currency": "usd",
            "created": int(datetime(2026, 3, 27, 10, 0, 0, tzinfo=timezone.utc).timestamp()),
            "metadata": {"merchant_id": "test_merchant"},
        }
    }

    mock_producer = MagicMock()

    with patch(
        "airflow.dags.nightly_reconciliation.ReconciliationProducer",
        return_value=mock_producer,
    ):
        from airflow.dags.nightly_reconciliation import compare_and_publish

        compare_and_publish(stripe_intents=stripe_intents, ds=ds)

    # Should not publish anything (clean match is silently skipped)
    mock_producer.publish_batch.assert_not_called()
