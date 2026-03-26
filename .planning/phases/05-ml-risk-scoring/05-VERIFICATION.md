---
phase: 05-ml-risk-scoring
verified: 2026-03-26T00:00:00Z
status: passed
score: 13/13 must-haves verified
re_verification: null
gaps: []
human_verification: []
---

# Phase 05: ML Risk Scoring Verification Report

**Phase Goal:** Deploy ML risk scoring pipeline — XGBoost inference on validated payments, state machine writes, downstream Kafka publish
**Verified:** 2026-03-26
**Status:** PASSED
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #  | Truth                                                                              | Status     | Evidence                                                                          |
|----|------------------------------------------------------------------------------------|------------|-----------------------------------------------------------------------------------|
| 1  | XGBoostScorer loads model.ubj and returns risk_score float[0,1]                   | VERIFIED   | ml/scorer.py: load_model + clamped predict; model.ubj exists (38KB)              |
| 2  | Missing model file at startup causes crash with structlog ERROR                    | VERIFIED   | scorer.py:72-74 `logger.error("model_file_missing") sys.exit(1)`                 |
| 3  | Conservative feature defaults produce manual_review=true                           | VERIFIED   | FEATURE_DEFAULTS has merchant_risk_score=1.0, tx_velocity_1m=10; confirmed by UAT|
| 4  | score() returns RiskScore with risk_score, is_high_risk, manual_review fields      | VERIFIED   | RiskScore Pydantic model; scorer.py returns RiskScore with all 3 fields          |
| 5  | ScoringConsumer reads payment.transaction.validated, publishes scored events       | VERIFIED   | scoring_consumer.py SOURCE_TOPIC + scored_producer.publish wired                 |
| 6  | Redis feature lookup with 3x50ms retry falls back to defaults + manual_review=true | VERIFIED   | _fetch_features: 3-attempt loop + TimeoutError immediate fallback; manual_review forced at step 7 |
| 7  | State transitions SCORING then AUTHORIZED/FLAGGED written to payment_state_log    | VERIFIED   | _process_message steps 4+9: two write_transition calls; UAT confirmed DB writes  |
| 8  | payment.alert.triggered published only for risk_score >= 0.7                      | VERIFIED   | scoring_consumer.py:300 `if risk_result.is_high_risk: self._alert_producer.publish` |
| 9  | POST /score accepts 8 feature fields and returns risk_score, is_high_risk, manual_review | VERIFIED | ml_service.py:70-91; UAT confirmed risk_score: 0.00336                       |
| 10 | Application-level idempotency guard skips duplicate SCORING state writes           | VERIFIED   | _is_duplicate_scoring() queries payment_state_log; _process_message step 3       |
| 11 | scoring-consumer container starts, loads model, connects to all dependencies       | VERIFIED   | Dockerfile.scoring-consumer; docker-compose with depends_on service_healthy; UAT |
| 12 | ml-scoring-service container starts on port 8001, POST /score returns risk scores  | VERIFIED   | Dockerfile.ml-service; docker-compose port 8001:8001; UAT confirmed              |
| 13 | E2E: validated event -> scored event; high-risk -> alert on payment.alert.triggered | VERIFIED  | test_phase5_e2e.py (312 lines, 4 E2E tests); UAT 4/4 passed                     |

**Score:** 13/13 truths verified

---

### Required Artifacts

