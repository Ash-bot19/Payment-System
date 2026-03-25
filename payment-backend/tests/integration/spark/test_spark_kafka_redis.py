"""End-to-end integration tests: Kafka publish -> Spark enrichment -> Redis verify.

These tests validate the full foreachBatch pipeline by:
    1. Creating a static enriched DataFrame (bypasses live Kafka for CI stability)
    2. Calling write_features_to_redis() directly (same function used by streaming job)
    3. Asserting the Redis feat:{event_id} hash contains all 8 ML feature keys

Requires:
    - A live Redis instance at REDIS_URL (defaults to redis://localhost:6379/0)
    - Java installed (PySpark JVM)

Tests are skipped automatically when:
    - Redis is not available (connection refused)
    - Java is not installed (Spark JVM unavailable)

Per CLAUDE.md ML Risk Score Contract (LOCKED):
    8 feature keys: tx_velocity_1m, tx_velocity_5m, amount_zscore,
    merchant_risk_score, device_switch_flag, hour_of_day, weekend_flag,
    amount_cents_log
"""

import math
import os
import shutil
from datetime import datetime

import pytest
import redis
import structlog
from pyspark.sql.types import (
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from spark.redis_sink import write_features_to_redis

logger = structlog.get_logger(__name__)

# Guard: skip Spark JVM tests when Java is not installed
requires_java = pytest.mark.skipif(
    shutil.which("java") is None,
    reason="Java not found; Spark JVM tests require a JVM. Run inside spark Docker container.",
)

# Feature keys per CLAUDE.md ML Risk Score Contract (LOCKED)
EXPECTED_FEATURE_KEYS = [
    "tx_velocity_1m",
    "tx_velocity_5m",
    "amount_zscore",
    "merchant_risk_score",
    "device_switch_flag",
    "hour_of_day",
    "weekend_flag",
    "amount_cents_log",
]

# Schema for enriched DataFrame — output of PySpark column transforms in feature_engineering.py
ENRICHED_SCHEMA = StructType([
    StructField("event_id", StringType()),
    StructField("event_type", StringType()),
    StructField("merchant_id", StringType()),
    StructField("stripe_customer_id", StringType()),
    StructField("amount_cents", LongType()),
    StructField("received_at", TimestampType()),
    StructField("hour_of_day", IntegerType()),
    StructField("weekend_flag", IntegerType()),
    StructField("amount_cents_log", DoubleType()),
])


@pytest.fixture(scope="module")
def redis_client():
    """Connect to local Redis. Skip all tests in this module if Redis unavailable."""
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    r = redis.Redis.from_url(url, decode_responses=True)
    try:
        r.ping()
    except redis.ConnectionError:
        pytest.skip("Redis not available at " + url)
    yield r


@pytest.fixture(autouse=True)
def clean_redis_keys(redis_client):
    """Clean up test keys after each test for isolation."""
    yield
    for key in redis_client.scan_iter("feat:evt_test_*"):
        redis_client.delete(key)
    for key in redis_client.scan_iter("merchant:stats:merch_test_*"):
        redis_client.delete(key)
    for key in redis_client.scan_iter("device_seen:cus_test_*"):
        redis_client.delete(key)


@requires_java
@pytest.mark.integration
def test_e2e_kafka_to_redis_features(spark, redis_client):
    """E2E test: enriched DataFrame processed by foreachBatch -> all 8 feature keys appear in Redis.

    Simulates the Spark streaming enrichment pipeline without a live Kafka broker.
    The enriched DataFrame mirrors the output of PySpark column transforms in
    feature_engineering.py (hour_of_day, weekend_flag, amount_cents_log pre-computed).
    write_features_to_redis() computes the remaining features (merchant_risk_score,
    device_switch_flag, amount_zscore) and writes all 8 to Redis.
    """
    os.environ["REDIS_URL"] = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    data = [(
        "evt_test_001",
        "payment_intent.succeeded",
        "merch_test_1",
        "cus_test_1",
        5000,
        datetime(2024, 3, 16, 14, 30, 0),  # Saturday 14:00
        14,
        1,
        math.log1p(5000),
    )]
    batch_df = spark.createDataFrame(data, schema=ENRICHED_SCHEMA)

    write_features_to_redis(batch_df, epoch_id=0)

    assert redis_client.exists("feat:evt_test_001") == 1, (
        "Expected feat:evt_test_001 to exist in Redis after write_features_to_redis"
    )
    features = redis_client.hgetall("feat:evt_test_001")
    for key in EXPECTED_FEATURE_KEYS:
        assert key in features, (
            f"Expected feature key '{key}' in Redis hash feat:evt_test_001, "
            f"got keys: {list(features.keys())}"
        )
    logger.info(
        "e2e_kafka_to_redis_features_passed",
        event_id="evt_test_001",
        feature_keys=list(features.keys()),
    )


@requires_java
@pytest.mark.integration
def test_e2e_feature_values_correct(spark, redis_client):
    """E2E test: feature values written to Redis have correct types and values.

    Validates:
        - hour_of_day == 14 (14:30 UTC input)
        - weekend_flag == 1 (2024-03-16 is Saturday)
        - amount_cents_log == log1p(5000)
        - merchant_risk_score == 0.5 (default; no merchant:risk:{id} key seeded)
        - device_switch_flag == 0 (first time seeing this customer)
        - amount_zscore == 0.0 (first transaction for merchant; Welford count < 3 threshold)
    """
    os.environ["REDIS_URL"] = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    data = [(
        "evt_test_002",
        "payment_intent.succeeded",
        "merch_test_2",
        "cus_test_2",
        5000,
        datetime(2024, 3, 16, 14, 30, 0),  # Saturday 14:00
        14,
        1,
        math.log1p(5000),
    )]
    batch_df = spark.createDataFrame(data, schema=ENRICHED_SCHEMA)

    write_features_to_redis(batch_df, epoch_id=0)

    features = redis_client.hgetall("feat:evt_test_002")
    assert features, "feat:evt_test_002 not found in Redis"

    assert int(features["hour_of_day"]) == 14, (
        f"Expected hour_of_day=14, got {features['hour_of_day']}"
    )
    assert int(features["weekend_flag"]) == 1, (
        f"Expected weekend_flag=1 (Saturday), got {features['weekend_flag']}"
    )
    assert float(features["amount_cents_log"]) == pytest.approx(math.log1p(5000), abs=0.01), (
        f"Expected amount_cents_log=log1p(5000), got {features['amount_cents_log']}"
    )
    assert float(features["merchant_risk_score"]) == pytest.approx(0.5, abs=0.01), (
        f"Expected merchant_risk_score=0.5 (default), got {features['merchant_risk_score']}"
    )
    assert int(features["device_switch_flag"]) == 0, (
        f"Expected device_switch_flag=0 (new customer), got {features['device_switch_flag']}"
    )
    assert float(features["amount_zscore"]) == pytest.approx(0.0, abs=0.01), (
        f"Expected amount_zscore=0.0 (first tx, Welford count<3), got {features['amount_zscore']}"
    )
    logger.info(
        "e2e_feature_values_correct_passed",
        event_id="evt_test_002",
        features=features,
    )


@requires_java
@pytest.mark.integration
def test_e2e_feature_ttl(spark, redis_client):
    """E2E test: feat:{event_id} Redis hash has TTL ~3600s after write_features_to_redis.

    Validates that FEATURE_TTL=3600 is applied via pipe.expire() in redis_sink.py.
    Allows a small margin (3500s lower bound) for test execution time.
    """
    os.environ["REDIS_URL"] = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    data = [(
        "evt_test_003",
        "payment_intent.succeeded",
        "merch_test_3",
        "cus_test_3",
        2500,
        datetime(2024, 3, 18, 9, 15, 0),  # Monday 09:15
        9,
        0,
        math.log1p(2500),
    )]
    batch_df = spark.createDataFrame(data, schema=ENRICHED_SCHEMA)

    write_features_to_redis(batch_df, epoch_id=0)

    assert redis_client.exists("feat:evt_test_003") == 1, (
        "Expected feat:evt_test_003 to exist in Redis"
    )
    ttl = redis_client.ttl("feat:evt_test_003")
    assert ttl > 3500, (
        f"Expected TTL > 3500s (approximately 3600s), got ttl={ttl}. "
        "Verify pipe.expire(feat_key, 3600) is called in redis_sink.py."
    )
    logger.info(
        "e2e_feature_ttl_passed",
        event_id="evt_test_003",
        ttl=ttl,
    )
