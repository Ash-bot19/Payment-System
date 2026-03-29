"""Feature Reconstruction Script.

Reconstructs ML features from historical PostgreSQL data
(payment_state_log + ledger_entries) to produce a training-ready
Parquet file with all 8 ML features defined in CLAUDE.md.

Purpose: Bootstrap the offline feature store for ML training data backfill
without Kafka/Spark/JVM dependencies.

Feature Fidelity Notes
----------------------
This script approximates the live Spark streaming pipeline features using
batch SQL queries and retrospective statistics. Approximation details:

- hour_of_day:
    EXACT — derived from payment_state_log.created_at at INITIATED state.
    Identical source as live pipeline.

- weekend_flag:
    EXACT — derived from the same INITIATED created_at timestamp.
    Identical source as live pipeline.

- amount_cents_log:
    EXACT — log1p(amount_cents) from ledger_entries DEBIT row.
    Identical computation as live pipeline.

- merchant_risk_score:
    APPROXIMATE — looked up from Redis key `merchant:risk:{merchant_id}`.
    Falls back to 0.5 if Redis is unavailable or key missing.
    Same fallback behaviour as live ScoringConsumer.

- tx_velocity_1m:
    APPROXIMATE — computed via SQL COUNT(*) OVER window function
    (PARTITION BY merchant_id ORDER BY created_at RANGE INTERVAL '1 minute'
    PRECEDING). This retrospective window approximates the live Spark 1-minute
    tumbling window but counts over the entire batch, not just what was visible
    at stream time. Slight over-count possible for high-frequency merchants.

- tx_velocity_5m:
    APPROXIMATE — same approach as tx_velocity_1m with a 5-minute window.

- amount_zscore:
    APPROXIMATE — batch retrospective z-score: (amount - merchant_mean) /
    merchant_std computed over the full batch. The live pipeline uses a Welford
    online algorithm with streaming Redis state. Batch z-scores require at least
    2 transactions per merchant (returns 0.0 for single-transaction merchants).

- device_switch_flag:
    ALWAYS 0 — device sequence is not stored in PostgreSQL (per D-09).
    The live pipeline derives this from Redis device history maintained by
    the streaming consumer. Reconstructing it from cold storage is not
    possible without a device event log. All reconstructed rows have
    device_switch_flag=0.

No Label Column
---------------
The output Parquet file does NOT include a ground-truth fraud label column.
Labels require a separate labeling strategy (e.g., dispute events from Stripe,
manual review outcomes). Labeling is out of scope for the feature reconstruction
pipeline (per D-18). The caller is responsible for joining labels before training.

Usage
-----
    python -m replay.feature_reconstruction [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]

Output
------
    data/feature_store/features_YYYYMMDD.parquet
    Columns: transaction_id, merchant_id, event_id, settled_at,
             tx_velocity_1m, tx_velocity_5m, amount_zscore,
             merchant_risk_score, device_switch_flag,
             hour_of_day, weekend_flag, amount_cents_log
"""

import argparse
import math
import os
import sys
from datetime import date, datetime

import pandas as pd
import psycopg2
import structlog

from spark.feature_functions import (
    compute_amount_cents_log,
    compute_hour_of_day,
    compute_weekend_flag,
)

logger = structlog.get_logger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://payment:payment@localhost:5432/payment_db"
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

FEATURE_COLUMNS = [
    "tx_velocity_1m",
    "tx_velocity_5m",
    "amount_zscore",
    "merchant_risk_score",
    "device_switch_flag",
    "hour_of_day",
    "weekend_flag",
    "amount_cents_log",
]
METADATA_COLUMNS = ["transaction_id", "merchant_id", "event_id", "settled_at"]

OUTPUT_DIR = os.path.join("data", "feature_store")


# ─── SQL Query Builder ────────────────────────────────────────────────────────


