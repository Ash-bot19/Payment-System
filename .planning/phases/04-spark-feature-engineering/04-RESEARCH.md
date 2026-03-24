# Phase 4: Spark Feature Engineering - Research

**Researched:** 2026-03-24
**Domain:** Apache Spark Structured Streaming — Kafka ingestion, event-time windowing, Redis online feature store
**Confidence:** HIGH (core stack), MEDIUM (Docker/GCS local-dev patterns)

---

## Summary

Phase 4 introduces Apache Spark Structured Streaming as a new process that consumes `payment.transaction.validated` from Kafka, computes all 8 ML input features per transaction, and writes them to Redis as a per-transaction Hash so the Phase 5 ML scoring service can read them with sub-millisecond latency. This is the first Spark service in the project; no Spark service exists in the current docker-compose.

The two time-sensitive features (`tx_velocity_1m` and `tx_velocity_5m`) require event-time sliding windows with watermarks. The remaining six features (`amount_zscore`, `merchant_risk_score`, `device_switch_flag`, `hour_of_day`, `weekend_flag`, `amount_cents_log`) are per-event derivations computed inside a `foreachBatch` function before the Redis write. Redis writes happen via redis-py pipeline in `foreachBatch`, not via the spark-redis JVM connector, keeping the dependency surface small and Python-native.

The checkpoint location must be a `gs://` GCS path in production (per CLAUDE.md locked rule). For local dev/CI, a local path works — the code must be parameterised via env var `SPARK_CHECKPOINT_DIR` so the same job file runs in both environments.

**Primary recommendation:** Run Spark as a standalone `spark-submit` job (not a long-lived uvicorn service), containerised with `bitnami/spark:3.5`, submitted via `docker-compose run spark-worker spark-submit --packages ...`. Use `foreachBatch` + redis-py pipeline for all Redis writes; avoid the spark-redis JVM connector (last release 3.1.0, June 2024 — not officially tested against Spark 3.5).

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| FEAT-01 | tx_velocity_1m: count of transactions by merchant in last 1 minute | Spark sliding window: window("received_at", "1 minute", "1 minute") + groupBy merchant_id + count(); watermark 2 min |
| FEAT-02 | tx_velocity_5m: count of transactions by merchant in last 5 minutes | Spark sliding window: window("received_at", "5 minutes", "1 minute") + groupBy merchant_id + count(); watermark 10 min |
| FEAT-03 | amount_zscore: z-score of amount_cents vs merchant rolling mean/stddev | Stateful per-merchant mean+stddev tracked in foreachBatch with Redis HINCRBYFLOAT accumulator; or approx via per-batch mean; see Architecture Patterns |
| FEAT-04 | merchant_risk_score: static or slowly-changing merchant risk score | Read from Redis Hash `merchant:risk:{merchant_id}` — populated by seed script; Spark reads in foreachBatch via pipeline |
| FEAT-05 | device_switch_flag: 1 if stripe_customer_id used different merchant in last 5 min | State tracked in Redis Set `device_seen:{stripe_customer_id}` with TTL 300s; check + set in foreachBatch |
| FEAT-06 | hour_of_day: integer 0–23 from received_at | `F.hour(F.col("received_at"))` — pure Spark expression |
| FEAT-07 | weekend_flag: 1 if received_at falls on Sat/Sun | `F.dayofweek(F.col("received_at")).isin([1,7]).cast("int")` — pure Spark expression |
| FEAT-08 | amount_cents_log: log1p(amount_cents) | `F.log1p(F.col("amount_cents").cast("double"))` — pure Spark expression |
| FEAT-09 | Write all 8 features to Redis Hash `feat:{event_id}` with TTL 3600s | redis-py pipeline HSET + EXPIRE in foreachBatch; one pipeline per row batch |
| FEAT-10 | Spark consumer group `spark-feature-engine`, manual offset management via checkpoint | Spark Structured Streaming manages offsets via checkpointLocation — no manual commit needed; group.id not used by Structured Streaming |
| FEAT-11 | GCS checkpoint in prod, local path in dev | `SPARK_CHECKPOINT_DIR` env var; local default for Docker Compose dev |
| FEAT-12 | Dockerfile + docker-compose service for spark-feature-engine | bitnami/spark:3.5 image; spark-submit entry point with --packages |
</phase_requirements>

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pyspark | 3.5.8 | Structured Streaming engine | Locked in CLAUDE.md (3.5+); 3.5.8 is latest 3.5.x on PyPI as of 2026-03-24 |
| spark-sql-kafka-0-10_2.12 | 3.5.8 | Kafka source/sink connector JAR | Official Spark project; required for `.format("kafka")` reads |
| redis (redis-py) | 5.0.4 | Write features to Redis from foreachBatch | Already in requirements.txt; Python-native, no JVM overhead |
| structlog | 24.1.0 | All logging | Locked by CLAUDE.md — never print() |
| pydantic | 2.7.1 | Input schema parsing of Kafka JSON | Already in requirements.txt; consistent with Phases 2-3 |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| google-cloud-storage | latest | GCS connector dependency for gs:// checkpoint | GCP/Cloud Run deploy only; not needed for local dev |
| gcs-connector-hadoop3-latest.jar | hadoop3 build | Enables gs:// URI in Spark | Required in prod Docker image; skip in local dev |
| pytest | 8.2.1 | Unit tests | Already in requirements.txt |
| pyspark[connect] | 3.5.8 | Spark testing utilities (assertDataFrameEqual) | Test fixtures only |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| redis-py pipeline in foreachBatch | spark-redis JVM connector (com.redislabs:spark-redis:3.1.0) | spark-redis is Scala/JVM; Python support limited; last release June 2024 not verified against Spark 3.5; redis-py is already in project, Python-native, simpler |
| bitnami/spark:3.5 Docker image | apache/spark:3.5 official image | Both work; bitnami has better Docker Compose patterns and healthcheck support; widely documented for local dev |
| Spark standalone mode | Spark local[*] (no Docker cluster) | For a single streaming job, local[*] inside one container is simpler; only needs one Docker service, not master+worker |

