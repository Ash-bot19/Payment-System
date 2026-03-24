---
phase: 03-state-machine-rate-limiting-downstream-publish
plan: 02
subsystem: kafka
tags: [kafka, redis, postgres, sqlalchemy, alembic, pydantic, rate-limiting, state-machine]

# Dependency graph
requires:
  - phase: 03-01
    provides: PaymentStateMachine, MerchantRateLimiter, ValidatedEventProducer, Alembic migration for payment_state_log
  - phase: 02-01
    provides: ValidatedPaymentEvent, DLQMessage, validate_event(), DLQProducer
  - phase: 02-02
    provides: ValidationConsumer poll loop, /health endpoint, Dockerfile.validation, docker-compose service

provides:
  - ValidationConsumer wired with PaymentStateMachine, MerchantRateLimiter, ValidatedEventProducer following D-03 processing order
  - merchant_id field on ValidatedPaymentEvent model (published downstream)
  - Rate-limited events write INITIATED+FAILED to state log with no DLQ publish
  - Valid events write INITIATED+VALIDATED and publish to payment.transaction.validated
  - Invalid events write INITIATED+FAILED and publish to payment.dlq
  - Alembic migrations run automatically at consumer startup
  - docker-compose validation-consumer depends on postgres with service_healthy condition

affects:
  - Phase 04 (Spark feature engineering — consumes payment.transaction.validated, expects merchant_id field)
  - Phase 05 (ML scoring — downstream of payment.transaction.validated)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - D-03 processing order: rate-limit → INITIATED → validate → VALIDATED/FAILED → publish → commit offset
    - merchant_id extracted from raw payload metadata before any validation (D-06)
    - Rate-limited events silently dropped to state log only (no DLQ) — DLQ reserved for schema/business failures
    - Alembic upgrade("head") at consumer startup ensures schema is always current
    - DATABASE_URL_SYNC (psycopg2) env var used for SQLAlchemy synchronous engine (distinct from async asyncpg URL)

key-files:
  created: []
  modified:
    - payment-backend/models/validation.py
    - payment-backend/kafka/consumers/validation_logic.py
    - payment-backend/kafka/consumers/validation_consumer.py
    - payment-backend/infra/docker-compose.yml
    - payment-backend/tests/unit/test_validation_logic.py

key-decisions:
  - "merchant_id added to ValidatedPaymentEvent so all downstream consumers receive merchant context without re-parsing raw Stripe payload"
  - "validate_event() signature updated to accept merchant_id as parameter — consumer extracts it before calling validate to keep extraction logic in one place (D-06)"
  - "Rate-limiting applied only to payment_intent.succeeded events — canceled/failed events have no revenue impact and should not be throttled"
  - "Alembic migrations run at ValidationConsumer startup (not separately) — consumer is the owner of its DB schema, simplifies deployment"

patterns-established:
  - "D-03 order pattern: all downstream consumers processing Kafka events should follow rate-limit → state-init → business-logic → state-update → publish → commit"
  - "extract-before-validate pattern: always extract infrastructure fields (merchant_id) from raw payload before passing to domain validation"

requirements-completed: [SM-02, SM-03, SM-04]

# Metrics
duration: 15min
completed: 2026-03-24
---

# Phase 03 Plan 02: Wire ValidationConsumer with State Machine, Rate Limiter, and Downstream Publish

**ValidationConsumer fully wired following D-03 processing order — rate limiting, state machine writes, and payment.transaction.validated publish with merchant_id propagated to all downstream consumers**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-03-24T~07:10Z
- **Completed:** 2026-03-24T~07:25Z
- **Tasks:** 2 of 2
- **Files modified:** 5

## Accomplishments

- Added `merchant_id: str` to `ValidatedPaymentEvent` — all downstream Kafka consumers now receive merchant context in the validated event payload
- Rewrote `ValidationConsumer._process_message()` to follow the locked D-03 processing order: rate-limit check → INITIATED write → validate → VALIDATED/FAILED write → publish → commit offset
- Updated `docker-compose.yml` so `validation-consumer` depends on `postgres` with `condition: service_healthy`, and added `DATABASE_URL_SYNC`, `REDIS_URL`, `DEFAULT_MERCHANT_ID` environment variables

## Task Commits

1. **Task 1: Add merchant_id to ValidatedPaymentEvent and wire consumer with all Phase 3 dependencies** - `24d756a` (feat)
2. **Task 2: Update Docker Compose and Dockerfile for PostgreSQL dependency + run existing tests** - `6d73cdd` (feat)

**Plan metadata:** (docs commit below)

## Files Created/Modified

- `payment-backend/models/validation.py` - Added `merchant_id: str` field after `stripe_customer_id`
- `payment-backend/kafka/consumers/validation_logic.py` - Added `merchant_id: str` parameter to `validate_event()`, passes it to `ValidatedPaymentEvent` constructor
- `payment-backend/kafka/consumers/validation_consumer.py` - Complete rewrite of `__init__`, `_process_message`, `_cleanup`; added `_extract_merchant_id()`, `_run_migrations()`; imports for PaymentStateMachine, MerchantRateLimiter, ValidatedEventProducer
- `payment-backend/infra/docker-compose.yml` - Added postgres depends_on with service_healthy; added DATABASE_URL_SYNC, REDIS_URL, DEFAULT_MERCHANT_ID environment vars
- `payment-backend/tests/unit/test_validation_logic.py` - Updated all `validate_event()` calls to pass `merchant_id="test_merchant"`; added assertion on `merchant_id` in happy-path test

## Decisions Made

- `validate_event()` signature extended with `merchant_id: str = "unknown_merchant"` default — preserves backwards compatibility for tests that don't need to specify merchant context
- Rate limiting applied only to `payment_intent.succeeded` events (not canceled/failed) — throttling non-revenue events would cause data loss with no business benefit
- Alembic migrations run synchronously at consumer startup via `_run_migrations()` — consumer owns its schema, eliminates the need for a separate migration job

## Deviations from Plan

None - plan executed exactly as written. All acceptance criteria satisfied in both tasks.

## Issues Encountered

None. The `validate_event()` signature change required updating 6 existing test call sites, which was already specified in the plan (step 8 of Task 1).

## User Setup Required

None - no external service configuration required. `DEFAULT_MERCHANT_ID=test_merchant` is already in `.env.example`.

## Next Phase Readiness

- `payment.transaction.validated` now carries `merchant_id`, enabling Spark feature engineering (Phase 04) to group velocity windows per merchant without re-parsing Stripe payloads
- PostgreSQL `payment_state_log` is populated by the consumer — state machine data is available for observability queries
- All 16 unit tests pass (9 validation logic + 2 health + 2 webhook + 3 model tests)

---
*Phase: 03-state-machine-rate-limiting-downstream-publish*
*Completed: 2026-03-24*
