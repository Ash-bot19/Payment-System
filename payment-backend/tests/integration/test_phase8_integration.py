"""Integration tests for Phase 8 — persist_discrepancies against live PostgreSQL.

Requires docker-compose PostgreSQL + migration 004 applied.
Run with: INTEGRATION_TEST=1 pytest tests/integration/test_phase8_integration.py -v

Tests:
  - persist_discrepancies writes rows to reconciliation_discrepancies table
  - persist_discrepancies is idempotent (inserting same data twice adds two rows — append-only)
  - persist_discrepancies returns correct row count
  - reconciliation_discrepancies table rejects UPDATE (append-only trigger)
  - reconciliation_discrepancies table rejects DELETE (append-only trigger)

Isolation strategy: each test generates a unique merchant_id via uuid.uuid4() so test
rows are isolated by value. No teardown fixture needed — the table is append-only and
leftover rows keyed by unique UUIDs are harmless. This mirrors the UUID-keyed isolation
pattern used in Phase 3 integration tests for payment_state_log.
"""

import os
import sys
import types
import uuid
from datetime import date
from typing import Any

import pytest

SKIP_REASON = "requires live docker-compose stack (set INTEGRATION_TEST=1)"
pytestmark = pytest.mark.skipif(not os.environ.get("INTEGRATION_TEST"), reason=SKIP_REASON)

DATABASE_URL = os.environ.get(
    "DATABASE_URL_SYNC",
    "postgresql://payment:payment@localhost:5432/payment_db",
)


def _stub_airflow() -> None:
    import airflow

    class _InDagContext:
        active: bool = False

    m = types.ModuleType("airflow.decorators")

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
        from unittest.mock import MagicMock

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

    m.dag = dag
    m.task = task
    sys.modules["airflow.decorators"] = m
    airflow.decorators = m  # type: ignore[attr-defined]


_stub_airflow()
import airflow.dags.nightly_reconciliation as dag_module  # noqa: E402


def _sample_discrepancy(transaction_id: str, merchant_id: str) -> dict[str, Any]:
    """Build a sample discrepancy dict matching ReconciliationMessage.model_dump()."""
    return {
        "transaction_id": transaction_id,
        "discrepancy_type": "AMOUNT_MISMATCH",
        "stripe_payment_intent_id": "pi_test_001",
        "internal_amount_cents": 5000,
        "stripe_amount_cents": None,
        "diff_cents": -100,
        "currency": "USD",
        "merchant_id": merchant_id,
        "stripe_created_at": None,
        "run_date": date(2026, 3, 28),
    }


def test_persist_discrepancies_writes_single_row():
    """persist_discrepancies inserts one row into reconciliation_discrepancies."""
    from sqlalchemy import create_engine, text

    merchant_id = f"merch_test_{uuid.uuid4().hex[:8]}"
    discrepancy = _sample_discrepancy("tx_integration_single", merchant_id)
    count = dag_module.persist_discrepancies([discrepancy], ds="2026-03-28")
    assert count == 1

    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT transaction_id, discrepancy_type FROM reconciliation_discrepancies "
                "WHERE transaction_id = 'tx_integration_single' AND merchant_id = :mid"
            ),
            {"mid": merchant_id},
        )
        rows = list(result)
    assert len(rows) == 1
    assert rows[0].discrepancy_type == "AMOUNT_MISMATCH"


def test_persist_discrepancies_writes_multiple_rows():
    """persist_discrepancies inserts multiple rows in one call."""
    merchant_id = f"merch_test_{uuid.uuid4().hex[:8]}"
    discrepancies = [
        _sample_discrepancy(f"tx_integration_multi_{merchant_id}_001", merchant_id),
        _sample_discrepancy(f"tx_integration_multi_{merchant_id}_002", merchant_id),
        _sample_discrepancy(f"tx_integration_multi_{merchant_id}_003", merchant_id),
    ]
    count = dag_module.persist_discrepancies(discrepancies, ds="2026-03-28")
    assert count == 3

    from sqlalchemy import create_engine, text
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT COUNT(*) as cnt FROM reconciliation_discrepancies "
                "WHERE merchant_id = :mid"
            ),
            {"mid": merchant_id},
        )
        row = next(iter(result))
    assert row.cnt == 3


def test_persist_discrepancies_empty_list_no_op():
    """persist_discrepancies returns 0 and writes nothing for empty input."""
    count = dag_module.persist_discrepancies([], ds="2026-03-28")
    assert count == 0


def test_reconciliation_discrepancies_append_only_rejects_update():
    """UPDATE on reconciliation_discrepancies raises exception (append-only trigger)."""
    import psycopg2

    merchant_id = f"merch_test_{uuid.uuid4().hex[:8]}"
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO reconciliation_discrepancies
           (transaction_id, discrepancy_type, merchant_id, run_date)
           VALUES (%s, 'DUPLICATE_LEDGER', %s, '2026-03-28')""",
        (f"tx_trigger_{merchant_id}", merchant_id),
    )
    conn.commit()

    # Attempt UPDATE — must fail
    with pytest.raises(Exception, match="append-only"):
        cur.execute(
            "UPDATE reconciliation_discrepancies SET discrepancy_type = 'AMOUNT_MISMATCH' "
            "WHERE merchant_id = %s",
            (merchant_id,),
        )
        conn.commit()
    conn.rollback()
    cur.close()
    conn.close()


def test_reconciliation_discrepancies_append_only_rejects_delete():
    """DELETE on reconciliation_discrepancies raises exception (append-only trigger)."""
    import psycopg2

    merchant_id = f"merch_test_{uuid.uuid4().hex[:8]}"
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO reconciliation_discrepancies
           (transaction_id, discrepancy_type, merchant_id, run_date)
           VALUES (%s, 'MISSING_INTERNALLY', %s, '2026-03-28')""",
        (f"tx_delete_{merchant_id}", merchant_id),
    )
    conn.commit()

    with pytest.raises(Exception, match="append-only"):
        cur.execute(
            "DELETE FROM reconciliation_discrepancies WHERE merchant_id = %s",
            (merchant_id,),
        )
        conn.commit()
    conn.rollback()
    cur.close()
    conn.close()