def build_query(
    start_date: date | None,
    end_date: date | None,
) -> tuple[str, list]:
    """Build a parameterized SQL query to fetch transactions with ML features.

    Selects INITIATED rows from payment_state_log (where from_state IS NULL AND
    to_state = 'INITIATED') joined to ledger_entries DEBIT rows. Velocity
    features are computed via SQL window functions (per D-07). Only SETTLED
    transactions (those with a DEBIT ledger entry) are returned.

    Args:
        start_date: Optional inclusive lower bound on psl.created_at (UTC date).
        end_date:   Optional inclusive upper bound on psl.created_at (UTC date).

    Returns:
        Tuple of (sql_string, params_list). Params are passed to psycopg2 as
        positional %s placeholders.
    """
    sql = """
        SELECT
            psl.transaction_id,
            psl.merchant_id,
            psl.event_id,
            psl.created_at,
            le.amount_cents,
            le.created_at AS settled_at,
            COUNT(*) OVER (
                PARTITION BY psl.merchant_id
                ORDER BY psl.created_at
                RANGE INTERVAL '1 minute' PRECEDING
            ) AS tx_velocity_1m,
            COUNT(*) OVER (
                PARTITION BY psl.merchant_id
                ORDER BY psl.created_at
                RANGE INTERVAL '5 minutes' PRECEDING
            ) AS tx_velocity_5m
        FROM payment_state_log psl
        INNER JOIN ledger_entries le
            ON psl.transaction_id = le.transaction_id
            AND le.entry_type = 'DEBIT'
        WHERE psl.from_state IS NULL
          AND psl.to_state = 'INITIATED'
    """

    params: list = []

    if start_date is not None:
        sql += "\n          AND psl.created_at >= %s"
        params.append(start_date)

    if end_date is not None:
        sql += "\n          AND psl.created_at <= %s"
        params.append(end_date)

    sql += "\n        ORDER BY psl.created_at ASC"

    return sql, params


# ─── Feature Computation ──────────────────────────────────────────────────────


def compute_batch_zscore(df: pd.DataFrame) -> pd.Series:
    """Compute batch retrospective amount z-scores per merchant.

    Per D-08: uses mean and std of amount_cents over the full batch for each
    merchant. This differs from the live Welford streaming algorithm. Returns
    0.0 when a merchant has only one transaction (std=0) or NaN std.

    Args:
        df: DataFrame with columns 'merchant_id' and 'amount_cents'.

    Returns:
        pandas Series of float z-scores, aligned with df.index.
    """
    if df.empty:
        return pd.Series(dtype=float)

    result = pd.Series(0.0, index=df.index, dtype=float)

    for merchant_id, group in df.groupby("merchant_id"):
        mean = group["amount_cents"].mean()
        std = group["amount_cents"].std()

        if std is None or (isinstance(std, float) and math.isnan(std)) or std == 0:
            z = pd.Series(0.0, index=group.index, dtype=float)
        else:
            z = (group["amount_cents"] - mean) / std

        result.loc[group.index] = z

    return result


def get_merchant_risk_scores(merchant_ids: list[str]) -> dict[str, float]:
    """Fetch merchant risk scores from Redis.

    Reads Redis key `merchant:risk:{merchant_id}` for each merchant. Falls back
    to 0.5 (same default as live ScoringConsumer) if Redis is unavailable or
    the key is not found.

    Args:
        merchant_ids: List of merchant identifier strings.

    Returns:
        Dict mapping merchant_id -> risk_score (float in [0, 1]).
    """
    scores: dict[str, float] = {}

    try:
        import redis

        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        for merchant_id in merchant_ids:
            val = r.get(f"merchant:risk:{merchant_id}")
            scores[merchant_id] = float(val) if val is not None else 0.5
    except Exception as exc:
        logger.warning(
            "redis_unavailable_using_fallback",
            error=str(exc),
            fallback=0.5,
        )
        return {mid: 0.5 for mid in merchant_ids}

    return scores


# ─── Core Reconstruction Logic ────────────────────────────────────────────────


