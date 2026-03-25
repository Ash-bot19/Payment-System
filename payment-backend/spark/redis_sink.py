"""Redis sink for Spark feature engineering pipeline.

Implements the foreachBatch write function that computes per-event features
and writes them as Redis Hashes with appropriate TTLs.

Exports:
    write_features_to_redis -- foreachBatch handler; writes feat:{event_id} hash
    create_redis_client     -- factory for redis.Redis connection

Redis key patterns (from CLAUDE.md conventions):
    feat:{event_id}                  -- Hash, all 8 ML features, TTL 3600s
    merchant:risk:{merchant_id}      -- Hash, "score" field (seeded externally)
    merchant:stats:{merchant_id}     -- Hash, Welford state (count/mean/M2)
    device_seen:{stripe_customer_id} -- String, last merchant_id, TTL 300s
"""

import os

import redis
import structlog

from spark.feature_functions import update_merchant_stats_and_zscore

logger = structlog.get_logger(__name__)

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
FEATURE_TTL: int = 3600    # seconds — feat:{event_id} hash lifetime
DEVICE_SEEN_TTL: int = 300  # seconds — device tracking window (5 minutes)


def create_redis_client() -> redis.Redis:
    """Create and return a Redis client from REDIS_URL env var.

    Returns:
        redis.Redis: Connected client with decode_responses=True.
    """
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def write_features_to_redis(batch_df, epoch_id: int) -> None:
    """foreachBatch sink: compute remaining features and write all 8 to Redis.

    Called by Spark Structured Streaming for each micro-batch. This function
    runs on the driver. It receives a fully-materialized DataFrame batch.

    Features computed here (inside foreachBatch):
        - merchant_risk_score: read from merchant:risk:{merchant_id} hash
        - device_switch_flag: compare device_seen:{stripe_customer_id} to current merchant
        - amount_zscore: Welford online z-score via update_merchant_stats_and_zscore()

    Features expected as columns already on the batch_df (set by Spark expressions in
    the main streaming job, Plan 02):
        - tx_velocity_1m, tx_velocity_5m, hour_of_day, weekend_flag, amount_cents_log

    Args:
        batch_df:  Spark DataFrame (or mock with .collect()) for this micro-batch.
        epoch_id:  Monotonically increasing batch identifier from Spark.
    """
    rows = batch_df.collect()

    if not rows:
        logger.info("empty_batch", epoch_id=epoch_id)
        return

    r = create_redis_client()
    pipe = r.pipeline()

    for row in rows:
        # --- Merchant risk score (static/seeded in Redis) ---
        raw_risk = r.hget(f"merchant:risk:{row.merchant_id}", "score")
        merchant_risk = float(raw_risk) if raw_risk is not None else 0.5

        # --- Device switch flag ---
        device_key = f"device_seen:{row.stripe_customer_id}"
        last_merchant = r.get(device_key)
        device_switch = (
            1 if (last_merchant is not None and last_merchant != row.merchant_id) else 0
        )

        # --- Amount z-score (Welford, updates merchant:stats:{merchant_id}) ---
        amount_zscore = update_merchant_stats_and_zscore(r, row.merchant_id, row.amount_cents)

        # --- Write all 8 features to Redis pipeline ---
        feat_key = f"feat:{row.event_id}"
        pipe.hset(
            feat_key,
            mapping={
                "tx_velocity_1m": getattr(row, "tx_velocity_1m", 0),
                "tx_velocity_5m": getattr(row, "tx_velocity_5m", 0),
                "amount_zscore": amount_zscore,
                "merchant_risk_score": merchant_risk,
                "device_switch_flag": device_switch,
                "hour_of_day": row.hour_of_day,
                "weekend_flag": row.weekend_flag,
                "amount_cents_log": row.amount_cents_log,
            },
        )
        pipe.expire(feat_key, FEATURE_TTL)

        # Update device tracking with current merchant (always refresh TTL)
        pipe.set(device_key, row.merchant_id, ex=DEVICE_SEEN_TTL)

    # Execute all pipeline commands atomically — CRITICAL: without this call
    # all writes are silently buffered and never sent to Redis.
    pipe.execute()

    logger.info("features_written", epoch_id=epoch_id, row_count=len(rows))
