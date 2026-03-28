"""Nightly reconciliation Airflow DAG — Phase 07, Plan 02 / Phase 08, Plan 01.

Compares internal ledger entries against Stripe API data for a given UTC day.
Detects 3 discrepancy types per D-01 and publishes each to
payment.reconciliation.queue via ReconciliationProducer.
Persists discrepancy rows to reconciliation_discrepancies PostgreSQL table
for downstream dbt mart consumption (Phase 8).

Discrepancy types:
  - DUPLICATE_LEDGER (Type 4): Internal ledger has >2 rows per transaction.
  - MISSING_INTERNALLY (Type 1): Stripe has a succeeded PaymentIntent not in ledger.
  - AMOUNT_MISMATCH (Type 3): Both sides exist but amounts differ.

DAG task dependency:
  detect_duplicates ─┐  (run in parallel)
                     │
  fetch_stripe_window ─> compare_and_publish ─> persist_discrepancies

Note: compare_and_publish depends only on fetch_stripe_window for XCom data.
detect_duplicates runs in parallel and is independent.
persist_discrepancies receives the compare_and_publish discrepancy list via XCom.

Task functions are defined at module level for testability. The @dag body
wires them with Airflow TaskFlow XCom references.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import stripe
import structlog
from airflow.decorators import dag, task
from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    MetaData,
    Table,
    TIMESTAMP,
    Text,
    create_engine,
    insert,
    text,
)

from kafka.producers.reconciliation_producer import ReconciliationProducer
from models.reconciliation import ReconciliationMessage

logger = structlog.get_logger(__name__)

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
DATABASE_URL_SYNC = os.environ.get(
    "DATABASE_URL_SYNC",
    "postgresql://payment:payment@postgres:5432/payment_db",
)
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "")


@task()
def detect_duplicates(ds: str | None = None) -> int:
    """Detect transactions with >2 ledger rows in the given window.

    Uses GROUP BY transaction_id HAVING COUNT(*) > 2. Each duplicate
    transaction is published as a DUPLICATE_LEDGER message via
    ReconciliationProducer.

    Args:
        ds: Airflow execution date string, e.g. '2026-03-27'.

    Returns:
        Count of transactions with duplicate ledger entries (for XCom).
    """
    window_start = f"{ds} 00:00:00+00"
    window_end = f"{ds} 23:59:59.999999+00"

    engine = create_engine(DATABASE_URL_SYNC)
    duplicate_rows: list[Any] = []

    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT transaction_id, COUNT(*) as row_count
                FROM ledger_entries
                WHERE created_at >= :window_start AND created_at <= :window_end
                GROUP BY transaction_id
                HAVING COUNT(*) > 2
                """
            ),
            {"window_start": window_start, "window_end": window_end},
        )
        for row in result:
            duplicate_rows.append(row)

        if not duplicate_rows:
            logger.info(
                "reconciliation_duplicates_found",
                count=0,
                run_date=ds,
            )
            return 0

        messages: list[dict[str, Any]] = []
        for row in duplicate_rows:
            # Fetch merchant_id for this transaction_id
            merchant_result = conn.execute(
                text(
                    "SELECT DISTINCT merchant_id FROM ledger_entries "
                    "WHERE transaction_id = :tid LIMIT 1"
                ),
                {"tid": row.transaction_id},
            )
            merchant_row = next(iter(merchant_result), None)
            merchant_id = merchant_row.merchant_id if merchant_row else "unknown"

            msg = ReconciliationMessage(
                transaction_id=row.transaction_id,
                discrepancy_type="DUPLICATE_LEDGER",
                stripe_payment_intent_id=None,
                internal_amount_cents=None,
                stripe_amount_cents=None,
                diff_cents=None,
                currency="USD",
                merchant_id=merchant_id,
                stripe_created_at=None,
                run_date=datetime.strptime(ds, "%Y-%m-%d").date(),
            )
            messages.append(msg.model_dump(mode="json"))

    producer = ReconciliationProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
    producer.publish_batch(messages)

    count = len(messages)
    logger.info(
        "reconciliation_duplicates_found",
        count=count,
        run_date=ds,
    )
    return count