| Artifact                                               | Expected                                          | Status     | Details                                      |
|--------------------------------------------------------|---------------------------------------------------|------------|----------------------------------------------|
| `payment-backend/models/ml_scoring.py`                 | FeatureVector, RiskScore, ScoredPaymentEvent      | VERIFIED   | 55 lines, all 3 Pydantic models present      |
| `payment-backend/ml/scorer.py`                         | XGBoostScorer + FEATURE_DEFAULTS                  | VERIFIED   | 107 lines, class + 8-key defaults + sys.exit |
| `payment-backend/ml/train.py`                          | Synthetic training script with "Run once" comment | VERIFIED   | 5076 bytes, comment on line 1                |
| `payment-backend/ml/models/model.ubj`                  | Pre-trained XGBoost binary model                  | VERIFIED   | 38,815 bytes — non-empty                    |
| `payment-backend/tests/unit/ml/test_scorer.py`         | Unit tests for scorer (min 60 lines)              | VERIFIED   | 282 lines                                    |
| `payment-backend/kafka/producers/scored_event_producer.py` | ScoredEventProducer for payment.transaction.scored | VERIFIED | 104 lines, TOPIC + 3-retry + flush          |
| `payment-backend/kafka/producers/alert_producer.py`    | AlertProducer for payment.alert.triggered         | VERIFIED   | 104 lines, TOPIC + 3-retry + flush           |
| `payment-backend/kafka/consumers/scoring_consumer.py`  | ScoringConsumer full poll loop                    | VERIFIED   | 371 lines, all pipeline steps documented     |
| `payment-backend/services/ml_service.py`               | FastAPI POST /score + /health + /metrics          | VERIFIED   | 92 lines, lifespan startup, all 3 endpoints  |
| `payment-backend/tests/unit/test_scoring_consumer.py`  | Unit tests (min 80 lines)                         | VERIFIED   | 411 lines                                    |
| `payment-backend/tests/unit/test_ml_service.py`        | FastAPI endpoint unit tests                       | VERIFIED   | 140 lines                                    |
| `payment-backend/tests/integration/test_phase5_integration.py` | Integration tests (min 60 lines)        | VERIFIED   | 309 lines                                    |
| `payment-backend/infra/Dockerfile.scoring-consumer`    | Docker image for ScoringConsumer                  | VERIFIED   | CMD python -m kafka.consumers.scoring_consumer, EXPOSE 8003, HEALTHCHECK |
| `payment-backend/infra/Dockerfile.ml-service`          | Docker image for ml_service                       | VERIFIED   | CMD uvicorn services.ml_service:app --port 8001, EXPOSE 8001, HEALTHCHECK |
| `payment-backend/infra/docker-compose.yml`             | Updated compose with both new services            | VERIFIED   | 254 lines, both services at lines 197-248    |
| `payment-backend/tests/e2e/test_phase5_e2e.py`         | E2E tests (min 40 lines)                          | VERIFIED   | 312 lines, 4 test functions                  |

---

### Key Link Verification

| From                        | To                                   | Via                               | Status   | Evidence                                                   |
|-----------------------------|--------------------------------------|-----------------------------------|----------|------------------------------------------------------------|
| ml/scorer.py                | ml/models/model.ubj                  | xgb.Booster().load_model(path)    | WIRED    | scorer.py:77 `self._booster.load_model(model_path)`        |
| ml/scorer.py                | models/ml_scoring.py                 | returns RiskScore                 | WIRED    | scorer.py:19 `from models.ml_scoring import RiskScore`; returns RiskScore at line 102 |
| scoring_consumer.py         | ml/scorer.py                         | XGBoostScorer.score(features)     | WIRED    | scoring_consumer.py:258 `risk_result = self._scorer.score(features)` |
| scoring_consumer.py         | Redis feat:{event_id}                | HGETALL with 3x50ms retry         | WIRED    | scoring_consumer.py:181 `self._redis.hgetall(f"feat:{event_id}")` in retry loop |
| scoring_consumer.py         | services/state_machine.py            | PaymentStateMachine.write_transition() | WIRED | scoring_consumer.py:246 + 270 two write_transition calls   |
| scoring_consumer.py         | kafka/producers/scored_event_producer.py | ScoredEventProducer.publish()  | WIRED    | scoring_consumer.py:294 `self._scored_producer.publish(...)`|
| scoring_consumer.py         | kafka/producers/alert_producer.py    | AlertProducer.publish() if is_high_risk | WIRED | scoring_consumer.py:300-304 conditional alert publish    |
| docker-compose.yml          | Dockerfile.scoring-consumer          | build context + dockerfile ref    | WIRED    | docker-compose.yml:200 `dockerfile: infra/Dockerfile.scoring-consumer` |
| docker-compose.yml          | Dockerfile.ml-service                | build context + dockerfile ref    | WIRED    | docker-compose.yml:230 `dockerfile: infra/Dockerfile.ml-service` |

---

### Requirements Coverage

