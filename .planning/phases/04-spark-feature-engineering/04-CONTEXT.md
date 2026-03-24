# Phase 4: Spark Feature Engineering — Context

**Gathered:** 2026-03-24
**Status:** Ready for planning

<domain>
## Phase Boundary

Spark Structured Streaming job consumes `payment.transaction.validated`, engineers all 8 ML input features per transaction, and writes them to Redis `feat:{event_id}` Hash with a 3600s TTL. Phase 4 ends when features are in Redis. ML scoring is Phase 5.

This is the first Spark service in the project. Phase 4 introduces `spark/` as a new top-level module inside `payment-backend/` and a new `spark-feature-engine` Docker Compose service.

04-01-PLAN.md already covers Task 1 (feature_functions.py + unit tests) and Task 2 (redis_sink.py + unit tests). Remaining plans cover the main streaming job, the velocity streaming query, Docker/docker-compose integration, and integration tests.

</domain>

<decisions>
## Implementation Decisions

### A — Velocity Feature Join Strategy

- **D-A1:** Use **Option 1 — write velocity to Redis, look up in foreachBatch**. Two separate streaming queries compute sliding-window counts and write them to Redis. The main foreachBatch reads these counts via Redis lookup before writing `feat:{event_id}`.
- **D-A2:** Redis key format for velocity (NEW — document in CLAUDE.md):
  - `velocity:1m:{merchant_id}:{epoch_minute}` — count of transactions in the 1-minute tumbling window
  - `velocity:5m:{merchant_id}:{epoch_minute}` — count in the 5-minute sliding window (1-minute slide)
  - `epoch_minute = int(received_at.timestamp() // 60)`
  - TTL: 300s (5 minutes) — long enough for foreachBatch to look up the current window
- **D-A3:** Velocity features are a supporting signal, not the centrepiece. Correctness is "close enough" — the foreachBatch looks up the most recent completed window count for the merchant. No stream-stream join. No debugging rabbit holes.
- **D-A4:** Velocity streaming queries are separate `writeStream` sinks (writing to Redis via their own foreachBatch). The main events stream is a third `writeStream`. All three share the same `spark` session. Each has its own checkpoint subdirectory: `{SPARK_CHECKPOINT_DIR}/velocity_1m`, `{SPARK_CHECKPOINT_DIR}/velocity_5m`, `{SPARK_CHECKPOINT_DIR}/features`.

### B — Streaming Trigger & Startup Offsets

- **D-B1:** Default trigger: `processingTime("10 seconds")`. The feature engine is a live service, not a script — continuous processing is the only sensible default.
- **D-B2:** Test trigger: `availableNow()` — activated via `SPARK_TRIGGER_ONCE=true` env var. Single entry point reads the env var and selects the trigger. No separate test entry point.
- **D-B3:** Cold start `startingOffsets`: `latest`. Prevents replaying accumulated test messages from previous sessions polluting Redis on every fresh Docker restart. Checkpoint handles resumption correctly on subsequent restarts — this setting only applies on first run (no checkpoint yet).
- **D-B4:** Startup validation — fail fast before `SparkSession` is created:
  ```python
  checkpoint_dir = os.getenv("SPARK_CHECKPOINT_DIR", "/tmp/spark-checkpoints")
  if not os.path.exists(checkpoint_dir):
      raise RuntimeError(f"Checkpoint dir not found: {checkpoint_dir}. Check SPARK_CHECKPOINT_DIR.")
  ```
  Code owns its errors. JVM stack traces are not error messages.

### C — Docker Service Topology

- **D-C1:** Persistent container, consistent with all other services. `restart: unless-stopped`. Part of `docker-compose up -d`. The feature engine is infrastructure — it runs when the stack runs.
- **D-C2:** Dockerfile location: `payment-backend/spark/Dockerfile`. Code and build artifact co-located. `infra/` holds shared infrastructure (docker-compose, Kafka configs), not service-specific Dockerfiles.
- **D-C3:** Base image: `bitnami/spark:3.5`. Entry point: `spark-submit --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.8 /app/spark/feature_engineering.py`.
- **D-C4:** Spark UI port 4040 exposed in docker-compose. It is the only window into streaming query health (micro-batch timing, watermark progress, processing rate) during local dev.
- **D-C5:** Local checkpoint dir: Docker named volume mounted at `/tmp/spark-checkpoints` inside the container. `SPARK_CHECKPOINT_DIR=/tmp/spark-checkpoints`. Satisfies the startup validator (path exists) and persists across container restarts.

### D — Redis Failure Handling in foreachBatch

