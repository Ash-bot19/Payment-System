"""Demo data seeder — Phase 09.

Inserts demo rows into payment_state_log, ledger_entries, and
reconciliation_discrepancies so dbt mart tables have visible data.

Run from payment-backend root:
    python scripts/seed_demo_data.py

Tables seeded:
    payment_state_log          — 15 transactions × state transitions (68 rows total)
    ledger_entries             — 8 SETTLED txns × DEBIT+CREDIT pairs (16 rows)
    reconciliation_discrepancies — 5 rows (2 MISSING_INTERNALLY, 2 AMOUNT_MISMATCH, 1 DUPLICATE_LEDGER)

All inserts are idempotent (skip if seed event_id already exists).
DEBIT+CREDIT pairs are committed in a single transaction to satisfy the
DEFERRABLE balance trigger on ledger_entries.
"""

import os
import random
import uuid
from datetime import date, datetime, timedelta, timezone

import psycopg2
import structlog

logger = structlog.get_logger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://payment:payment@localhost:5432/payment_db"
)

MERCHANTS = ["merchant_a", "merchant_b", "merchant_c"]

# Transaction distribution per context spec:
#   8 SETTLED  (3 merchant_a, 3 merchant_b, 2 merchant_c)
#   4 FLAGGED  (2 merchant_a, 1 merchant_b, 1 merchant_c)
#   3 AUTHORIZED (1 per merchant)

SETTLED_MERCHANTS = (
    ["merchant_a"] * 3 + ["merchant_b"] * 3 + ["merchant_c"] * 2
)
FLAGGED_MERCHANTS = (
    ["merchant_a"] * 2 + ["merchant_b"] * 1 + ["merchant_c"] * 1
)
AUTHORIZED_MERCHANTS = ["merchant_a", "merchant_b", "merchant_c"]


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _random_ts(days_ago_max: int = 6) -> datetime:
    """Random timestamp within the last N days."""
    offset_days = random.randint(0, days_ago_max)
    offset_hours = random.randint(0, 23)
    return datetime.now(timezone.utc) - timedelta(days=offset_days, hours=offset_hours)


def _state_rows(
    tx_id: str, merchant_id: str, states: list[str], base_ts: datetime
) -> list[dict]:
    """Build state_log rows for a transaction lifecycle."""
    rows = []
    ts = base_ts
    prev = None
    for state in states:
        rows.append(
            {
                "transaction_id": tx_id,
                "from_state": prev,
                "to_state": state,
                "event_id": f"seed_evt_{tx_id}_{state}",
                "merchant_id": merchant_id,
                "created_at": ts,
            }
        )
        prev = state
        ts = ts + timedelta(seconds=random.randint(1, 5))
    return rows