**Installation (add to requirements.txt):**
```bash
pip install pyspark==3.5.8
```

**Kafka JAR (passed at spark-submit time — not pip):**
```
--packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.8
```

**Version verification (confirmed 2026-03-24):**
- pyspark 3.5.8 — verified via `pip index versions pyspark`
- spark-sql-kafka-0-10_2.12:3.5.8 — matches pyspark version (always use matching version)
- redis 5.0.4 — already in requirements.txt

---

## Architecture Patterns

### Recommended Project Structure

```
payment-backend/
  spark/
    feature_engineering.py     # main streaming job (spark-submit entry point)
    feature_functions.py       # pure PySpark column transforms (hour_of_day, weekend_flag, etc.)
    redis_sink.py              # foreachBatch function — writes feature Hash to Redis
    __init__.py
  infra/
    Dockerfile.spark           # bitnami/spark:3.5 + pyspark pip install
    docker-compose.yml         # add spark-feature-engine service
  tests/
    unit/
      test_feature_functions.py   # pure column transforms (no Kafka, no Redis)
      test_redis_sink.py          # foreachBatch function with mocked Redis client
    integration/
      test_spark_kafka_redis.py   # end-to-end: publish to Kafka, verify Redis keys
```

### Pattern 1: Spark Session Initialisation for a Streaming Job

**What:** Create a SparkSession with Kafka JAR on the classpath (provided via `--packages` at submit time, not in Python code).
**When to use:** Every streaming job entry point.

```python
# Source: https://spark.apache.org/docs/3.5.7/structured-streaming-kafka-integration.html
from pyspark.sql import SparkSession

spark = (
    SparkSession.builder
    .appName("spark-feature-engine")
    .config("spark.sql.shuffle.partitions", "3")  # matches Kafka topic partition count
    .getOrCreate()
)
```

No `--packages` in Python code. Pass at submit time:
```bash
spark-submit \
  --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.8 \
  spark/feature_engineering.py
```

### Pattern 2: Kafka ReadStream

**What:** Subscribe to `payment.transaction.validated`, parse JSON value column into a typed DataFrame.
**When to use:** Consuming any Kafka topic in Structured Streaming.

