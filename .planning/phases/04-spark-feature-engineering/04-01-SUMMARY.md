---
phase: 04-spark-feature-engineering
plan: "01"
subsystem: spark
tags: [pyspark, redis, welford, feature-engineering, ml, structlog]

# Dependency graph
requires:
  - phase: 03-state-machine-rate-limiting-downstream-publish
    provides: ValidatedPaymentEvent model with merchant_id, published to payment.transaction.validated
provides:
  - Pure feature compute functions (compute_hour_of_day, compute_weekend_flag, compute_amount_cents_log, update_merchant_stats_and_zscore)
  - foreachBatch Redis sink (write_features_to_redis) covering all 8 ML features
  - Unit test suite for feature_functions.py (14 tests) and redis_sink.py (9 tests)
affects:
  - 04-02 (streaming job wires these functions into Spark readStream + writeStream)
  - 04-03 (Docker/E2E depends on redis_sink and feature_functions being correct)
  - 05-ml-scoring (reads feat:{event_id} hashes written here)

# Tech tracking
tech-stack:
  added:
    - pyspark==3.5.8 (added to requirements.txt)
  patterns:
    - TDD RED-GREEN per task (tests before implementation)
    - Welford online algorithm for streaming mean/stddev without storing history
    - foreachBatch Redis pipeline pattern (all writes batched, pipe.execute() required)
    - MagicMock isolation for Redis and Spark Row objects in unit tests

key-files:
  created:
    - payment-backend/spark/__init__.py
    - payment-backend/spark/feature_functions.py
    - payment-backend/spark/redis_sink.py
    - payment-backend/tests/unit/spark/__init__.py
    - payment-backend/tests/unit/spark/conftest.py
    - payment-backend/tests/unit/spark/test_feature_functions.py
    - payment-backend/tests/unit/spark/test_redis_sink.py
  modified:
    - payment-backend/requirements.txt (added pyspark==3.5.8)

key-decisions:
  - "Welford count < 3 threshold (not < 2): cold start returns 0.0 AND single prior point returns 0.0 — need 2 prior observations to produce meaningful z-score"
  - "Pure Python functions in feature_functions.py, NOT PySpark Column objects — these run inside foreachBatch on driver, not distributed executors"
  - "foreachBatch Redis pipeline: all writes buffered in pipe, single pipe.execute() per batch — silent data loss if execute() omitted"
  - "device_seen:{stripe_customer_id} TTL=300s, feat:{event_id} TTL=3600s — matches CLAUDE.md locked conventions"
  - "merchant_risk_score defaults to 0.5 when merchant:risk:{merchant_id} key missing — safe neutral default"

patterns-established:
  - "Redis key pattern: feat:{event_id}, merchant:stats:{merchant_id}, merchant:risk:{merchant_id}, device_seen:{stripe_customer_id}"
  - "All logging via structlog.get_logger() — no print() calls anywhere in spark/"
  - "MagicMock for redis.Redis in unit tests with side_effect dict for stateful Welford tests"
  - "conftest.py SparkSession fixture: scope=session, local[2], shuffle.partitions=2"

requirements-completed: [FEAT-03, FEAT-04, FEAT-05, FEAT-06, FEAT-07, FEAT-08, FEAT-09]

# Metrics
duration: 13min
completed: 2026-03-25
---

# Phase 4 Plan 01: Feature Functions and Redis Sink Summary

**Pure Welford z-score, device-switch tracking, and foreachBatch Redis pipeline covering all 8 ML input features, with 23 unit tests passing.**

## Performance

- **Duration:** 13 min
- **Started:** 2026-03-25T13:26:33Z
- **Completed:** 2026-03-25T13:39:49Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments

- Implemented all 4 pure feature transform functions in feature_functions.py: hour_of_day, weekend_flag, amount_cents_log, and Welford online z-score with Redis state
- Implemented redis_sink.py write_features_to_redis() foreachBatch handler with merchant risk lookup, device switch tracking, and all 8 ML features written as a Redis Hash with TTL 3600s
- 23 unit tests pass: 14 for feature_functions.py, 9 for redis_sink.py, all using MagicMock for Redis and Spark isolation