- **D-D1:** **Option 1 — fail the batch, let Spark retry.** `redis.exceptions.ConnectionError` propagates out of `write_features_to_redis`. Spark retries the batch with the same `epoch_id`.
- **D-D2:** At-least-once semantics are safe because `HSET` on `feat:{event_id}` is idempotent. Retrying an identical batch overwrites with identical values — no corruption.
- **D-D3:** No per-row try/except. Partial batches create inconsistent feature store state and add complexity with no benefit — failed rows end up in `manual_review` anyway.
- **D-D4:** Redis connection failures logged at `ERROR` level. A failure to write features is not a warning — it means data was not written and batches are being delayed. ERROR is the correct severity.
- **D-D5:** `manual_review=true` fallback in Phase 5 (CLAUDE.md locked contract) is for genuinely ambiguous transactions, not infrastructure failures that were silently discarded. Retry + ERROR log is the correct contract boundary.

</decisions>

<specifics>
## Specific Ideas

- The three streaming queries (velocity_1m, velocity_5m, features) should all call `query.awaitTermination()` on the last one. Use `spark.streams.awaitAnyTermination()` to block until any query fails — prevents the job from exiting silently if one query dies.
- `SPARK_TRIGGER_ONCE=true` sets all three queries to `availableNow()` — not just the features query. Otherwise velocity writes may not complete before the features query reads them.
- The velocity Redis write can use a plain `SET` with TTL (not HSET) since the value is a single count integer. Simpler key than a Hash for a scalar value.
- The startup validator should also check `KAFKA_BOOTSTRAP_SERVERS` and `REDIS_URL` env vars are set (not empty). Three env vars validated once at startup beats three separate runtime errors.
- `spark.sql.shuffle.partitions=3` matches the Kafka topic partition count (locked at 3 per CLAUDE.md). Set this in SparkSession config.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Locked contracts (non-negotiable)
- `CLAUDE.md` §ML Risk Score Contract — 8 feature names, Redis key `feat:{event_id}`, TTL 3600s
- `CLAUDE.md` §Kafka Topics — `payment.transaction.validated` is the input topic (locked name)
- `CLAUDE.md` §Coding Rules — structlog only, no print(), Spark checkpoint dir = GCS bucket (never /tmp in prod, but local Docker volume is acceptable for local dev via named volume)
- `CLAUDE.md` §Redis keys — add `velocity:1m:` and `velocity:5m:` to the Redis key conventions section

### Phase requirements
- `.planning/phases/04-spark-feature-engineering/04-RESEARCH.md` — all patterns, library choices, and FEAT-01 through FEAT-12

### Existing code (read before implementing)
- `payment-backend/spark/feature_functions.py` — pure feature transforms (Plan 01 output); streaming job imports these
- `payment-backend/spark/redis_sink.py` — `write_features_to_redis` foreachBatch function (Plan 01 output); streaming job passes this to `.foreachBatch()`
- `payment-backend/models/validation.py` — `ValidatedPaymentEvent` schema; matches the Kafka message JSON structure
- `payment-backend/kafka/producers/validated_event_producer.py` — mirror this pattern for any producer in the Spark service if needed

</canonical_refs>

<code_context>
## Existing Code Insights

### Integration Points
- Spark reads from: `payment.transaction.validated` (Kafka, produced by Phase 3 ValidationConsumer)
- Spark writes to: Redis `feat:{event_id}` Hash, `velocity:1m:{merchant_id}:{epoch_minute}`, `velocity:5m:{merchant_id}:{epoch_minute}`
- Spark reads from Redis: `merchant:risk:{merchant_id}` Hash (seeded separately), `device_seen:{stripe_customer_id}` string, `merchant:stats:{merchant_id}` Hash (Welford state)

### New Files Expected (Plans 02+)
- `payment-backend/spark/feature_engineering.py` — main spark-submit entry point; SparkSession, startup validator, three writeStream queries, `awaitAnyTermination()`
- `payment-backend/spark/Dockerfile` — bitnami/spark:3.5 base, pip install pyspark redis structlog pydantic
- `payment-backend/infra/docker-compose.yml` — add `spark-feature-engine` service (port 4040, named checkpoint volume, `restart: unless-stopped`)
- `payment-backend/tests/integration/spark/test_spark_kafka_redis.py` — integration test using `SPARK_TRIGGER_ONCE=true`, publishes to Kafka, verifies Redis keys

### Reusable Patterns
- `ValidationConsumer` startup sequence (read-then-process loop) is a model for the Spark job's startup validation block
- `DLQProducer._delivery_report` pattern for any Kafka producer callbacks
- Phase 3 integration test UUID-topic isolation pattern applies to Spark integration tests too

</code_context>

<deferred>
## Deferred Ideas

- GCS checkpoint (`gs://` path) — deferred to Phase 11 GCP Deploy. Local dev uses Docker named volume.
- GCS connector JAR (`gcs-connector-hadoop3-latest.jar`) in Spark Docker image — deferred to Phase 11.
- Spark metrics exposed to Prometheus — deferred to Phase 9 Dashboard + Monitoring.
- Multiple Spark workers / cluster mode — deferred; `local[*]` inside one container is correct for portfolio scale.
- Exactly-once semantics via Kafka transactions — deferred; at-least-once with idempotent HSET is sufficient for a feature store.

</deferred>

---

*Phase: 04-spark-feature-engineering*
*Context gathered: 2026-03-24*