```python
# Source: https://spark.apache.org/docs/3.5.7/structured-streaming-kafka-integration.html
from pyspark.sql import functions as F
from pyspark.sql.types import StructType, StructField, StringType, LongType, TimestampType

validated_schema = StructType([
    StructField("event_id", StringType()),
    StructField("event_type", StringType()),
    StructField("amount_cents", LongType()),
    StructField("currency", StringType()),
    StructField("stripe_customer_id", StringType()),
    StructField("merchant_id", StringType()),
    StructField("received_at", TimestampType()),
])

raw_stream = (
    spark.readStream
    .format("kafka")
    .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP_SERVERS)
    .option("subscribe", "payment.transaction.validated")
    .option("startingOffsets", "latest")
    .option("failOnDataLoss", "true")
    .load()
)

events = raw_stream.select(
    F.from_json(F.col("value").cast("string"), validated_schema).alias("data")
).select("data.*")
```

**Critical:** Spark Structured Streaming stores offsets in the checkpointLocation — it does NOT use Kafka consumer group offset commits. The `kafka.group.id` option should NOT be set; Spark auto-generates a group ID. This is consistent with CLAUDE.md "manual offset commit ONLY" — Spark's checkpoint IS the manual offset record.

### Pattern 3: Event-Time Sliding Windows for Velocity Features

**What:** Count transactions per merchant_id in 1-minute and 5-minute sliding windows.
**When to use:** FEAT-01 and FEAT-02.

```python
# Source: https://spark.apache.org/docs/3.5.7/structured-streaming-programming-guide.html
from pyspark.sql import functions as F

# 1-minute velocity: tumbling window (slide = window size)
velocity_1m = (
    events
    .withWatermark("received_at", "2 minutes")  # tolerate 2 min late data
    .groupBy(
        F.window(F.col("received_at"), "1 minute", "1 minute"),
        F.col("merchant_id")
    )
    .count()
    .select(
        F.col("merchant_id"),
        F.col("window.start").alias("window_start"),
        F.col("count").alias("tx_velocity_1m")
    )
)

# 5-minute velocity: sliding window (1-minute slide for freshness)
velocity_5m = (
    events
    .withWatermark("received_at", "10 minutes")  # tolerate 10 min late data
    .groupBy(
        F.window(F.col("received_at"), "5 minutes", "1 minute"),
        F.col("merchant_id")
    )
    .count()
    .select(
        F.col("merchant_id"),
        F.col("window.start").alias("window_start"),
        F.col("count").alias("tx_velocity_5m")
    )
)
```

**outputMode for windowed aggregations:** Use `update` — emits updated counts for each window as events arrive. `append` only emits after the watermark passes (introduces latency). `complete` re-emits all windows every trigger (expensive). Use `update` for low-latency feature writes.

### Pattern 4: Per-Event Feature Derivation (no windowing)

**What:** Compute `hour_of_day`, `weekend_flag`, `amount_cents_log` as pure column expressions.
**When to use:** FEAT-06, FEAT-07, FEAT-08 — these don't require state.

```python
# Source: PySpark SQL Functions API — https://spark.apache.org/docs/3.5.7/api/python/reference/pyspark.sql/functions/datetime.html
enriched = events.select(
    F.col("event_id"),
    F.col("merchant_id"),
    F.col("stripe_customer_id"),
    F.col("amount_cents"),
    F.col("received_at"),
    F.hour(F.col("received_at")).alias("hour_of_day"),
    F.when(F.dayofweek(F.col("received_at")).isin(1, 7), 1).otherwise(0).alias("weekend_flag"),
    F.log1p(F.col("amount_cents").cast("double")).alias("amount_cents_log"),
)
```

### Pattern 5: foreachBatch Redis Write Sink

**What:** In `foreachBatch`, iterate rows, look up `merchant_risk_score` and `device_switch_flag` from Redis, compute `amount_zscore`, then write a complete feature Hash per transaction.
**When to use:** Any Spark → Redis write; the only supported pattern for Redis sinks in Python Structured Streaming.