## Task Commits

Each task was committed atomically:

1. **Task 1: Create feature_functions.py with pure feature transforms and Welford zscore** - `b59168f` (feat)
2. **Task 2: Create redis_sink.py with foreachBatch Redis write function and unit tests** - `d13bbd9` (feat)

**Plan metadata:** (docs commit — see below)

_Note: TDD tasks — tests written before implementation in each task._

## Files Created/Modified

- `payment-backend/spark/__init__.py` — Package marker for spark module
- `payment-backend/spark/feature_functions.py` — Pure Python feature transforms: compute_hour_of_day, compute_weekend_flag, compute_amount_cents_log, update_merchant_stats_and_zscore (Welford)
- `payment-backend/spark/redis_sink.py` — foreachBatch handler: merchant risk score, device switch flag, Welford z-score, Redis pipeline HSET+EXPIRE for feat:{event_id}
- `payment-backend/tests/unit/spark/__init__.py` — Package marker for spark tests
- `payment-backend/tests/unit/spark/conftest.py` — Session-scoped SparkSession fixture (local[2])
- `payment-backend/tests/unit/spark/test_feature_functions.py` — 14 unit tests for all 4 pure functions
- `payment-backend/tests/unit/spark/test_redis_sink.py` — 9 unit tests for redis sink with full mock isolation
- `payment-backend/requirements.txt` — Added pyspark==3.5.8

## Decisions Made

- **Welford threshold count < 3 (not < 2):** After Welford update, count < 3 means return 0.0. This ensures both cold start (0 priors) and single-point merchants (1 prior) produce the safe 0.0 default. Need 2+ prior observations for a reliable variance estimate.
- **Pure Python, not PySpark Column objects:** feature_functions.py uses datetime.fromisoformat() and math.log1p() operating on plain str/int values. PySpark Column expressions (F.hour, F.log1p) belong in Plan 02's streaming job. foreachBatch rows arrive pre-collected on the driver.
- **pipe.execute() is mandatory:** Redis pipeline buffers writes silently. Omitting execute() causes zero writes with no error. Added explicit comment in code.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Welford z-score count threshold adjusted from < 2 to < 3**

- **Found during:** Task 1 (test_welford_zscore_single_point failed)
- **Issue:** Plan says "if count < 2 return 0.0" (after Welford update). With initial count=1, update makes count=2 which is not < 2, so zscore was computed. The plan's behavior test expects count=1 (prior) → 0.0 return.
- **Fix:** Changed threshold to count < 3, meaning both count=1 (cold start) and count=2 (one prior) return 0.0. Need count=3+ (2+ priors) for reliable variance.
- **Files modified:** payment-backend/spark/feature_functions.py
- **Verification:** All 14 feature_functions tests pass including sequential 100/200/300 → zscore≈1.0
- **Committed in:** b59168f (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 bug fix)
**Impact on plan:** Fix necessary for test correctness. Math is sound — need 2 prior observations for meaningful variance. No scope creep.

## Issues Encountered

- pyspark==3.5.8 was not in requirements.txt and not installed in venv — installed synchronously before running tests (Rule 3 auto-fix, blocking issue)

## Known Stubs

None — all 8 features are fully computed, no hardcoded placeholders or TODO values.

## User Setup Required

None — no external service configuration required for this plan. Tests run entirely with mocks.

## Next Phase Readiness

- feature_functions.py and redis_sink.py are ready for Plan 02 to wire into SparkSession + readStream + writeStream
- SparkSession fixture in conftest.py ready for any Spark DataFrame tests needed in Plan 02/03
- All Redis key patterns established and tested match CLAUDE.md locked conventions

## Self-Check: PASSED

All 7 created files confirmed present on disk. Task commits b59168f and d13bbd9 confirmed in git log.

---
*Phase: 04-spark-feature-engineering*
*Completed: 2026-03-25*
