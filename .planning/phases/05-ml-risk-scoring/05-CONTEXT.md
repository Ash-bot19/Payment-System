# Phase 5: ML Risk Scoring Service — Context

**Gathered:** 2026-03-25
**Status:** Ready for planning

<domain>
## Phase Boundary

ML scoring service consumes `payment.transaction.validated`, reads the 8 ML features from Redis (`feat:{event_id}`), runs XGBoost inference, writes state transitions to PostgreSQL, and publishes to `payment.transaction.scored` and `payment.alert.triggered`. Phase 5 ends when scored events are flowing downstream with correct state transitions persisted in the DB.

Phase 5 introduces two new top-level artifacts:
- `ml/` module: `train.py`, `models/model.ubj`, and scoring utilities
- Two processes: `ScoringConsumer` (Kafka poll loop) and `services/ml_service.py` (FastAPI)

Phase 5 does NOT retrain the model, set up MLflow, or build any training infrastructure. It loads a pre-trained model file and serves it.

</domain>

<decisions>
## Implementation Decisions

### A — Model Loading & Lifecycle

- **D-A1:** Model file: `ml/models/model.ubj` — committed to the repo. Not a Docker volume, not a registry. Zero runtime dependency on training infrastructure.
- **D-A2:** `ml/train.py` generates synthetic payment data and produces the model file. Run once, output committed. Comment at top: `# Run once to regenerate ml/models/model.ubj. Not part of the serving path.` Training script is visible in the repo as proof of the full loop, but never invoked at runtime.
- **D-A3:** If `ml/models/model.ubj` is missing at startup: **crash-and-exit** with a clear structlog ERROR. `manual_review=true` fallback is for *runtime* failures (Redis timeout, inference error). A missing model at boot is misconfiguration — different failure mode, handled differently.
- **D-A4:** `ml/train.py` is in scope as a readable artifact, but out of Phase 5 DoD. The serving path never imports or calls it.

### B — Service Architecture

- **D-B1:** Two separate processes — same pattern as ValidationConsumer:
  1. `ScoringConsumer` — standalone Python Kafka poll loop with a threaded HTTP server for `/health` and a Prometheus metrics endpoint. No uvicorn, no FastAPI overhead.
  2. `services/ml_service.py` — FastAPI app (port 8001) with `POST /score`, `/health`, `/metrics`. Does NOT own the Kafka consumer loop.
  - The `uvicorn services.ml_service:app --reload --port 8001` line in CLAUDE.md refers to this FastAPI app only.
- **D-B2:** `POST /score` accepts a raw feature vector in the request body (all 8 features as explicit fields). Pydantic model schema matches exactly the 8 features Spark produces — the endpoint doubles as a living contract document.
- **D-B3:** Feature values in request body, not Redis lookup. The endpoint is a debug/test utility — it must work without an in-flight event in Redis.
- **D-B4:** Kafka consumer group: `ml-scoring-service`. Set via `KAFKA_CONSUMER_GROUP` env var with `ml-scoring-service` as default.

### C — Feature Assembly: Missing or Slow Redis Keys

- **D-C1:** When `feat:{event_id}` is missing from Redis — retry **3×50ms** (150ms total). This handles the common case of Spark being slightly behind the consumer. After 3 misses, fall through to conservative defaults + `manual_review=true`. No DLQ for missing features — DLQ is for structural failures (schema violations, malformed messages), not runtime data dependency gaps.
- **D-C2:** Missing key and Redis timeout produce the **same outcome** (conservative defaults + `manual_review=true`) but **different log messages and distinct Prometheus counters**:
  - `feature_miss_total` — key not found after retries
  - `feature_timeout_total` — Redis response exceeded 20ms threshold
  - Same code path; observability layer distinguishes them for alerting.
- **D-C3:** Conservative defaults when features unavailable:
  ```python
  FEATURE_DEFAULTS = {
      "tx_velocity_1m": 10,       # above normal thresholds
      "tx_velocity_5m": 50,       # above normal thresholds
      "amount_zscore": 2.0,       # unusual amount
      "merchant_risk_score": 1.0, # maximum risk
      "device_switch_flag": 1,    # switched device
      "hour_of_day": 12,          # neutral (no bias)
      "weekend_flag": 0,          # neutral (no bias)
      "amount_cents_log": 0.0,    # neutral
  }
  ```
  `manual_review=true` is set **unconditionally** when features are unavailable — defaults are belt-and-suspenders in case downstream reads feature values rather than the flag.
