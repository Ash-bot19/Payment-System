---
phase: 03-state-machine-rate-limiting-downstream-publish
plan: 01
subsystem: database
tags: [alembic, sqlalchemy, postgresql, redis, kafka, state-machine, rate-limiting]

# Dependency graph
requires:
  - phase: 02-kafka-consumer-validation-dlq
    provides: "DLQProducer pattern, ValidatedPaymentEvent model, validation_consumer structure"
provides:
  - "payment_state_log Alembic migration with PL/pgSQL append-only triggers"
  - "PaymentState enum (INITIATED, VALIDATED, FAILED)"
  - "PaymentStateLogEntry SQLAlchemy ORM model"
  - "ProcessingResult dataclass"
  - "PaymentStateMachine class with write_transition/record_initiated/record_validated/record_failed"
  - "MerchantRateLimiter class with Redis INCR+EXPIRE pattern"
  - "ValidatedEventProducer mirroring DLQProducer to payment.transaction.validated"
affects:
  - "03-02 — consumer integration wires these classes into ValidationConsumer"
  - "03-03 — integration tests exercise all three classes against real services"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Alembic migration with raw SQL PL/pgSQL trigger for append-only enforcement"
    - "SQLAlchemy Core insert() for append-only table writes (not ORM session)"
    - "Injectable class design (engine, redis_client passed via constructor) for testability"
    - "Mirror pattern: ValidatedEventProducer identical structure to DLQProducer"

key-files:
  created:
    - payment-backend/db/alembic.ini
    - payment-backend/db/migrations/env.py
    - payment-backend/db/migrations/script.py.mako
    - payment-backend/db/migrations/versions/001_create_payment_state_log.py
    - payment-backend/models/state_machine.py
    - payment-backend/services/state_machine.py
    - payment-backend/services/rate_limiter.py
    - payment-backend/kafka/producers/validated_event_producer.py
  modified:
    - payment-backend/.env.example

key-decisions:
  - "SQLAlchemy Core insert() used for PaymentStateMachine writes — explicit append-only semantics, avoids ORM session complexity for this table"
  - "DB-level PL/pgSQL trigger enforces append-only on payment_state_log at database layer — physically immutable audit log"
  - "Alembic env.py reads DATABASE_URL_SYNC (psycopg2) separate from DATABASE_URL (asyncpg) — same DB, different drivers for sync vs async use"
  - "ValidatedEventProducer mirrors DLQProducer exactly — same retry logic, same crash-on-exhaustion, same flush(timeout=5)"
  - "MerchantRateLimiter uses INCR+EXPIRE with 2-minute TTL safety margin — locked key format rate_limit:{merchant_id}:{minute_bucket}"

patterns-established:
  - "Injectable dependencies: classes receive Redis/Engine/bootstrap_servers via __init__ — never instantiate internally"
  - "Alembic raw SQL via op.execute() for DDL that SQLAlchemy cannot auto-generate (trigger functions, triggers)"
  - "Producer retry pattern: 3 attempts, backoff [1,2,4]s, crash-and-restart on exhaustion — no silent drops"

requirements-completed: [SM-01, SM-02, SM-03, RATELIMIT-01]

# Metrics
duration: 3min
completed: 2026-03-24
---

# Phase 03 Plan 01: State Machine + Rate Limiting + Downstream Publish — Building Blocks Summary

**Alembic migration creating append-only payment_state_log with PL/pgSQL UPDATE/DELETE triggers, plus injectable PaymentStateMachine, MerchantRateLimiter (Redis INCR), and ValidatedEventProducer (mirrors DLQProducer) — all standalone testable classes**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-24T07:01:46Z
- **Completed:** 2026-03-24T07:04:52Z
- **Tasks:** 2
- **Files modified:** 9

## Accomplishments

- Created Alembic infrastructure (alembic.ini, env.py, script.py.mako) reading DATABASE_URL_SYNC from environment with psycopg2 driver
- Created migration 001_create_payment_state_log.py with full table DDL, two performance indexes, PL/pgSQL trigger function, and BEFORE UPDATE/DELETE triggers enforcing append-only constraint at the DB layer
- Created models/state_machine.py with PaymentState enum, PaymentStateLogEntry SQLAlchemy ORM model, and ProcessingResult dataclass
- Created services/state_machine.py with PaymentStateMachine using Core insert() + engine.begin() — no ORM session complexity
- Created services/rate_limiter.py with MerchantRateLimiter using locked key format and INCR+EXPIRE Redis pattern
- Created kafka/producers/validated_event_producer.py mirroring DLQProducer exactly with 3-retry exponential backoff

## Task Commits

Each task was committed atomically:

1. **Task 1: Alembic setup + payment_state_log migration with append-only trigger** - `332551d` (feat)
2. **Task 2: PaymentStateMachine, MerchantRateLimiter, and ValidatedEventProducer classes** - `5431a0a` (feat)

**Plan metadata:** (docs commit — see below)

## Files Created/Modified

- `payment-backend/db/alembic.ini` - Alembic config with script_location=migrations, sqlalchemy.url default
- `payment-backend/db/migrations/env.py` - Env reading DATABASE_URL_SYNC, Base from models.state_machine
- `payment-backend/db/migrations/script.py.mako` - Standard Alembic revision template
- `payment-backend/db/migrations/versions/001_create_payment_state_log.py` - Table DDL + append-only PL/pgSQL triggers
- `payment-backend/models/state_machine.py` - PaymentState enum, PaymentStateLogEntry ORM, ProcessingResult dataclass
- `payment-backend/services/state_machine.py` - PaymentStateMachine with Core insert writes
- `payment-backend/services/rate_limiter.py` - MerchantRateLimiter with Redis INCR+EXPIRE
- `payment-backend/kafka/producers/validated_event_producer.py` - ValidatedEventProducer mirroring DLQProducer
- `payment-backend/.env.example` - Added DATABASE_URL_SYNC and DEFAULT_MERCHANT_ID

## Decisions Made

- SQLAlchemy Core insert() used in PaymentStateMachine — not ORM sessions. Append-only tables with DB-level triggers and no ORM session lifecycle needed; Core is explicit and correct here.
- Separate DATABASE_URL_SYNC env var for Alembic (psycopg2 synchronous driver) alongside existing DATABASE_URL (asyncpg async driver) — same PostgreSQL instance, different drivers for different use cases.
- ValidatedEventProducer is an exact mirror of DLQProducer — same retry logic, same crash-on-exhaustion. Consistency makes both producers auditable and predictable.

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

- Virtual environment requires `.venv/Scripts/python` invocation on Windows — standard `python` resolves system Python without SQLAlchemy. All imports verified using the venv interpreter.

## User Setup Required

None — no external service configuration required beyond what is already in .env.example.

## Next Phase Readiness

- All three injectable classes are ready for Plan 02 to wire into ValidationConsumer.__init__
- Alembic migration is ready to run: `alembic -c payment-backend/db/alembic.ini upgrade head` (requires PostgreSQL running)
- Plan 03-02 will modify ValidationConsumer to instantiate PaymentStateMachine, MerchantRateLimiter, and ValidatedEventProducer and integrate them into the _process_message() flow per D-03 processing order

---
*Phase: 03-state-machine-rate-limiting-downstream-publish*
*Completed: 2026-03-24*