def _insert_state_rows(cur, rows: list[dict]) -> int:
    inserted = 0
    for row in rows:
        cur.execute(
            "SELECT COUNT(*) FROM payment_state_log WHERE event_id = %s",
            (row["event_id"],),
        )
        if cur.fetchone()[0] > 0:
            continue
        cur.execute(
            """
            INSERT INTO payment_state_log
                (transaction_id, from_state, to_state, event_id, merchant_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                row["transaction_id"],
                row["from_state"],
                row["to_state"],
                row["event_id"],
                row["merchant_id"],
                row["created_at"],
            ),
        )
        inserted += 1
    return inserted


def _insert_ledger_pair(
    conn, tx_id: str, merchant_id: str, amount_cents: int
) -> int:
    """Insert DEBIT + CREDIT in a single transaction (DEFERRABLE trigger requires it)."""
    debit_event = f"seed_evt_{tx_id}_debit"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM ledger_entries WHERE source_event_id = %s",
            (debit_event,),
        )
        if cur.fetchone()[0] > 0:
            return 0
        cur.execute(
            """
            INSERT INTO ledger_entries
                (transaction_id, amount_cents, entry_type, merchant_id, currency, source_event_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (tx_id, amount_cents, "DEBIT", merchant_id, "USD", debit_event),
        )
        cur.execute(
            """
            INSERT INTO ledger_entries
                (transaction_id, amount_cents, entry_type, merchant_id, currency, source_event_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                tx_id,
                amount_cents,
                "CREDIT",
                merchant_id,
                "USD",
                f"seed_evt_{tx_id}_credit",
            ),
        )
    conn.commit()
    return 2


def _insert_discrepancy(cur, row: dict) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM reconciliation_discrepancies WHERE transaction_id = %s AND discrepancy_type = %s",
        (row["transaction_id"], row["discrepancy_type"]),
    )
    if cur.fetchone()[0] > 0:
        return 0
    cur.execute(
        """
        INSERT INTO reconciliation_discrepancies
            (transaction_id, discrepancy_type, stripe_payment_intent_id,
             internal_amount_cents, stripe_amount_cents, diff_cents,
             currency, merchant_id, stripe_created_at, run_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            row["transaction_id"],
            row["discrepancy_type"],
            row.get("stripe_payment_intent_id"),
            row.get("internal_amount_cents"),
            row.get("stripe_amount_cents"),
            row.get("diff_cents"),
            row.get("currency", "USD"),
            row["merchant_id"],
            row.get("stripe_created_at"),
            row["run_date"],
        ),
    )
    return 1


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False

    try:
        state_log_count = 0
        ledger_count = 0
        discrepancy_count = 0

        # ── Generate transaction UUIDs ────────────────────────────────────────
        settled_txns = [
            (str(uuid.uuid4()), SETTLED_MERCHANTS[i], _random_ts())
            for i in range(8)
        ]
        flagged_txns = [
            (str(uuid.uuid4()), FLAGGED_MERCHANTS[i], _random_ts())
            for i in range(4)
        ]
        authorized_txns = [
            (str(uuid.uuid4()), AUTHORIZED_MERCHANTS[i], _random_ts())
            for i in range(3)
        ]

        # ── payment_state_log ─────────────────────────────────────────────────
        with conn.cursor() as cur:
            for tx_id, merchant_id, base_ts in settled_txns:
                rows = _state_rows(
                    tx_id,
                    merchant_id,
                    ["INITIATED", "VALIDATED", "SCORING", "AUTHORIZED", "SETTLED"],
                    base_ts,
                )
                state_log_count += _insert_state_rows(cur, rows)

            for tx_id, merchant_id, base_ts in flagged_txns:
                rows = _state_rows(
                    tx_id,
                    merchant_id,
                    ["INITIATED", "VALIDATED", "SCORING", "FLAGGED"],
                    base_ts,
                )
                state_log_count += _insert_state_rows(cur, rows)

            for tx_id, merchant_id, base_ts in authorized_txns:
                rows = _state_rows(
                    tx_id,
                    merchant_id,
                    ["INITIATED", "VALIDATED", "SCORING", "AUTHORIZED"],
                    base_ts,
                )
                state_log_count += _insert_state_rows(cur, rows)

        conn.commit()

        # ── ledger_entries (SETTLED transactions only) ────────────────────────
        for tx_id, merchant_id, _ in settled_txns:
            amount_cents = random.randint(1000, 50000)
            ledger_count += _insert_ledger_pair(conn, tx_id, merchant_id, amount_cents)

        # ── reconciliation_discrepancies ──────────────────────────────────────
        yesterday = date.today() - timedelta(days=1)

        discrepancy_rows = [
            # 2 MISSING_INTERNALLY
            {
                "transaction_id": str(uuid.uuid4()),
                "discrepancy_type": "MISSING_INTERNALLY",
                "stripe_payment_intent_id": "pi_seed_missing_1",
                "internal_amount_cents": None,
                "stripe_amount_cents": random.randint(1000, 5000),
                "diff_cents": None,
                "merchant_id": "merchant_a",
                "stripe_created_at": _random_ts(2),
                "run_date": yesterday,
            },
            {
                "transaction_id": str(uuid.uuid4()),
                "discrepancy_type": "MISSING_INTERNALLY",
                "stripe_payment_intent_id": "pi_seed_missing_2",
                "internal_amount_cents": None,
                "stripe_amount_cents": random.randint(1000, 5000),
                "diff_cents": None,
                "merchant_id": "merchant_b",
                "stripe_created_at": _random_ts(2),
                "run_date": yesterday,
            },
            # 2 AMOUNT_MISMATCH (using first two settled txn IDs)
            {
                "transaction_id": settled_txns[0][0],
                "discrepancy_type": "AMOUNT_MISMATCH",
                "stripe_payment_intent_id": "pi_seed_mismatch_1",
                "internal_amount_cents": 2500,
                "stripe_amount_cents": 2700,
                "diff_cents": 200,
                "merchant_id": settled_txns[0][1],
                "stripe_created_at": _random_ts(1),
                "run_date": yesterday,
            },
            {
                "transaction_id": settled_txns[1][0],
                "discrepancy_type": "AMOUNT_MISMATCH",
                "stripe_payment_intent_id": "pi_seed_mismatch_2",
                "internal_amount_cents": 4000,
                "stripe_amount_cents": 4150,
                "diff_cents": 150,
                "merchant_id": settled_txns[1][1],
                "stripe_created_at": _random_ts(1),
                "run_date": yesterday,
            },
            # 1 DUPLICATE_LEDGER
            {
                "transaction_id": settled_txns[2][0],
                "discrepancy_type": "DUPLICATE_LEDGER",
                "stripe_payment_intent_id": "pi_seed_dup_1",
                "internal_amount_cents": 3000,
                "stripe_amount_cents": 3000,
                "diff_cents": 0,
                "merchant_id": settled_txns[2][1],
                "stripe_created_at": _random_ts(1),
                "run_date": yesterday,
            },
        ]

        with conn.cursor() as cur:
            for row in discrepancy_rows:
                discrepancy_count += _insert_discrepancy(cur, row)
        conn.commit()

        logger.info(
            "seed_complete",
            state_log_rows=state_log_count,
            ledger_rows=ledger_count,
            discrepancy_rows=discrepancy_count,
        )

    except Exception as exc:
        conn.rollback()
        logger.error("seed_failed", error=str(exc))
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
