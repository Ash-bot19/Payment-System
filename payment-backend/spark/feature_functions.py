"""Pure feature computation functions for Spark feature engineering pipeline.

These functions work with plain Python types (str timestamps, int amounts),
NOT PySpark Column objects. They are called inside foreachBatch where rows
are already collected to the driver.

Exports:
    compute_hour_of_day       -- integer 0-23 from ISO timestamp string
    compute_weekend_flag      -- 1 for Sat/Sun, 0 for weekdays
    compute_amount_cents_log  -- log1p(amount_cents)
    update_merchant_stats_and_zscore -- Welford online z-score with Redis state
"""

import math
from datetime import datetime

import redis
import structlog

logger = structlog.get_logger(__name__)


def compute_hour_of_day(received_at_str: str) -> int:
    """Return the hour (0-23) extracted from an ISO 8601 timestamp string.

    Args:
        received_at_str: ISO timestamp string e.g. "2024-03-15T14:30:00"

    Returns:
        Integer hour of day in range [0, 23].
    """
    dt = datetime.fromisoformat(received_at_str)
    return dt.hour


def compute_weekend_flag(received_at_str: str) -> int:
    """Return 1 if the timestamp falls on Saturday or Sunday, else 0.

    Args:
        received_at_str: ISO timestamp string e.g. "2024-03-16T12:00:00"

    Returns:
        1 for weekend (Saturday=5, Sunday=6 per datetime.weekday()), else 0.
    """
    dt = datetime.fromisoformat(received_at_str)
    # weekday(): Monday=0 ... Saturday=5, Sunday=6
    return 1 if dt.weekday() in (5, 6) else 0


def compute_amount_cents_log(amount_cents: int) -> float:
    """Return log1p(amount_cents) — log-transformed transaction amount.

    log1p is used so amount_cents=0 returns 0.0 without domain errors.

    Args:
        amount_cents: Transaction amount in cents (non-negative integer).

    Returns:
        float: math.log1p(amount_cents)
    """
    return math.log1p(float(amount_cents))


def update_merchant_stats_and_zscore(
    r_client: redis.Redis,
    merchant_id: str,
    amount_cents: int,
) -> float:
    """Update Welford online statistics in Redis and return the current z-score.

    Redis key: merchant:stats:{merchant_id} — Hash with fields:
        count (int), mean (float), M2 (float)

    Returns 0.0 when count < 2 (insufficient data to estimate variance).

    Args:
        r_client:    Connected redis.Redis client (decode_responses=True).
        merchant_id: Merchant identifier string.
        amount_cents: Transaction amount in cents for the current event.

    Returns:
        float: Z-score of amount_cents relative to merchant history,
               or 0.0 if fewer than 2 observations are available.
    """
    key = f"merchant:stats:{merchant_id}"
    raw = r_client.hgetall(key)

    # Parse existing Welford state (defaults to empty slate)
    count = int(raw.get("count", 0))
    mean = float(raw.get("mean", 0.0))
    m2 = float(raw.get("M2", 0.0))

    # Welford online update
    count += 1
    delta = amount_cents - mean
    mean += delta / count
    delta2 = amount_cents - mean
    m2 += delta * delta2

    # Persist updated stats
    r_client.hset(key, mapping={"count": count, "mean": mean, "M2": m2})

    logger.debug(
        "merchant_stats_updated",
        merchant_id=merchant_id,
        count=count,
        mean=mean,
        m2=m2,
    )

    # Need at least 3 observations (count >= 3 after update, i.e. 2 prior points)
    # to produce a meaningful z-score. With count < 3, variance estimate is
    # unreliable; return 0.0 as a safe default.
    # Equivalently: require the pre-update count >= 2 (i.e. 2+ prior observations).
    if count < 3:
        return 0.0

    variance = m2 / (count - 1)
    stddev = math.sqrt(variance) if variance > 0 else 1.0
    zscore = (amount_cents - mean) / stddev

    return zscore