@task()
def fetch_stripe_window(ds: str | None = None) -> dict[str, Any]:
    """Fetch all succeeded PaymentIntents from Stripe for the given UTC day.

    Uses auto-pagination via stripe.PaymentIntent.list() iterator.
    Filters to status == 'succeeded' only.

    Args:
        ds: Airflow execution date string, e.g. '2026-03-27'.

    Returns:
        Dict keyed by PaymentIntent id with amount, currency, created, metadata.
    """
    stripe.api_key = STRIPE_API_KEY
    stripe.max_network_retries = 3

    window_start_dt = datetime.strptime(ds, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    window_end_dt = window_start_dt + timedelta(days=1) - timedelta(microseconds=1)
    window_start_ts = int(window_start_dt.timestamp())
    window_end_ts = int(window_end_dt.timestamp())

    intents: dict[str, Any] = {}
    for pi in stripe.PaymentIntent.list(
        created={"gte": window_start_ts, "lte": window_end_ts},
        limit=100,
    ):
        if pi.status != "succeeded":
            continue
        intents[pi.id] = {
            "amount": pi.amount_received,
            "currency": pi.currency,
            "created": pi.created,
            "metadata": pi.metadata,
        }

    logger.info(
        "stripe_window_fetched",
        count=len(intents),
        run_date=ds,
    )
    return intents


@task()
def compare_and_publish(
    stripe_intents: dict[str, Any], ds: str | None = None
) -> list[dict[str, Any]]:
    """Compare internal ledger against Stripe data and publish discrepancies.

    Detects:
      - MISSING_INTERNALLY (Type 1): Stripe PI not in internal ledger DEBIT rows.
      - AMOUNT_MISMATCH (Type 3): Both exist but DEBIT amount_cents != Stripe amount.

    Clean matches (amounts agree) are silently skipped.

    Args:
        stripe_intents: Dict keyed by PI id from fetch_stripe_window XCom.
        ds: Airflow execution date string, e.g. '2026-03-27'.

    Returns:
        List of discrepancy dicts (ReconciliationMessage.model_dump() format).
        Returns empty list if no discrepancies are found.
    """
    window_start = f"{ds} 00:00:00+00"
    window_end = f"{ds} 23:59:59.999999+00"

    engine = create_engine(DATABASE_URL_SYNC)

    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT transaction_id, amount_cents, merchant_id, currency
                FROM ledger_entries
                WHERE created_at >= :window_start AND created_at <= :window_end
                  AND entry_type = 'DEBIT'
                """
            ),
            {"window_start": window_start, "window_end": window_end},
        )
        internal: dict[str, dict[str, Any]] = {}
        for row in result:
            internal[row.transaction_id] = {
                "amount_cents": row.amount_cents,
                "merchant_id": row.merchant_id,
                "currency": row.currency,
            }

    messages: list[dict[str, Any]] = []
    type_1_count = 0
    type_3_count = 0
    clean_matches = 0
    run_date = datetime.strptime(ds, "%Y-%m-%d").date()

    # Type 1: MISSING_INTERNALLY — Stripe has PI, ledger does not
    for pi_id, pi_data in stripe_intents.items():
        if pi_id not in internal:
            msg = ReconciliationMessage(
                transaction_id=pi_id,
                discrepancy_type="MISSING_INTERNALLY",
                stripe_payment_intent_id=pi_id,
                internal_amount_cents=None,
                stripe_amount_cents=pi_data["amount"],
                diff_cents=None,
                currency=pi_data["currency"].upper(),
                merchant_id=pi_data["metadata"].get("merchant_id", "unknown"),
                stripe_created_at=datetime.fromtimestamp(
                    pi_data["created"], tz=timezone.utc
                ),
                run_date=run_date,
            )
            messages.append(msg.model_dump(mode="json"))
            type_1_count += 1
        else:
            ledger = internal[pi_id]
            if pi_data["amount"] != ledger["amount_cents"]:
                # Type 3: AMOUNT_MISMATCH — diff = stripe - internal
                msg = ReconciliationMessage(
                    transaction_id=pi_id,
                    discrepancy_type="AMOUNT_MISMATCH",
                    stripe_payment_intent_id=pi_id,
                    internal_amount_cents=ledger["amount_cents"],
                    stripe_amount_cents=None,
                    diff_cents=pi_data["amount"] - ledger["amount_cents"],
                    currency=ledger["currency"],
                    merchant_id=ledger["merchant_id"],
                    stripe_created_at=None,
                    run_date=run_date,
                )
                messages.append(msg.model_dump(mode="json"))
                type_3_count += 1
            else:
                clean_matches += 1

    if messages:
        producer = ReconciliationProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
        producer.publish_batch(messages)

    logger.info(
        "reconciliation_comparison_complete",
        type_1_count=type_1_count,
        type_3_count=type_3_count,
        clean_matches=clean_matches,
        run_date=ds,
    )
    return messages


@task()
def persist_discrepancies(
    discrepancies: list[dict[str, Any]], ds: str | None = None
) -> int:
    """Persist reconciliation discrepancies to reconciliation_discrepancies table.

    Receives discrepancy list from compare_and_publish via XCom.
    Writes rows using SQLAlchemy Core insert() (append-only pattern).
    No-ops cleanly if discrepancies list is empty.

    Table schema is defined explicitly (no autoload_with) to avoid a DB
    connection at DAG parse time — Airflow parses every DAG file on scheduler
    start, so any top-level DB call would require a live PostgreSQL connection
    just to load the DAG.

    Args:
        discrepancies: List of ReconciliationMessage .model_dump() dicts.
        ds: Airflow execution date string.

    Returns:
        Count of rows inserted.
    """
    if not discrepancies:
        logger.info("persist_discrepancies_skipped", reason="empty_list", run_date=ds)
        return 0

    engine = create_engine(DATABASE_URL_SYNC)

    metadata = MetaData()
    recon_table = Table(
        "reconciliation_discrepancies",
        metadata,
        Column("transaction_id", Text),
        Column("discrepancy_type", Text),
        Column("stripe_payment_intent_id", Text),
        Column("internal_amount_cents", BigInteger),
        Column("stripe_amount_cents", BigInteger),
        Column("diff_cents", BigInteger),
        Column("currency", Text),
        Column("merchant_id", Text),
        Column("stripe_created_at", TIMESTAMP(timezone=True)),
        Column("run_date", Date),
    )

    with engine.begin() as conn:
        conn.execute(insert(recon_table), discrepancies)

    logger.info(
        "persist_discrepancies_written",
        count=len(discrepancies),
        run_date=ds,
    )
    return len(discrepancies)


@dag(
    dag_id="nightly_reconciliation",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["reconciliation", "ledger"],
    default_args={"retries": 1, "retry_delay": timedelta(minutes=5)},
)
def nightly_reconciliation() -> None:
    """Nightly DAG: detect ledger duplicates and compare against Stripe API."""
    # detect_duplicates and fetch_stripe_window run in parallel.
    # compare_and_publish depends on fetch_stripe_window XCom output.
    # persist_discrepancies receives the discrepancy list from compare_and_publish via XCom.
    dupes = detect_duplicates()  # noqa: F841
    stripe_data = fetch_stripe_window()
    discrepancy_list = compare_and_publish(stripe_data)
    persist_discrepancies(discrepancy_list)


nightly_reconciliation()