- **D-C4:** Log at **WARNING** per event, with `feature_miss_total` Prometheus counter increment. WARNING (not ERROR) because ERROR-per-event when Spark is degraded would flood logs and obscure real errors. Grafana alert on `feature_miss_total` rate over a rolling window is the Spark-is-down signal.

### D — State Machine Writes

- **D-D1:** **Direct PostgreSQL writes** — same pattern as ValidationConsumer (SQLAlchemy Core `insert()`). Consistency of pattern is a portfolio asset. No Kafka-mediated state events.
- **D-D2:** **No Alembic migrations** in the scoring service. It connects to PostgreSQL and inserts rows into the existing `payment_state_log` table. Schema ownership stays with the service that defined it (ValidationConsumer). If the scoring service ever needs a new table, that migration joins the existing migration history — not run from this service's entrypoint.
- **D-D3:** **Two DB rows per transaction:**
  1. Write `state=SCORING` when the event is picked up from Kafka (before Redis lookup / inference)
  2. Write `state=AUTHORIZED` (risk < 0.7) or `state=FLAGGED` (risk >= 0.7) after inference completes
  The intermediate SCORING state provides operational visibility. It is not overhead.
- **D-D4:** Write order: **DB write (AUTHORIZED/FLAGGED) first, then Kafka publish**. Same at-least-once decision as Phase 1. A duplicate Kafka publish is survivable (idempotency keys on downstream consumers). A missing DB state write is not survivable from an audit perspective.
- **D-D5:** Idempotency on DB insert: **application-layer guard, not a DB unique constraint**. `payment_state_log` is an append-only audit log — the same `event_id` legitimately has multiple rows (INITIATED, VALIDATED, SCORING, AUTHORIZED). Before inserting `state=SCORING`, the consumer checks `SELECT 1 FROM payment_state_log WHERE event_id=? AND state='SCORING'` — if it exists, skip and continue (replay-safe no-op). Same application-level pattern as ValidationConsumer; do NOT add a unique constraint to the table.

</decisions>

<specifics>
## Specific Ideas

- The `ScoringConsumer` startup sequence: validate env vars (`KAFKA_BOOTSTRAP_SERVERS`, `REDIS_URL`, `DATABASE_URL_SYNC`, `ML_MODEL_PATH`) → load XGBoost model → connect to Redis → connect to PostgreSQL → start Kafka poll loop. Fail fast on any missing config.
- `ML_MODEL_PATH` env var with default `ml/models/model.ubj` — makes the model path overridable without changing code.
- The Redis feature lookup should use `HGETALL feat:{event_id}` — one round trip for all 8 fields. Do not issue 8 separate `HGET` calls.
- The 20ms Redis timeout should be enforced via `socket_connect_timeout` and `socket_timeout` on the `redis.Redis` client, not application-level timers. Let redis-py raise `redis.exceptions.TimeoutError` — clean exception path.
- `payment.alert.triggered` is published when `is_high_risk=True` (risk >= 0.7). This is a subset of all scored events — not every scored event goes to this topic.
- `ml/train.py` should use `sklearn.datasets` or pure numpy to generate synthetic data with clear fraud signal separation. The model needs to produce plausible scores (not all 0.5) for integration tests to be meaningful.
- XGBoost model loaded once at startup and held in process memory — do not reload per inference. Load time is 10-100ms for a small model.
- For the `POST /score` FastAPI endpoint: input model `FeatureVector` (8 fields), output model `RiskScore` (`risk_score`, `is_high_risk`, `manual_review`). Both in `models/ml_scoring.py`.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Locked contracts (non-negotiable)
- `CLAUDE.md` §ML Risk Score Contract — 8 feature names, float[0,1] output, p99 < 100ms SLA, `manual_review=true` fallback on Redis timeout >20ms
- `CLAUDE.md` §Kafka Topics — `payment.transaction.validated` (input), `payment.transaction.scored` (output), `payment.alert.triggered` (output for high-risk)
- `CLAUDE.md` §Payment State Machine — `SCORING → AUTHORIZED` (risk < 0.7) or `SCORING → FLAGGED` (risk >= 0.7); `payment_state_log` append-only
- `CLAUDE.md` §DLQ Contract — failure_reason enum includes `ML_TIMEOUT`
- `CLAUDE.md` §Idempotency Strategy — Redis key format for idempotency; scoring service must honour the same pattern

