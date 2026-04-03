"""Main Spark Structured Streaming entry point for feature engineering.

Reads from payment.transaction.validated Kafka topic, computes velocity
features via windowed aggregations, enriches events with PySpark column
expressions, and writes all 8 ML input features to Redis.

Three concurrent writeStream queries:
    velocity_1m  -- tumbling 1-minute window count per merchant_id → Redis velocity:1m:
    velocity_5m  -- sliding 5-minute/1-minute window count per merchant_id → Redis velocity:5m:
    features     -- enriched events with hour_of_day, weekend_flag, amount_cents_log → Redis feat:

Spark reads from: payment.transaction.validated (Kafka)
Spark writes to:  Redis feat:{event_id} Hash (all 8 ML features, TTL 3600s)
                  Redis velocity:1m:{merchant_id}:{epoch_minute} (count, TTL 300s)
                  Redis velocity:5m:{merchant_id}:{epoch_minute} (count, TTL 300s)

Entry point: spark-submit payment-backend/spark/feature_engineering.py
"""

import os
import sys

import redis
import structlog
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from spark.redis_sink import write_features_to_redis

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Schema — mirrors ValidatedPaymentEvent (models/validation.py)
# ---------------------------------------------------------------------------

VALIDATED_SCHEMA = StructType([
    StructField("event_id", StringType()),
    StructField("event_type", StringType()),
    StructField("amount_cents", LongType()),
    StructField("currency", StringType()),
    StructField("stripe_customer_id", StringType()),
    StructField("merchant_id", StringType()),
    StructField("received_at", TimestampType()),
])


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

def validate_env():
    """Validate required environment variables and filesystem paths.

    Checks SPARK_CHECKPOINT_DIR (must exist on disk), KAFKA_BOOTSTRAP_SERVERS,
    and REDIS_URL. Raises RuntimeError immediately on any missing/invalid value
    so the JVM never starts with bad config.

    Returns:
        tuple[str, str, str]: (checkpoint_dir, kafka_servers, redis_url)

    Raises:
        RuntimeError: If any required env var is missing/empty or checkpoint
                      directory does not exist on disk.
    """
    log = structlog.get_logger()

    checkpoint_dir = os.getenv("SPARK_CHECKPOINT_DIR", "")
    if not checkpoint_dir:
        raise RuntimeError("SPARK_CHECKPOINT_DIR env var is required but not set")
    if not checkpoint_dir.startswith("gs://") and not os.path.exists(checkpoint_dir):
        raise RuntimeError(
            f"Checkpoint dir not found: {checkpoint_dir}. Check SPARK_CHECKPOINT_DIR."
        )

    kafka_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "")
    if not kafka_servers:
        raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS env var is required but not set")

    redis_url = os.getenv("REDIS_URL", "")
    if not redis_url:
        raise RuntimeError("REDIS_URL env var is required but not set")

    log.info(
        "env_validated",
        checkpoint_dir=checkpoint_dir,
        kafka_servers=kafka_servers,
    )
    return checkpoint_dir, kafka_servers, redis_url


# ---------------------------------------------------------------------------
# Trigger selection
# ---------------------------------------------------------------------------

def get_trigger() -> dict:
    """Return the writeStream trigger dict based on SPARK_TRIGGER_ONCE env var.

    Default (SPARK_TRIGGER_ONCE unset or not "true"):
        {"processingTime": "10 seconds"} — continuous streaming

    Test mode (SPARK_TRIGGER_ONCE="true"):
        {"availableNow": True} — process all available data then stop

    Returns:
        dict: Trigger kwargs to unpack into .trigger(**get_trigger())
    """
    if os.getenv("SPARK_TRIGGER_ONCE", "").lower() == "true":
        return {"availableNow": True}
    return {"processingTime": "10 seconds"}


# ---------------------------------------------------------------------------
# Velocity Redis writer factory
# ---------------------------------------------------------------------------