```python
# Source: Spark docs foreachBatch + redis-py pipeline
import redis
import math

def write_features_to_redis(batch_df, epoch_id):
    """foreachBatch sink — writes feat:{event_id} Hash to Redis.

    Called once per micro-batch. batch_df is a static DataFrame.
    Uses redis-py pipeline for batched writes (atomic per-pipeline, not per-row).
    """
    import structlog
    log = structlog.get_logger()

    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    rows = batch_df.collect()  # safe — batch_df is small (micro-batch)

    if not rows:
        return

    pipe = r.pipeline()
    for row in rows:
        # Lookup static/slow-changing features
        merchant_risk = float(r.hget(f"merchant:risk:{row.merchant_id}", "score") or 0.5)

        # Device switch flag: has this customer been seen at a different merchant in last 5 min?
        device_key = f"device_seen:{row.stripe_customer_id}"
        last_merchant = r.get(device_key)
        device_switch = 1 if (last_merchant and last_merchant != row.merchant_id) else 0

        # amount_zscore — approximate: use per-batch mean/stddev (see Pitfalls for full approach)
        # Placeholder; real implementation uses rolling stats from Redis
        amount_zscore = 0.0  # computed in feature_functions.py

        feature_key = f"feat:{row.event_id}"
        pipe.hset(feature_key, mapping={
            "tx_velocity_1m": getattr(row, "tx_velocity_1m", 0),
            "tx_velocity_5m": getattr(row, "tx_velocity_5m", 0),
            "amount_zscore": amount_zscore,
            "merchant_risk_score": merchant_risk,
            "device_switch_flag": device_switch,
            "hour_of_day": row.hour_of_day,
            "weekend_flag": row.weekend_flag,
            "amount_cents_log": row.amount_cents_log,
        })
        pipe.expire(feature_key, 3600)  # 1-hour TTL — covers ML scoring SLA

        # Update device seen tracker
        pipe.set(device_key, row.merchant_id, ex=300)

    pipe.execute()
    log.info("features_written", epoch_id=epoch_id, row_count=len(rows))
```

**Important:** `batch_df.collect()` is acceptable here because micro-batches are small (payment events). Do NOT call `batch_df.collect()` on large batch DataFrames.

### Pattern 6: WriteStream Configuration

```python
# Source: https://spark.apache.org/docs/3.5.7/structured-streaming-programming-guide.html
import os

checkpoint_dir = os.getenv("SPARK_CHECKPOINT_DIR", "/tmp/spark-checkpoints/feature-engine")

query = (
    enriched
    .writeStream
    .outputMode("update")
    .foreachBatch(write_features_to_redis)
    .option("checkpointLocation", checkpoint_dir)
    .trigger(processingTime="5 seconds")
    .start()
)

query.awaitTermination()
```

### Pattern 7: amount_zscore — Rolling Merchant Statistics in Redis

