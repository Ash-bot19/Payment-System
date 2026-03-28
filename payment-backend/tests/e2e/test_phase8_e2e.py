"""E2E tests for Phase 8 — dbt run + dbt test against live docker-compose PostgreSQL.

Requires:
  - docker-compose PostgreSQL running with migrations 001-004 applied
  - dbt-core==1.8.9 and dbt-postgres==1.8.2 installed
  - payment_db populated with at least one SETTLED transaction (DEBIT + CREDIT rows)

Run with: INTEGRATION_TEST=1 pytest tests/e2e/test_phase8_e2e.py -v

These tests call the dbt CLI via subprocess.run() and check exit codes.
dbt test count is NOT mixed with pytest count — reported separately.

Failure-path strategy for assert_ledger_balanced:
  The balance trigger (DEFERRABLE INITIALLY DEFERRED) on ledger_entries fires at COMMIT
  and rejects any transaction where SUM(CREDIT) - SUM(DEBIT) != 0. This prevents
  inserting imbalanced rows via normal INSERT. The locked decision (CONTEXT.md Area 4)
  is to exercise the failure path via a dbt seed: seeds/imbalanced_transaction.csv
  contains a known-bad row with debit_amount_cents != credit_amount_cents. The seed
  loads into dbt_dev.imbalanced_transaction without touching ledger_entries. This
  documents the failure condition and keeps the test suite deterministic.
"""

import os
import subprocess
import sys
from pathlib import Path

import psycopg2
import pytest

SKIP_REASON = "requires live docker-compose stack + dbt installed (set INTEGRATION_TEST=1)"
pytestmark = pytest.mark.skipif(not os.environ.get("INTEGRATION_TEST"), reason=SKIP_REASON)

DBT_PROJECT_DIR = str(
    Path(__file__).parent.parent.parent / "dbt"
)
DATABASE_URL = os.environ.get(
    "DATABASE_URL_SYNC",
    "postgresql://payment:payment@localhost:5432/payment_db",
)


def _run_dbt(args: list[str], capture_output: bool = True) -> subprocess.CompletedProcess:
    """Run a dbt command from the dbt project directory."""
    cmd = ["dbt"] + args + ["--profiles-dir", DBT_PROJECT_DIR]
    return subprocess.run(
        cmd,
        cwd=DBT_PROJECT_DIR,
        capture_output=capture_output,
        text=True,
    )


def _insert_ledger_pair(
    transaction_id: str,
    amount_cents: int,
    merchant_id: str = "merch_e2e_001",
) -> None:
    """Insert a balanced DEBIT + CREDIT pair into ledger_entries.

    Inserts both rows in one transaction so the balance trigger is satisfied.
    """
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    for entry_type in ("DEBIT", "CREDIT"):
        cur.execute(
            """INSERT INTO ledger_entries
               (transaction_id, amount_cents, entry_type, merchant_id,
                currency, source_event_id)
               VALUES (%s, %s, %s, %s, 'USD', %s)""",
            (transaction_id, amount_cents, entry_type, merchant_id,
             f"evt_{transaction_id}_{entry_type.lower()}"),
        )
    conn.commit()
    cur.close()
    conn.close()


@pytest.fixture
def ensure_ledger_has_balanced_row():
    """Ensure at least one balanced DEBIT + CREDIT pair exists for dbt model tests."""
    tx_id = "e2e_balanced_tx_001"
    _insert_ledger_pair(tx_id, amount_cents=5000)
    yield tx_id
    # No teardown — ledger_entries is append-only. Row is harmless and keyed by fixed tx_id.
    # Subsequent runs that attempt to insert the same tx_id will succeed because ledger_entries
    # has no UNIQUE constraint on transaction_id (multiple DEBIT+CREDIT pairs per tx are allowed).


def test_dbt_compile_succeeds():
    """dbt compile exits 0 for all 10 models."""
    result = _run_dbt(["compile"])
    assert result.returncode == 0, (
        f"dbt compile failed.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_dbt_run_all_models(ensure_ledger_has_balanced_row):
    """dbt run exits 0, all 10 models materialize successfully."""
    result = _run_dbt(["run"])
    assert result.returncode == 0, (
        f"dbt run failed.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    # Verify all 10 models appear in output
    expected_models = [
        "stg_transactions", "stg_ledger_entries",
        "dim_merchants", "dim_users",
        "fact_transactions", "fact_ledger_balanced",
        "reconciliation_summary", "fraud_metrics",
        "hourly_payment_volume", "merchant_performance",
    ]
    combined = result.stdout + result.stderr
    for model in expected_models:
        assert model in combined, f"Model '{model}' not found in dbt run output"


def test_dbt_schema_tests_pass(ensure_ledger_has_balanced_row):
    """dbt schema contract tests (not_null, unique, accepted_values) pass."""
    result = _run_dbt(["test", "--exclude", "test_type:singular"])
    assert result.returncode == 0, (
        f"dbt schema tests failed.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_assert_ledger_balanced_passes_on_balanced_data(ensure_ledger_has_balanced_row):
    """assert_ledger_balanced singular test exits 0 when all transactions are balanced."""
    # First run dbt to build fact_ledger_balanced from current data
    _run_dbt(["run", "--select", "stg_ledger_entries", "fact_ledger_balanced"])

    result = _run_dbt(["test", "--select", "assert_ledger_balanced"])
    assert result.returncode == 0, (
        f"assert_ledger_balanced failed on balanced data.\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_dbt_seed_loads_imbalanced_transaction():
    """dbt seed loads imbalanced_transaction.csv into dbt_dev.imbalanced_transaction.

    The seed contains a row with debit_amount_cents=10000, credit_amount_cents=9999.
    This documents the known-bad row for failure-path testing without touching
    ledger_entries (which has a DEFERRABLE balance trigger blocking imbalanced commits).

    The assert_ledger_balanced singular test queries fact_ledger_balanced (built from
    ledger_entries), not from this seed. This seed is the canonical reference for
    what an imbalanced row looks like and verifies the seed file is well-formed.
    """
    result = _run_dbt(["seed", "--select", "imbalanced_transaction"])
    assert result.returncode == 0, (
        f"dbt seed failed.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    # Verify the row exists in dbt_dev.imbalanced_transaction
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        "SELECT transaction_id, debit_amount_cents, credit_amount_cents "
        "FROM dbt_dev.imbalanced_transaction WHERE transaction_id = 'imbalanced_test_001'"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    assert len(rows) == 1
    assert rows[0][1] == 10000  # debit_amount_cents
    assert rows[0][2] == 9999   # credit_amount_cents
    assert rows[0][1] != rows[0][2], "Seed row is intentionally imbalanced"