### Existing code (read before implementing)
- `payment-backend/spark/redis_sink.py` — defines `feat:{event_id}` hash structure and the 8 field names; scoring service must read these exact field names
- `payment-backend/models/validation.py` — `ValidatedPaymentEvent` schema; scoring consumer deserialises this from `payment.transaction.validated`
- `payment-backend/kafka/consumers/validation_consumer.py` — reference implementation for the Kafka poll loop pattern, threaded health server, manual offset commit, DLQ publish before offset commit
- `payment-backend/services/state_machine.py` — `PaymentStateMachine` class; scoring service can import and reuse this for DB writes

### Phase dependencies
- Phase 4 output: `feat:{event_id}` Redis hash with TTL 3600s — scoring service relies on this existing at inference time
- Existing `payment_state_log` table (Phase 3 migration 001) — scoring service inserts rows without running new migrations

</canonical_refs>

<code_context>
## Existing Code Insights

### Integration Points
- Scoring consumer reads from: `payment.transaction.validated` (Kafka)
- Scoring consumer reads from: Redis `feat:{event_id}` hash (8 ML features, written by Phase 4 Spark job)
- Scoring consumer writes to: PostgreSQL `payment_state_log` (SCORING, then AUTHORIZED/FLAGGED)
- Scoring consumer writes to: `payment.transaction.scored` (all events)
- Scoring consumer writes to: `payment.alert.triggered` (high-risk events only, risk >= 0.7)
- FastAPI `POST /score` reads raw feature values from request body — no Redis or DB dependency

### New Files Expected
- `ml/train.py` — synthetic data generation + XGBoost training; comment: run once only
- `ml/models/model.ubj` — committed pre-trained XGBoost model binary
- `ml/__init__.py`
- `ml/scorer.py` — `XGBoostScorer` class: load model, `score(features: dict) -> RiskScore`, timeout-safe
- `models/ml_scoring.py` — Pydantic models: `FeatureVector` (8 fields), `RiskScore` (risk_score, is_high_risk, manual_review), `ScoredPaymentEvent` (extends ValidatedPaymentEvent)
- `kafka/consumers/scoring_consumer.py` — `ScoringConsumer` class: Kafka poll loop, feature assembly, state machine writes, Kafka publish
- `services/ml_service.py` — FastAPI app: `POST /score`, `/health`, `/metrics` (port 8001)
- `infra/docker-compose.yml` — add `ml-scoring-service` and `scoring-consumer` services
- `tests/unit/ml/test_scorer.py` — unit tests for XGBoostScorer (model load, score, fallback)
- `tests/integration/test_phase5_integration.py` — integration tests against live stack

### Reusable Patterns
- `ValidationConsumer` poll loop + threaded health server → mirror for `ScoringConsumer`
- `PaymentStateMachine` SQLAlchemy Core `insert()` pattern → reuse directly
- `DLQProducer` → reuse for ML_TIMEOUT failures
- Phase 3 integration test UUID-topic isolation → reuse for Phase 5 integration tests

</code_context>

<deferred>
## Deferred Ideas

- Model retraining pipeline / MLflow integration — deferred to a future MLOps phase (not in roadmap yet)
- A/B model versioning — deferred; single model version is sufficient for portfolio scale
- Feature drift monitoring — deferred to Phase 9 Dashboard + Monitoring
- Batch inference endpoint — deferred; single-event scoring is the only use case in scope
- GCP model artifact storage (GCS) — deferred to Phase 11 GCP Deploy

</deferred>

---

*Phase: 05-ml-risk-scoring*
*Context gathered: 2026-03-25*
