"""Integration tests for spark/feature_engineering.py transform logic.

These tests validate:
    - validate_env() startup gating (raises on missing/invalid env vars)
    - get_trigger() returns correct trigger dict based on SPARK_TRIGGER_ONCE
    - PySpark column expressions (F.hour, F.dayofweek, F.log1p) produce correct values
    - velocity_1m window groupBy+count produces correct counts
    - write_velocity_to_redis foreachBatch writes correct Redis keys with TTL 300s

No live Kafka or Redis instance required for most tests.
test_write_velocity_to_redis uses mocked Redis.

Note: Tests requiring a live SparkSession (JVM) are skipped when Java is not
available. These tests pass when run inside the spark-feature-engine Docker
container (bitnami/spark:3.5) which includes a JVM.
"""

import math
import os
import shutil
import tempfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pyspark.sql import functions as F
from pyspark.sql.types import (
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# Guard: skip Spark JVM tests when Java is not installed
requires_java = pytest.mark.skipif(
    shutil.which("java") is None,
    reason="Java not found; Spark JVM tests require a JVM. Run inside spark Docker container.",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_validate_env():
    """Import validate_env from spark.feature_engineering (deferred to allow monkeypatching)."""
    from spark.feature_engineering import validate_env
    return validate_env


def _import_get_trigger():
    """Import get_trigger from spark.feature_engineering."""
    from spark.feature_engineering import get_trigger
    return get_trigger


def _import_write_velocity_to_redis():
    """Import write_velocity_to_redis from spark.feature_engineering."""
    from spark.feature_engineering import write_velocity_to_redis
    return write_velocity_to_redis


# ---------------------------------------------------------------------------
# validate_env tests
# ---------------------------------------------------------------------------

def test_validate_env_missing_checkpoint_dir(monkeypatch):
    """validate_env raises RuntimeError when SPARK_CHECKPOINT_DIR is empty."""
    monkeypatch.setenv("SPARK_CHECKPOINT_DIR", "")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    validate_env = _import_validate_env()
    with pytest.raises(RuntimeError, match="SPARK_CHECKPOINT_DIR"):
        validate_env()


def test_validate_env_nonexistent_dir(monkeypatch):
    """validate_env raises RuntimeError when SPARK_CHECKPOINT_DIR path does not exist."""
    monkeypatch.setenv("SPARK_CHECKPOINT_DIR", "/nonexistent/path/xyz")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    validate_env = _import_validate_env()
    with pytest.raises(RuntimeError, match="not found"):
        validate_env()


def test_validate_env_missing_kafka(monkeypatch):
    """validate_env raises RuntimeError when KAFKA_BOOTSTRAP_SERVERS is empty."""
    tmp_dir = tempfile.mkdtemp()
    monkeypatch.setenv("SPARK_CHECKPOINT_DIR", tmp_dir)
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    validate_env = _import_validate_env()
    with pytest.raises(RuntimeError, match="KAFKA_BOOTSTRAP_SERVERS"):
        validate_env()


def test_validate_env_missing_redis(monkeypatch):
    """validate_env raises RuntimeError when REDIS_URL is empty."""
    tmp_dir = tempfile.mkdtemp()
    monkeypatch.setenv("SPARK_CHECKPOINT_DIR", tmp_dir)
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv("REDIS_URL", "")

    validate_env = _import_validate_env()
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        validate_env()


# ---------------------------------------------------------------------------
# get_trigger tests
# ---------------------------------------------------------------------------

def test_get_trigger_default(monkeypatch):
    """get_trigger returns processingTime dict when SPARK_TRIGGER_ONCE is not set."""
    monkeypatch.delenv("SPARK_TRIGGER_ONCE", raising=False)

    get_trigger = _import_get_trigger()
    result = get_trigger()
    assert result == {"processingTime": "10 seconds"}


def test_get_trigger_once(monkeypatch):
    """get_trigger returns availableNow dict when SPARK_TRIGGER_ONCE=true."""
    monkeypatch.setenv("SPARK_TRIGGER_ONCE", "true")

    get_trigger = _import_get_trigger()
    result = get_trigger()
    assert result == {"availableNow": True}


# ---------------------------------------------------------------------------
# PySpark column expression tests (require local SparkSession)
# ---------------------------------------------------------------------------

# Schema matches VALIDATED_SCHEMA in feature_engineering.py
_SCHEMA = StructType([
    StructField("event_id", StringType()),
    StructField("event_type", StringType()),
    StructField("amount_cents", LongType()),
    StructField("currency", StringType()),
    StructField("stripe_customer_id", StringType()),
    StructField("merchant_id", StringType()),
    StructField("received_at", TimestampType()),
])


@requires_java
def test_enriched_columns(spark):
    """Enriched stream column expressions produce correct values for a known input.

    Input: 2024-03-16 14:30:00 (Saturday), amount_cents=5000
    Expected:
        hour_of_day = 14
        weekend_flag = 1  (Saturday: F.dayofweek returns 7 in Spark)
        amount_cents_log = log1p(5000)
    """
    data = [(
        "evt_001",
        "payment_intent.succeeded",
        5000,
        "usd",
        "cus_1",
        "merch_1",
        datetime(2024, 3, 16, 14, 30, 0),  # Saturday
    )]
    df = spark.createDataFrame(data, schema=_SCHEMA)

    result_df = df.select(
        F.col("event_id"),
        F.hour(F.col("received_at")).alias("hour_of_day"),
        F.when(F.dayofweek(F.col("received_at")).isin(1, 7), 1)
        .otherwise(0)
        .alias("weekend_flag"),
        F.log1p(F.col("amount_cents").cast("double")).alias("amount_cents_log"),
    )

    row = result_df.collect()[0]
    assert row.hour_of_day == 14
    assert row.weekend_flag == 1, f"Expected weekend_flag=1 for Saturday, got {row.weekend_flag}"
    assert row.amount_cents_log == pytest.approx(math.log1p(5000), abs=0.001)


@requires_java
def test_enriched_columns_weekday(spark):
    """weekend_flag is 0 for a weekday timestamp (Monday 2024-03-18)."""
    data = [(
        "evt_002",
        "payment_intent.succeeded",
        1000,
        "usd",
        "cus_2",
        "merch_2",
        datetime(2024, 3, 18, 9, 0, 0),  # Monday
    )]
    df = spark.createDataFrame(data, schema=_SCHEMA)

    result_df = df.select(
        F.when(F.dayofweek(F.col("received_at")).isin(1, 7), 1)
        .otherwise(0)
        .alias("weekend_flag"),
    )

    row = result_df.collect()[0]
    assert row.weekend_flag == 0, f"Expected weekend_flag=0 for Monday, got {row.weekend_flag}"


@requires_java
def test_velocity_window_groupby(spark):
    """Tumbling 1-minute window groupBy produces count=3 for 3 events in the same minute.

    NOTE: withWatermark only applies to streaming DataFrames. For static
    DataFrames (used in unit testing), we skip withWatermark and use groupBy
    directly on the window expression.
    """
    data = [
        ("evt_1", "payment_intent.succeeded", 1000, "usd", "cus_1", "merch_1",
         datetime(2024, 3, 16, 14, 30, 0)),
        ("evt_2", "payment_intent.succeeded", 2000, "usd", "cus_2", "merch_1",
         datetime(2024, 3, 16, 14, 30, 15)),
        ("evt_3", "payment_intent.succeeded", 3000, "usd", "cus_3", "merch_1",
         datetime(2024, 3, 16, 14, 30, 45)),
    ]
    df = spark.createDataFrame(data, schema=_SCHEMA)

    # Static DataFrame — skip withWatermark (streaming only)
    result_df = (
        df
        .groupBy(
            F.window(F.col("received_at"), "1 minute", "1 minute"),
            F.col("merchant_id"),
        )
        .count()
    )

    rows = result_df.collect()
    merchant_1_rows = [r for r in rows if r.merchant_id == "merch_1"]
    assert len(merchant_1_rows) == 1, f"Expected 1 window for merch_1, got {len(merchant_1_rows)}"
    assert merchant_1_rows[0]["count"] == 3, (
        f"Expected count=3 for 3 events in same 1-minute window, got {merchant_1_rows[0]['count']}"
    )


# ---------------------------------------------------------------------------
# write_velocity_to_redis tests
# ---------------------------------------------------------------------------

def test_write_velocity_to_redis(monkeypatch):
    """write_velocity_to_redis foreachBatch writes correct Redis key and TTL.

    Verifies:
        - pipe.set called with key matching "velocity:1m:merch_1:{epoch_minute}"
        - value = 5 (the count)
        - ex = 300 (TTL in seconds)
        - pipe.execute() called exactly once
    """
    write_velocity_to_redis = _import_write_velocity_to_redis()

    # Mock Redis pipeline
    mock_pipe = MagicMock()
    mock_redis = MagicMock()
    mock_redis.pipeline.return_value = mock_pipe

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    with patch("redis.Redis.from_url", return_value=mock_redis):
        # Build a mock batch_df with .collect() returning a row.
        # Use a class that supports both attribute access (.field) and
        # subscript access (row["field"]) — mimics pyspark.sql.Row behaviour.
        window_start = datetime(2024, 3, 16, 14, 30, 0)

        class MockRow:
            def __init__(self, **kwargs):
                self._data = kwargs
                for k, v in kwargs.items():
                    setattr(self, k, v)

            def __getitem__(self, key):
                return self._data[key]

        mock_row = MockRow(
            merchant_id="merch_1",
            window_start=window_start,
            tx_velocity_1m=5,
        )
        mock_batch_df = MagicMock()
        mock_batch_df.collect.return_value = [mock_row]

        # Call the writer
        writer_fn = write_velocity_to_redis("velocity:1m:", "tx_velocity_1m")
        writer_fn(mock_batch_df, epoch_id=1)

    # Compute expected key
    expected_epoch_minute = int(window_start.timestamp() // 60)
    expected_key = f"velocity:1m:merch_1:{expected_epoch_minute}"

    mock_pipe.set.assert_called_once_with(expected_key, 5, ex=300)
    mock_pipe.execute.assert_called_once()


def test_write_velocity_to_redis_empty_batch(monkeypatch):
    """write_velocity_to_redis returns early without Redis call for empty batch."""
    write_velocity_to_redis = _import_write_velocity_to_redis()

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    with patch("redis.Redis.from_url") as mock_from_url:
        mock_batch_df = MagicMock()
        mock_batch_df.collect.return_value = []

        writer_fn = write_velocity_to_redis("velocity:1m:", "tx_velocity_1m")
        writer_fn(mock_batch_df, epoch_id=0)

        # Redis.from_url should NOT be called for empty batch
        mock_from_url.assert_not_called()