| Requirement            | Source Plan | Description                                                      | Status    | Evidence                                                     |
|------------------------|-------------|------------------------------------------------------------------|-----------|--------------------------------------------------------------|
| ML-SCORE-CONTRACT      | 05-01       | 8-feature input, float[0,1] output, is_high_risk >= 0.7, manual_review >= 0.85 | SATISFIED | FeatureVector + RiskScore models; scorer.py thresholds exact |
| MODEL-LOADING          | 05-01       | XGBoost model loaded once at startup, crash on missing file      | SATISFIED | scorer.py sys.exit(1); ml_service lifespan startup           |
| FEATURE-DEFAULTS       | 05-01       | Conservative defaults guarantee manual_review=true              | SATISFIED | FEATURE_DEFAULTS dict; belt-and-suspenders override at step 7 |
| SCORING-CONSUMER       | 05-02       | Kafka poll loop reading payment.transaction.validated            | SATISFIED | ScoringConsumer with SOURCE_TOPIC + full pipeline            |
| FASTAPI-ENDPOINT       | 05-02       | POST /score accepting FeatureVector, returning RiskScore         | SATISFIED | ml_service.py /score endpoint; UAT confirmed risk_score returned |
| STATE-WRITES           | 05-02       | SCORING + AUTHORIZED/FLAGGED transitions to payment_state_log   | SATISFIED | write_transition called twice in _process_message            |
| REDIS-FEATURE-LOOKUP   | 05-02       | HGETALL feat:{event_id} with 3x50ms retry                       | SATISFIED | _fetch_features with FEATURE_RETRY_COUNT=3, FEATURE_RETRY_DELAY_MS=50 |
| KAFKA-PUBLISH          | 05-02       | Publish to payment.transaction.scored + payment.alert.triggered  | SATISFIED | scored_event_producer + alert_producer both wired            |
| DLQ-CONTRACT-COMPLIANCE| 05-02       | Redis timeout falls back to defaults (not DLQ)                  | SATISFIED | TimeoutError caught in _fetch_features, returns defaults     |
| IDEMPOTENCY-GUARD      | 05-02       | Skip duplicate SCORING state writes via DB query                | SATISFIED | _is_duplicate_scoring() queries payment_state_log            |
| DOCKER-SERVICES        | 05-03       | Both containers buildable and runnable via docker-compose        | SATISFIED | Two Dockerfiles + docker-compose; UAT health checks passed   |
| E2E-VERIFICATION       | 05-03       | E2E tests covering full scoring pipeline                         | SATISFIED | test_phase5_e2e.py 4 tests; UAT 4/4 passed                  |

---

### Anti-Patterns Found

None. No TODOs, FIXMEs, print() calls, placeholder returns, or stub implementations found in any Phase 5 implementation file.

---

### Human Verification Results

Human UAT completed prior to this automated verification. All checks passed:

1. **scoring-consumer health** — `curl http://localhost:8003/health` returned `{"status": "ok"}`. PASSED.
2. **ml-scoring-service health** — `curl http://localhost:8001/health` returned `{"status": "ok", "model_loaded": true}`. PASSED.
3. **POST /score live inference** — returned `risk_score: 0.00336, is_high_risk: false, manual_review: false`. PASSED.
4. **scoring-consumer startup logs** — showed `xgboost_model_loaded`, `consumer started`, connected to topic. PASSED.
5. **Unit tests** — 95 passed, 6 pre-existing Spark JVM failures (not regressions from Phase 5). PASSED.
6. **E2E tests** — 4/4 passed against live containers. PASSED.

---

### Summary

Phase 5 goal fully achieved. The ML risk scoring pipeline is deployed end-to-end:

- XGBoostScorer loads a committed 38KB model (model.ubj) and produces calibrated risk scores with correct thresholds (is_high_risk >= 0.7, manual_review >= 0.85).
- ScoringConsumer orchestrates the full pipeline: reads from payment.transaction.validated, assembles features from Redis with 3x50ms retry and 20ms timeout fallback, runs inference, writes two state transitions (SCORING then AUTHORIZED/FLAGGED) to PostgreSQL before Kafka publish, routes high-risk events to payment.alert.triggered.
- FastAPI ml_service serves POST /score on port 8001 with model loaded at startup.
- Both services are containerized with health checks and integrated into docker-compose with correct dependency ordering.
- PaymentState enum extended with SCORING, AUTHORIZED, FLAGGED.
- 95 unit tests + 4 E2E tests passing. All must-haves from Plans 01, 02, 03 satisfied.

---

_Verified: 2026-03-26_
_Verifier: Claude (gsd-verifier)_
