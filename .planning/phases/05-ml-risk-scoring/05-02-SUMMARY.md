---
phase: 05-ml-risk-scoring
plan: 02
subsystem: ml-scoring
tags: [kafka-consumer, ml-inference, redis, state-machine, fastapi, xgboost, prometheus]
dependency_graph:
  requires: [05-01]
  provides: [ScoringConsumer, FastAPI-ml-service, ScoredEventProducer, AlertProducer]
  affects: [payment.transaction.scored, payment.alert.triggered, payment_state_log]
tech_stack:
  added: []
  patterns:
    - XGBoostScorer.score() called from Kafka consumer poll loop
    - Redis hgetall with 3x50ms retry loop + 20ms socket timeout fallback
    - DB write (state machine) before Kafka publish (D-D4 ordering guarantee)
    - FastAPI lifespan for model load (over deprecated on_event)
    - Prometheus counters for feature_miss, feature_timeout, scored, flagged
key_files:
  created:
    - payment-backend/kafka/consumers/scoring_consumer.py
    - payment-backend/kafka/producers/scored_event_producer.py
    - payment-backend/kafka/producers/alert_producer.py
    - payment-backend/services/ml_service.py
    - payment-backend/tests/unit/test_scoring_consumer.py
    - payment-backend/tests/unit/test_ml_service.py
    - payment-backend/tests/integration/test_phase5_integration.py
  modified:
    - payment-backend/models/state_machine.py (PaymentState enum extended)
decisions:
  - "FastAPI lifespan over @app.on_event for model load — on_event deprecated in FastAPI 0.115+ (CLAUDE.md convention)"
  - "model.ubj path resolved to ml/models/model.ubj (not ml/model.ubj — confirmed on actual filesystem)"
  - "Prometheus Counter names include timestamp suffix in test helpers to avoid re-registration collision across test runs"
metrics:
  duration_seconds: 387
  completed_date: "2026-03-25"
  tasks_completed: 2
  files_changed: 8
---

# Phase 05 Plan 02: ScoringConsumer + FastAPI ml_service Summary

ScoringConsumer Kafka poll loop with Redis 3x50ms retry + 20ms timeout fallback, XGBoost inference, SCORING/AUTHORIZED/FLAGGED state writes, dual Kafka publish, idempotency guard, plus FastAPI POST /score debug endpoint and 34 unit tests.

## Tasks Completed

| Task | Description | Commit | Files |
|------|-------------|--------|-------|
| 1 | Kafka producers + PaymentState enum + ScoringConsumer | 1f58192 | 4 files |
| 2 | FastAPI ml_service + unit tests + integration tests | 9460793 | 4 files |

## What Was Built

### PaymentState Enum Extension (`models/state_machine.py`)
Added `SCORING`, `AUTHORIZED`, `FLAGGED` to the existing enum. All Phase 3 states (INITIATED, VALIDATED, FAILED) preserved.

### ScoredEventProducer (`kafka/producers/scored_event_producer.py`)
Mirrors `ValidatedEventProducer` exactly. Publishes to `payment.transaction.scored` with 3-retry exponential backoff [1, 2, 4s], crash-on-exhaustion.

### AlertProducer (`kafka/producers/alert_producer.py`)
Mirrors `ValidatedEventProducer` exactly. Publishes to `payment.alert.triggered` — only called when `is_high_risk=True`.

### ScoringConsumer (`kafka/consumers/scoring_consumer.py`)
Full Kafka poll loop for `payment.transaction.validated`:
- Redis `hgetall(feat:{event_id})` with 3x50ms retry; 20ms socket timeout triggers immediate FEATURE_DEFAULTS fallback
- `XGBoostScorer.score()` inference on assembled feature dict
- Belt-and-suspenders: `manual_review=True` forced when `features_available=False`
- Idempotency guard: `SELECT 1 FROM payment_state_log WHERE event_id=:eid AND to_state='SCORING'`
- DB write order: SCORING state → AUTHORIZED/FLAGGED state → scored Kafka publish (D-D4)
- Alert publish only when `is_high_risk=True`
- Prometheus counters: `feature_miss_total`, `feature_timeout_total`, `events_scored_total`, `events_flagged_total`
- `/health` on port 8003, manual Kafka offset commit only, SIGTERM/SIGINT handling
- Env var validation at startup: crashes with `sys.exit(1)` on missing required vars

### FastAPI ml_service (`services/ml_service.py`)
- `GET /health` — liveness probe with `model_loaded` field
- `POST /score` — accepts `FeatureVector` (8 fields), returns `RiskScore`
- `GET /metrics` — Prometheus ASGI mount
- FastAPI `lifespan` context for model load (per CLAUDE.md convention, not deprecated `on_event`)

### Unit Tests
- `tests/unit/test_scoring_consumer.py` — 10 tests covering `_fetch_features` (hit, miss, timeout), `_is_duplicate_scoring` (true, false), `_process_message` (AUTHORIZED, FLAGGED, alert routing, idempotency skip, manual_review override)
- `tests/unit/test_ml_service.py` — 11 tests covering `/health` and `/score` endpoint (200, risk range, field types, threshold consistency, 422 on missing/wrong-type fields)

### Integration Tests
- `tests/integration/test_phase5_integration.py` — 6 tests for live Redis + PostgreSQL: full pipeline (features → inference → [0,1] range), SCORING+AUTHORIZED state writes, SCORING+FLAGGED state writes, feature miss fallback (FEATURE_DEFAULTS), idempotency guard (true + false cases)

## Test Results

```
34 unit tests: 10 scoring_consumer + 11 ml_service + 13 scorer (pre-existing) = 34 PASSED
6 integration tests: requires live Redis + PostgreSQL (pytest.skip guard when unavailable)
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Model path corrected from ml/model.ubj to ml/models/model.ubj**
- **Found during:** Task 2 test execution (first test run, SystemExit from XGBoostScorer)
- **Issue:** Plan examples and env var defaults referenced `ml/model.ubj` but actual filesystem places model at `ml/models/model.ubj`
- **Fix:** Updated `ML_MODEL_PATH` env var defaults in all test files and fixed `_make_consumer_no_init()` helper model path
- **Files modified:** test_scoring_consumer.py, test_ml_service.py, test_phase5_integration.py
- **Commit:** 9460793

**2. [Rule 2 - Critical functionality] FastAPI lifespan used instead of @app.on_event**
- **Found during:** Task 2 ml_service implementation
- **Issue:** Plan code snippet uses `@app.on_event("startup")` which is deprecated in FastAPI 0.115+ (per CLAUDE.md Key Decisions)
- **Fix:** Implemented `@asynccontextmanager async def lifespan(app: FastAPI)` pattern consistent with existing codebase conventions
- **Files modified:** services/ml_service.py
- **Commit:** 9460793

## Known Stubs

None — all features wired to live implementations.

## Self-Check: PASSED