def write_velocity_to_redis(key_prefix: str, count_col: str):
    """Return a foreachBatch function that writes velocity window counts to Redis.

    The returned function is passed to writeStream.foreachBatch(). It collects
    all rows from the aggregated velocity DataFrame and writes each count to
    Redis using a plain SET with a 300-second TTL.

    Redis key format:
        {key_prefix}{merchant_id}:{epoch_minute}

    Examples:
        velocity:1m:merch_abc:28791820  (epoch_minute = window_start.timestamp() // 60)
        velocity:5m:merch_abc:28791820

    Args:
        key_prefix:  Redis key prefix, e.g. "velocity:1m:" or "velocity:5m:"
        count_col:   Name of the count column in the aggregated DataFrame,
                     e.g. "tx_velocity_1m" or "tx_velocity_5m"

    Returns:
        Callable[[DataFrame, int], None]: foreachBatch handler
    """
    def _writer(batch_df, epoch_id: int) -> None:
        log = structlog.get_logger()
        rows = batch_df.collect()
        if not rows:
            return
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        r = redis.Redis.from_url(redis_url, decode_responses=True)
        pipe = r.pipeline()
        for row in rows:
            epoch_minute = int(row.window_start.timestamp() // 60)
            key = f"{key_prefix}{row.merchant_id}:{epoch_minute}"
            pipe.set(key, int(row[count_col]), ex=300)
        pipe.execute()
        log.info(
            "velocity_written",
            prefix=key_prefix,
            epoch_id=epoch_id,
            row_count=len(rows),
        )

    return _writer


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Start the Spark Structured Streaming feature engineering job.

    Sequence:
        1. validate_env() — fail fast before JVM starts
        2. Create SparkSession with shuffle.partitions=3 (matches Kafka partitions)
        3. Read Kafka stream from payment.transaction.validated
        4. Launch velocity_1m writeStream → Redis velocity:1m:
        5. Launch velocity_5m writeStream → Redis velocity:5m:
        6. Launch features writeStream → Redis feat:{event_id}
        7. Block on spark.streams.awaitAnyTermination()
    """
    checkpoint_dir, kafka_servers, redis_url = validate_env()

    spark = (
        SparkSession.builder
        .appName("spark-feature-engine")
        .config("spark.sql.shuffle.partitions", "3")
        .getOrCreate()
    )

    logger.info("spark_session_created", app_name="spark-feature-engine")

    # -----------------------------------------------------------------------
    # Kafka readStream — subscribe to payment.transaction.validated
    # -----------------------------------------------------------------------
    raw_stream = (
        spark.readStream
        .format("kafka")
        .option("kafka.bootstrap.servers", kafka_servers)
        .option("subscribe", "payment.transaction.validated")
        .option("startingOffsets", "latest")
        .option("failOnDataLoss", "true")
        .load()
    )

    # Parse JSON value using locked ValidatedPaymentEvent schema
    events = raw_stream.select(
        F.from_json(F.col("value").cast("string"), VALIDATED_SCHEMA).alias("data")
    ).select("data.*")

    # -----------------------------------------------------------------------
    # Query 1: velocity_1m — tumbling 1-minute window count per merchant_id
    # Per D-A2: key = velocity:1m:{merchant_id}:{epoch_minute}, TTL 300s
    # -----------------------------------------------------------------------
    velocity_1m = (
        events
        .withWatermark("received_at", "2 minutes")
        .groupBy(
            F.window(F.col("received_at"), "1 minute", "1 minute"),
            F.col("merchant_id"),
        )
        .count()
        .select(
            F.col("merchant_id"),
            F.col("window.start").alias("window_start"),
            F.col("count").alias("tx_velocity_1m"),
        )
    )

    velocity_1m_query = (
        velocity_1m.writeStream
        .outputMode("update")
        .foreachBatch(write_velocity_to_redis("velocity:1m:", "tx_velocity_1m"))
        .option("checkpointLocation", f"{checkpoint_dir}/velocity_1m")
        .trigger(**get_trigger())
        .start()
    )

    logger.info("velocity_1m_query_started")

    # -----------------------------------------------------------------------
    # Query 2: velocity_5m — sliding 5-minute/1-minute window per merchant_id
    # Per D-A2: key = velocity:5m:{merchant_id}:{epoch_minute}, TTL 300s
    # -----------------------------------------------------------------------
    velocity_5m = (
        events
        .withWatermark("received_at", "10 minutes")
        .groupBy(
            F.window(F.col("received_at"), "5 minutes", "1 minute"),
            F.col("merchant_id"),
        )
        .count()
        .select(
            F.col("merchant_id"),
            F.col("window.start").alias("window_start"),
            F.col("count").alias("tx_velocity_5m"),
        )
    )

    velocity_5m_query = (
        velocity_5m.writeStream
        .outputMode("update")
        .foreachBatch(write_velocity_to_redis("velocity:5m:", "tx_velocity_5m"))
        .option("checkpointLocation", f"{checkpoint_dir}/velocity_5m")
        .trigger(**get_trigger())
        .start()
    )

    logger.info("velocity_5m_query_started")

    # -----------------------------------------------------------------------
    # Query 3: enriched events — add column transforms via PySpark expressions
    #
    # NOTE: These column expressions (F.hour, F.dayofweek, F.log1p) run
    # distributed on Spark executors. The pure Python functions in
    # feature_functions.py run inside foreachBatch on the driver (different
    # code path — not used here).
    #
    # F.dayofweek convention: 1=Sunday, 7=Saturday
    # So isin(1, 7) correctly captures both Saturday and Sunday as weekend.
    # -----------------------------------------------------------------------
    enriched = events.select(
        F.col("event_id"),
        F.col("event_type"),
        F.col("merchant_id"),
        F.col("stripe_customer_id"),
        F.col("amount_cents"),
        F.col("received_at"),
        F.hour(F.col("received_at")).alias("hour_of_day"),
        F.when(F.dayofweek(F.col("received_at")).isin(1, 7), 1)
        .otherwise(0)
        .alias("weekend_flag"),
        F.log1p(F.col("amount_cents").cast("double")).alias("amount_cents_log"),
    )

    features_query = (
        enriched.writeStream
        .outputMode("append")
        .foreachBatch(write_features_to_redis)
        .option("checkpointLocation", f"{checkpoint_dir}/features")
        .trigger(**get_trigger())
        .start()
    )

    logger.info("features_query_started")

    # -----------------------------------------------------------------------
    # Block until any query fails or terminates
    # Per D-A4 / CONTEXT specifics: awaitAnyTermination prevents silent exit
    # if one of the three queries dies.
    # -----------------------------------------------------------------------
    logger.info("all_queries_started", query_count=3)
    spark.streams.awaitAnyTermination()


if __name__ == "__main__":
    main()
