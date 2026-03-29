"""Integration tests for seed_demo_data.py — Phase 09.

Requires a live PostgreSQL instance (skips gracefully otherwise).
Run from payment-backend root:
    DATABASE_URL_SYNC=postgresql://payment:payment@localhost:5432/payment_db \
        python -m pytest tests/integration/test_seed_demo_data.py -v
"""

import os
import subprocess
import sys

import pytest
import structlog

logger = structlog.get_logger(__name__)

psycopg2 = pytest.importorskip("psycopg2")

DATABASE_URL = os.environ.get(
    "DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db"
)


@pytest.fixture(scope="module")
def db_conn():
    """Module-scoped psycopg2 connection. Skips if PostgreSQL not available."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
    except Exception as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")
    yield conn
    conn.close()


def _call_main():
    """Import and run seed_demo_data.main() with payment-backend on sys.path."""
    pb_root = os.path.join(os.path.dirname(__file__), "..", "..")
    pb_root = os.path.abspath(pb_root)
    if pb_root not in sys.path:
        sys.path.insert(0, pb_root)

    # Override DATABASE_URL for the seed script to use the test DB URL
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = DATABASE_URL
    try:
        from scripts.seed_demo_data import main  # noqa: PLC0415

        main()
    finally:
        if old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_url


# ─── Tests ────────────────────────────────────────────────────────────────────


def test_seed_creates_expected_rows(db_conn):
    """Seed populates all 3 tables with minimum expected row counts."""
    _call_main()

    with db_conn.cursor() as cur:
        # payment_state_log: 8*5 + 4*4 + 3*4 = 68 rows expected (>= 20 minimum)
        cur.execute(
            "SELECT COUNT(*) FROM payment_state_log WHERE event_id LIKE 'seed_evt_%'"
        )
        state_count = cur.fetchone()[0]
        assert state_count >= 20, f"Expected >= 20 state_log rows, got {state_count}"

        # ledger_entries: 8 * 2 = 16 rows
        cur.execute(
            "SELECT COUNT(*) FROM ledger_entries WHERE source_event_id LIKE 'seed_evt_%'"
        )
        ledger_count = cur.fetchone()[0]
        assert ledger_count >= 16, f"Expected >= 16 ledger rows, got {ledger_count}"

        # reconciliation_discrepancies: 5 rows
        cur.execute(
            """
            SELECT COUNT(*) FROM reconciliation_discrepancies
            WHERE stripe_payment_intent_id LIKE 'pi_seed_%'
            """
        )
        disc_count = cur.fetchone()[0]
        assert disc_count >= 5, f"Expected >= 5 discrepancy rows, got {disc_count}"

    logger.info(
        "test_seed_creates_expected_rows_passed",
        state_rows=state_count,
        ledger_rows=ledger_count,
        discrepancy_rows=disc_count,
    )


def test_ledger_balance_constraint(db_conn):
    """All seeded ledger pairs have zero balance (DEBIT == CREDIT per transaction)."""
    _call_main()

    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT transaction_id,
                   SUM(CASE WHEN entry_type = 'CREDIT' THEN amount_cents ELSE 0 END)
                   - SUM(CASE WHEN entry_type = 'DEBIT'  THEN amount_cents ELSE 0 END) AS balance
            FROM ledger_entries
            WHERE source_event_id LIKE 'seed_evt_%'
            GROUP BY transaction_id
            """
        )
        rows = cur.fetchall()

    assert rows, "No seeded ledger rows found"
    imbalanced = [(tx, bal) for tx, bal in rows if bal != 0]
    assert not imbalanced, f"Imbalanced ledger entries: {imbalanced}"


def test_seed_is_idempotent(db_conn):
    """Running seed twice produces identical row counts (no duplicates)."""
    _call_main()

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM payment_state_log WHERE event_id LIKE 'seed_evt_%'"
        )
        count_after_first = cur.fetchone()[0]

    _call_main()

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM payment_state_log WHERE event_id LIKE 'seed_evt_%'"
        )
        count_after_second = cur.fetchone()[0]

    assert count_after_first == count_after_second, (
        f"Seed is not idempotent: {count_after_first} rows → {count_after_second} rows"
    )


def test_dbt_marts_populated_after_seed(db_conn):
    """dbt marts are non-empty after seeding (skips if dbt not installed)."""
    _call_main()

    # Check if dbt is available
    result = subprocess.run(
        ["dbt", "--version"], capture_output=True, text=True
    )
    if result.returncode != 0:
        pytest.skip("dbt not available")

    pb_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    dbt_result = subprocess.run(
        ["dbt", "run", "--project-dir", "dbt", "--profiles-dir", "dbt"],
        cwd=pb_root,
        capture_output=True,
        text=True,
    )
    assert dbt_result.returncode == 0, f"dbt run failed:\n{dbt_result.stdout}\n{dbt_result.stderr}"

    with db_conn.cursor() as cur:
        for mart in ("dbt_dev.fraud_metrics", "dbt_dev.merchant_performance", "dbt_dev.reconciliation_summary"):
            cur.execute(f"SELECT COUNT(*) FROM {mart}")  # noqa: S608
            count = cur.fetchone()[0]
            assert count > 0, f"dbt mart {mart} is empty after seeding"

    logger.info("test_dbt_marts_populated_after_seed_passed")