def reconstruct_features(
    conn,
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    """Reconstruct all 8 ML features from historical PostgreSQL data.

    Executes the SQL query, computes deterministic features using
    spark.feature_functions, computes batch z-scores, and fetches
    merchant risk scores from Redis (with fallback).

    Args:
        conn:       Active psycopg2 connection.
        start_date: Optional inclusive start date filter.
        end_date:   Optional inclusive end date filter.

    Returns:
        DataFrame with METADATA_COLUMNS + FEATURE_COLUMNS. All feature
        columns are cast to float64. Returns empty DataFrame (with correct
        schema) if no SETTLED transactions exist in the date range.
    """
    sql, params = build_query(start_date, end_date)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        col_names = [desc[0] for desc in cur.description]

    if not rows:
        logger.warning(
            "no_settled_transactions_found",
            start_date=str(start_date),
            end_date=str(end_date),
        )
        empty_cols = METADATA_COLUMNS + FEATURE_COLUMNS
        return pd.DataFrame(columns=empty_cols)

    df = pd.DataFrame(rows, columns=col_names)

    logger.info(
        "transactions_loaded",
        row_count=len(df),
        start_date=str(start_date),
        end_date=str(end_date),
    )

    # Deterministic features from timestamps
    df["hour_of_day"] = df["created_at"].apply(
        lambda ts: compute_hour_of_day(ts.isoformat())
    )
    df["weekend_flag"] = df["created_at"].apply(
        lambda ts: compute_weekend_flag(ts.isoformat())
    )
    df["amount_cents_log"] = df["amount_cents"].apply(
        lambda amt: compute_amount_cents_log(int(amt))
    )

    # Batch retrospective z-score (D-08)
    df["amount_zscore"] = compute_batch_zscore(df)

    # Merchant risk scores from Redis (fallback 0.5)
    unique_merchants = df["merchant_id"].unique().tolist()
    risk_map = get_merchant_risk_scores(unique_merchants)
    df["merchant_risk_score"] = df["merchant_id"].map(risk_map).fillna(0.5)

    # device_switch_flag: always 0 — device sequence not stored in PostgreSQL (D-09)
    df["device_switch_flag"] = 0

    # Build output DataFrame with explicit column order
    output_df = df[METADATA_COLUMNS + FEATURE_COLUMNS].copy()

    # Cast all feature columns to float64
    for col in FEATURE_COLUMNS:
        output_df[col] = output_df[col].astype(float)

    return output_df


# ─── Parquet Output ───────────────────────────────────────────────────────────


def write_parquet(df: pd.DataFrame, output_dir: str) -> str:
    """Write the feature DataFrame to a dated Parquet file.

    Per D-11, D-12: output directory is created if missing; filename is
    features_YYYYMMDD.parquet based on today's date.

    Args:
        df:         DataFrame with METADATA_COLUMNS + FEATURE_COLUMNS.
        output_dir: Directory path to write the Parquet file.

    Returns:
        Absolute path of the written Parquet file.
    """
    os.makedirs(output_dir, exist_ok=True)

    filename = f"features_{date.today().strftime('%Y%m%d')}.parquet"
    output_path = os.path.join(output_dir, filename)

    df.to_parquet(output_path, engine="pyarrow", index=False)

    logger.info(
        "parquet_written",
        path=output_path,
        row_count=len(df),
        columns=list(df.columns),
    )

    return output_path


# ─── CLI ──────────────────────────────────────────────────────────────────────


def parse_args(argv=None) -> argparse.Namespace:
    """Parse CLI arguments for date range filtering.

    Args:
        argv: Argument list (defaults to sys.argv[1:] when None).

    Returns:
        argparse.Namespace with start_date and end_date as date objects or None.
    """
    parser = argparse.ArgumentParser(
        description="Reconstruct ML features from historical PostgreSQL data."
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Start date YYYY-MM-DD (inclusive)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="End date YYYY-MM-DD (inclusive)",
    )

    args = parser.parse_args(argv)

    if args.start_date is not None:
        args.start_date = date.fromisoformat(args.start_date)
    if args.end_date is not None:
        args.end_date = date.fromisoformat(args.end_date)

    return args


# ─── Entry Point ─────────────────────────────────────────────────────────────


def main(argv=None) -> None:
    """Main entry point for the feature reconstruction script.

    Connects to PostgreSQL, reconstructs features for the given date range,
    and writes to a Parquet file in data/feature_store/.
    """
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.dev.ConsoleRenderer(),
        ]
    )

    args = parse_args(argv)

    logger.info(
        "feature_reconstruction_starting",
        start_date=str(args.start_date),
        end_date=str(args.end_date),
    )

    try:
        conn = psycopg2.connect(DATABASE_URL)
    except Exception as exc:
        logger.error("db_connection_failed", error=str(exc))
        sys.exit(1)

    try:
        df = reconstruct_features(conn, args.start_date, args.end_date)

        if df.empty:
            logger.info(
                "no_data_to_write",
                start_date=str(args.start_date),
                end_date=str(args.end_date),
            )
            conn.close()
            sys.exit(0)

        output_path = write_parquet(df, OUTPUT_DIR)

        logger.info(
            "feature_reconstruction_complete",
            row_count=len(df),
            output_path=output_path,
            start_date=str(args.start_date),
            end_date=str(args.end_date),
        )
    except Exception as exc:
        logger.error("feature_reconstruction_failed", error=str(exc))
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