**What:** Maintain a per-merchant rolling mean and M2 (Welford's algorithm) in Redis to compute an incremental z-score without storing all historical amounts.
**When to use:** FEAT-03. Fully in-memory; no DB required.

```python
# Welford's online algorithm for mean + variance
# Redis keys: merchant:stats:{merchant_id} → Hash {count, mean, M2}
def update_merchant_stats_and_zscore(r_client, merchant_id, amount_cents):
    """Returns amount_zscore for this transaction using Welford's algorithm."""
    key = f"merchant:stats:{merchant_id}"
    stats = r_client.hgetall(key)

    count = int(stats.get("count", 0))
    mean = float(stats.get("mean", 0.0))
    m2 = float(stats.get("M2", 0.0))

    # Welford update
    count += 1
    delta = amount_cents - mean
    mean += delta / count
    delta2 = amount_cents - mean
    m2 += delta * delta2

    # Persist updated stats (no TTL — merchant stats are long-lived)
    r_client.hset(key, mapping={"count": count, "mean": mean, "M2": m2})

    if count < 2:
        return 0.0  # not enough data
    variance = m2 / (count - 1)
    stddev = math.sqrt(variance) if variance > 0 else 1.0
    return (amount_cents - mean) / stddev
```

### Pattern 8: SparkSession in Tests (local mode)

**What:** Create a local SparkSession for unit tests without Kafka or Redis.
**When to use:** All unit tests in `tests/unit/test_feature_functions.py`.

```python
# Source: https://spark.apache.org/docs/latest/api/python/getting_started/testing_pyspark.html
import pytest
from pyspark.sql import SparkSession

@pytest.fixture(scope="session")
def spark():
    spark = (
        SparkSession.builder
        .master("local[2]")
        .appName("test-feature-engine")
        .config("spark.sql.shuffle.partitions", "2")
        .getOrCreate()
    )
    yield spark
    spark.stop()
```

### Anti-Patterns to Avoid

- **Setting `enable.auto.commit` on the Kafka source:** Structured Streaming ignores this option. Offsets are managed via checkpoint. Do not add it — it creates false confidence.
- **Using `kafka.group.id` option:** Spark generates its own group ID per query restart. Setting a fixed one causes multi-query conflicts.
- **checkpointLocation in /tmp:** Lost on container restart. Use `SPARK_CHECKPOINT_DIR` env var defaulting to a named Docker volume path for local dev.
- **`outputMode("complete")` for windowed aggregations:** Re-emits all historical windows every trigger. Expensive and unnecessary. Use `update`.
- **Calling `batch_df.show()` or `batch_df.collect()` without a size guard inside foreachBatch:** Harmless in dev, OOM risk in prod if batch is unexpectedly large. Add a count check or use `batch_df.limit(N).collect()` during development.
- **Not persisting batch_df when using it multiple times in foreachBatch:** If `batch_df` is referenced in multiple actions (e.g., count then collect), Spark re-evaluates the source each time. Call `batch_df.persist()` and `batch_df.unpersist()` if doing multiple passes.
- **spark-redis JVM connector for Python jobs:** Python support in spark-redis is via the DataFrame API only (no RDD ops), and Spark 3.5 compatibility is unverified. Use redis-py directly.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Kafka offset tracking | Custom Redis-based offset map | Spark checkpoint (built-in) | Checkpoint provides exactly-once offset semantics, handles partition rebalancing, survives restarts |
| Streaming window state | Custom in-memory dict per merchant | Spark `window()` + `withWatermark()` | Handles late data, state cleanup, distributed execution |
| Incremental z-score | Full history table in PostgreSQL | Welford's algorithm in Redis Hash | O(1) space, O(1) update, no DB needed |
| Redis batch writes | Individual HSET per row | redis-py `pipeline()` | Reduces round trips from N to 1 per micro-batch |
| Docker Spark JAR management | Baking JARs into image at build time | `--packages` at spark-submit time | Ivy resolves JARs automatically; simpler Dockerfile |

**Key insight:** Spark Structured Streaming's checkpoint mechanism IS the manual offset tracking solution. Trying to also implement confluent-kafka manual commits on top would be redundant and conflicting.

---

## Common Pitfalls

### Pitfall 1: Checkpoint Path on Windows Local Dev

**What goes wrong:** `checkpointLocation = "C:\\Users\\..."` or a Windows path causes Spark to throw `java.io.IOException: No FileSystem for scheme: C`.
**Why it happens:** Spark's Hadoop FileSystem abstraction doesn't recognise Windows drive letters.
**How to avoid:** Use a forward-slash path starting from `/` (Docker container path) or a named Docker volume mount. In docker-compose, mount a volume to `/spark-checkpoints` inside the container and set `SPARK_CHECKPOINT_DIR=/spark-checkpoints/feature-engine`.
**Warning signs:** Job starts, processes first batch, then fails on second trigger with FileSystem error.

### Pitfall 2: Kafka JAR Version Mismatch

**What goes wrong:** Spark throws `ClassNotFoundException: org.apache.kafka.clients.consumer.ConsumerRecord` or similar at runtime.
**Why it happens:** `spark-sql-kafka-0-10_2.12:3.5.X` version must exactly match the `pyspark` version installed. Using 3.5.1 JAR with pyspark 3.5.8 (or vice versa) causes binary incompatibility.
**How to avoid:** Always use the same version string for both `pyspark==X.Y.Z` (pip) and `--packages org.apache.spark:spark-sql-kafka-0-10_2.12:X.Y.Z` (submit).
**Warning signs:** Job fails immediately on `readStream.format("kafka").load()`.

### Pitfall 3: `startingOffsets` Ignored on Restart

**What goes wrong:** After restarting the job, it does not pick up from `startingOffsets="latest"` — it replays all messages from the last checkpoint.
**Why it happens:** This is correct and expected behaviour. `startingOffsets` only applies to new queries with no existing checkpoint. Once a checkpoint exists, it is always used.
**How to avoid:** Understand this as a feature, not a bug. Delete the checkpoint directory only when intentionally resetting the pipeline (e.g., schema change).
**Warning signs:** Developers are confused when the job appears to reprocess old data after a restart — it is reading from the checkpoint-recorded offset.

### Pitfall 4: outputMode Conflict with foreachBatch + Update

**What goes wrong:** `AnalysisException: Append output mode not supported when there are streaming aggregations without watermark`.
**Why it happens:** Windowed aggregations require either `update` or `complete` output mode, not `append` (unless watermark is set and you are emitting finalized windows only).
**How to avoid:** Use `outputMode("update")` for all windowed aggregation queries. `update` emits partial counts as they change — exactly what a feature store needs.
**Warning signs:** Exception at `.start()` time, not at runtime.

### Pitfall 5: Redis Pipeline Not Executed

**What goes wrong:** Features are never written to Redis despite no exception being raised.
**Why it happens:** `r.pipeline()` buffers commands but does NOT execute them until `pipe.execute()` is called. A missing `pipe.execute()` is a silent no-op.
**How to avoid:** Always pair `pipe = r.pipeline()` with `pipe.execute()` at the end of the foreachBatch function. Add a Redis key count assertion in integration tests.
**Warning signs:** No `feat:{event_id}` keys in Redis after the job runs.

### Pitfall 6: Spark and Python Version Mismatch in Docker

**What goes wrong:** `Exception: Python worker failed to connect back` or `PYTHON_VERSION mismatch`.
**Why it happens:** `bitnami/spark:3.5` uses Python 3.11 internally. If the pip-installed pyspark was built against a different Python, the worker process fails to start.
**How to avoid:** The bitnami/spark:3.5 image bundles Python 3.11 — consistent with the project's Python 3.11 requirement. Do not override the Python binary in the container.
**Warning signs:** Driver starts, but no tasks execute; executor logs show Python connection errors.

### Pitfall 7: amount_zscore Bootstrap Problem

**What goes wrong:** `merchant:stats:{merchant_id}` keys don't exist on first run, so the z-score is always 0.0 for the first N transactions.
**Why it happens:** Welford's algorithm needs at least 2 data points. A fresh Redis instance has no historical stats.
**How to avoid:** Accept that the first transaction per merchant has `amount_zscore=0.0` (handled by returning 0.0 when `count < 2`). Document this in comments. Do NOT fail or send to DLQ — the ML model should handle 0.0 gracefully via the fallback `manual_review=true` path if needed.
**Warning signs:** All z-scores are exactly 0.0 in the first few minutes of operation — this is expected.

---

## Code Examples

Verified patterns from official sources:

### Sliding Window with Watermark (Spark 3.5 docs)
```python
# Source: https://spark.apache.org/docs/3.5.7/structured-streaming-programming-guide.html
windowedCounts = (
    events
    .withWatermark("received_at", "10 minutes")
    .groupBy(
        F.window(F.col("received_at"), "10 minutes", "5 minutes"),
        F.col("merchant_id")
    )
    .count()
)
```

### foreachBatch Sink Pattern (Spark 3.5 docs)
```python
# Source: https://spark.apache.org/docs/3.5.7/structured-streaming-programming-guide.html
def process_batch(batch_df, epoch_id):
    batch_df.persist()
    # ... multiple actions on batch_df ...
    batch_df.unpersist()

query = (
    stream_df
    .writeStream
    .foreachBatch(process_batch)
    .option("checkpointLocation", checkpoint_dir)
    .outputMode("update")
    .trigger(processingTime="5 seconds")
    .start()
)
query.awaitTermination()
```

### Redis Pipeline Write Pattern (redis-py 5.x)
```python
# Source: redis-py documentation https://redis-py.readthedocs.io/en/stable/commands.html#pipelines
pipe = r.pipeline()
pipe.hset("feat:evt_123", mapping={"hour_of_day": 14, "weekend_flag": 0})
pipe.expire("feat:evt_123", 3600)
pipe.execute()  # MUST be called — pipeline is buffered
```

### Docker Compose Spark Service Pattern
```yaml
# Source: https://hub.docker.com/r/bitnami/spark (bitnami/spark:3.5)
spark-feature-engine:
  image: bitnami/spark:3.5
  container_name: spark-feature-engine
  environment:
    - SPARK_MODE=master          # single-node local mode
    - KAFKA_BOOTSTRAP_SERVERS=kafka:29092
    - REDIS_URL=redis://redis:6379/0
    - SPARK_CHECKPOINT_DIR=/spark-checkpoints/feature-engine
  volumes:
    - ../spark:/opt/spark-jobs
    - spark_checkpoints:/spark-checkpoints
  command: >
    spark-submit
    --master local[2]
    --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.8
    /opt/spark-jobs/feature_engineering.py
  depends_on:
    kafka:
      condition: service_healthy
    redis:
      condition: service_healthy
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| DStreams (old Spark Streaming) | Structured Streaming (DataFrames + SQL) | Spark 2.0 (2016), stable in 3.x | Use Structured Streaming only — DStreams are deprecated |
| `foreachRDD` / DStream sink | `foreachBatch` on DataFrame | Spark 2.4 (2018) | `foreachBatch` is the standard custom sink pattern |
| Manual offset tracking in Zookeeper | Checkpoint-based offset management | Spark 2.0+ | Checkpoint replaces external offset tracking for Structured Streaming |
| Spark Streaming + KafkaUtils | spark-sql-kafka-0-10 connector | Spark 2.0+ | Use `readStream.format("kafka")` — KafkaUtils is legacy |
| `trigger(once=True)` | `trigger(availableNow=True)` | Spark 3.3 | `availableNow` is the modern replacement for `once`; processes all available data then stops |

**Deprecated/outdated:**
- `trigger(once=True)`: Replaced by `trigger(availableNow=True)` in Spark 3.3+. `once` still works but is soft-deprecated.
- DStream API: Maintenance mode only. Not used in this project.
- `spark-redis` JVM connector for Python: Last release June 2024 (v3.1.0), Spark 3.5 compatibility unverified. Use redis-py directly.

---

## Open Questions

1. **Joining velocity window results back to the original event stream**
   - What we know: `velocity_1m` and `velocity_5m` are windowed aggregations (merchant_id + window → count). The main `enriched` stream has one row per event. To attach velocity counts to each event, we need to join them.
   - What's unclear: Stream-stream joins in Spark require watermarks on both sides. The velocity stream is already windowed; joining it back to the event stream may introduce latency equal to the watermark duration. Alternatively, velocity can be written separately to Redis (keyed by `merchant_id`) and looked up in the foreachBatch function.
   - Recommendation: Write velocity results to Redis (`merchant:velocity_1m:{merchant_id}`, `merchant:velocity_5m:{merchant_id}`) in a separate `foreachBatch` query. Then in the main event `foreachBatch`, look them up from Redis. This decouples the windowed and per-event streams and avoids stream-stream join complexity.

2. **GCS authentication for local dev**
   - What we know: `SPARK_CHECKPOINT_DIR` env var is the escape hatch. For local dev, a Docker named volume at `/spark-checkpoints` works. For GCP/Cloud Run, `gs://` + service account JSON is required.
   - What's unclear: The GCS Hadoop connector JAR (`gcs-connector-hadoop3`) must be in the Spark classpath for `gs://` to work. This requires either baking it into the Docker image or passing `--jars` at submit time.
   - Recommendation: Defer full GCS connector setup to Phase 11 (GCP Deploy). Phase 4 uses a named Docker volume for checkpoint. Document the GCS setup path in `infra/README` comments.

3. **Spark job health check in Docker Compose**
   - What we know: `spark-submit` runs as a foreground process; it exits when the job crashes. Docker `restart: unless-stopped` handles recovery. There is no HTTP health endpoint (unlike uvicorn services).
   - What's unclear: How to surface Spark streaming query health (lag, last batch time) to Prometheus without a separate metrics server.
   - Recommendation: For Phase 4, rely on Docker restart policy. Add a simple Python HTTP health server (same pattern as Phase 2's threaded `http.server`) that exposes `/health` and checks `query.status["isDataAvailable"]`. Defer Prometheus streaming metrics to Phase 9.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.2.1 (existing) |
| Config file | `payment-backend/pytest.ini` (existing — `asyncio_mode = auto`, `testpaths = tests`) |
| Quick run command | `pytest tests/unit/test_feature_functions.py tests/unit/test_redis_sink.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FEAT-06 | `hour_of_day` = correct integer from timestamp | unit | `pytest tests/unit/test_feature_functions.py::test_hour_of_day -x` | Wave 0 |
| FEAT-07 | `weekend_flag` = 1 on Saturday/Sunday, 0 on weekday | unit | `pytest tests/unit/test_feature_functions.py::test_weekend_flag -x` | Wave 0 |
| FEAT-08 | `amount_cents_log` = log1p(amount_cents) | unit | `pytest tests/unit/test_feature_functions.py::test_amount_cents_log -x` | Wave 0 |
| FEAT-03 | Welford's algorithm: z-score correct after N updates | unit | `pytest tests/unit/test_feature_functions.py::test_welford_zscore -x` | Wave 0 |
| FEAT-09 | Redis pipeline writes correct Hash keys + TTL | unit | `pytest tests/unit/test_redis_sink.py::test_feature_hash_written -x` | Wave 0 |
| FEAT-05 | `device_switch_flag=1` when customer seen at different merchant | unit | `pytest tests/unit/test_redis_sink.py::test_device_switch_flag -x` | Wave 0 |
| FEAT-01/02 | Velocity count correct for sliding window | integration | `pytest tests/integration/test_spark_kafka_redis.py -x` | Wave 0 |
| FEAT-09 | feat:{event_id} key exists in Redis after job processes a message | integration | `pytest tests/integration/test_spark_kafka_redis.py::test_feature_key_written -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/unit/ -x`
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/unit/test_feature_functions.py` — covers FEAT-06, FEAT-07, FEAT-08, FEAT-03
- [ ] `tests/unit/test_redis_sink.py` — covers FEAT-09, FEAT-05 (with mocked redis client)
- [ ] `tests/integration/test_spark_kafka_redis.py` — covers FEAT-01, FEAT-02, FEAT-09 end-to-end
- [ ] `tests/unit/conftest.py` update — add `spark` fixture (SparkSession local[2])
- [ ] Framework install: `pip install pyspark==3.5.8` — not yet in requirements.txt

---

## Sources

### Primary (HIGH confidence)

- [Spark 3.5.7 Structured Streaming + Kafka Integration Guide](https://spark.apache.org/docs/3.5.7/structured-streaming-kafka-integration.html) — Kafka readStream options, schema, offset management, JAR versions
- [Spark 3.5.x Structured Streaming Programming Guide](https://spark.apache.org/docs/3.5.7/structured-streaming-programming-guide.html) — withWatermark, window(), foreachBatch, outputMode, trigger, checkpointLocation
- [PySpark Testing Documentation](https://spark.apache.org/docs/latest/api/python/getting_started/testing_pyspark.html) — SparkSession fixture, assertDataFrameEqual
- PyPI `pip index versions pyspark` — verified pyspark 3.5.8 is latest 3.5.x (2026-03-24)
- `payment-backend/requirements.txt` — confirmed redis==5.0.4 already in project

### Secondary (MEDIUM confidence)

- [Spark-redis releases page](https://github.com/RedisLabs/spark-redis/releases) — confirmed v3.1.0 is latest (June 2024); Spark 3.5 compatibility unverified — basis for recommending redis-py instead
- [Bitnami Spark Docker image](https://hub.docker.com/r/bitnami/spark) — bitnami/spark:3.5 image confirmed; Docker Compose pattern referenced from community examples
- [GCS checkpoint on GKE demo](https://jaceklaskowski.github.io/spark-kubernetes-book/demo/using-cloud-storage-for-checkpoint-location-in-spark-structured-streaming-on-google-kubernetes-engine/) — gcs-connector-hadoop3 requirement, GOOGLE_APPLICATION_CREDENTIALS, spark.hadoop.fs.gs.project.id config

### Tertiary (LOW confidence — flag for validation)

- Multiple WebSearch results on velocity feature patterns, z-score approaches, and foreachBatch Redis patterns — cross-verified with official docs where possible; code examples above synthesized from official doc patterns

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — pyspark version verified via pip; Kafka JAR version matches pyspark; redis-py already in project
- Architecture: HIGH — core patterns from official Spark 3.5 docs
- Pitfalls: MEDIUM — most from official docs + known Spark behaviours; Windows path pitfall from CLAUDE.md Known Gotchas pattern
- spark-redis decision: MEDIUM — based on last release date + Python limitations documented in repo; recommend redis-py as safer choice

**Research date:** 2026-03-24
**Valid until:** 2026-06-24 (stable ecosystem — Spark 3.5.x patch releases don't break API; check pyspark version before planning if > 90 days)
