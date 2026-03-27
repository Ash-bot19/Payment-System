---
phase: 06-financial-ledger
plan: 02
subsystem: kafka-consumer
tags: [kafka, sqlalchemy, postgresql, docker, ledger, double-entry, prometheus]

# Dependency graph
requires:
  - phase: 06-01
    provides: LedgerEntry ORM model, ledger_entries migration (003), DEFERRABLE balance trigger, PaymentState.SETTLED enum, LedgerEntryProducer
  - phase: 05-ml-risk-scoring
    provides: ScoringConsumer publishing AUTHORIZED events to payment.ledger.entry
provides:
  - LedgerConsumer class consuming payment.ledger.entry
  - Balanced DEBIT+CREDIT write per AUTHORIZED payment in single DB transaction
  - AUTHORIZED → SETTLED state transition after ledger write
  - Dockerfile.ledger-consumer (port 8004)
  - docker-compose ledger-consumer service
affects:
  - future reconciliation: ledger_entries rows are source of truth for balance verification (Phase 07)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - LedgerConsumer follows ScoringConsumer pattern exactly (health server, poll loop, crash-on-OperationalError)
    - Idempotency via payment_state_log SETTLED row check (same pattern as ScoringConsumer _is_duplicate_scoring)
    - DLQ routing for IntegrityError/InternalError, crash (re-raise) for OperationalError (per D-08, D-09, D-10)

key-files:
  created:
    - payment-backend/kafka/consumers/ledger_consumer.py
    - payment-backend/infra/Dockerfile.ledger-consumer
    - payment-backend/tests/unit/test_ledger_consumer.py
    - payment-backend/tests/e2e/test_ledger_e2e.py
  modified:
    - payment-backend/infra/docker-compose.yml

key-decisions:
  - "OperationalError (DB down) is re-raised (crash) not routed to DLQ — systemic failures use Docker restart + Kafka replay per D-08, D-10"
  - "IntegrityError and InternalError (constraint violations including DEFERRABLE trigger) route to DLQ with LEDGER_WRITE_FAIL — these are non-retryable per D-09"
  - "Idempotency guard checks payment_state_log for existing SETTLED row — prevents duplicate DEBIT+CREDIT writes on message replay"
  - "Both DEBIT and CREDIT inserts share single engine.begin() context — DEFERRABLE INITIALLY DEFERRED balance trigger fires at commit, not per-insert"

# Metrics
duration: 5min
completed: 2026-03-27
---

# Phase 06 Plan 02: Ledger Consumer Summary

**LedgerConsumer reading payment.ledger.entry and writing exactly 2 balanced rows (DEBIT+CREDIT) per AUTHORIZED payment in a single DB transaction, transitioning state to SETTLED, with DLQ routing for constraint violations and crash-on-OperationalError for systemic DB failures**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-27T05:59:49Z
- **Completed:** 2026-03-27T06:04:49Z
- **Tasks:** 2 completed
- **Files modified:** 5 (4 created + 1 modified)

## Accomplishments

- LedgerConsumer replicates the ScoringConsumer structure: threaded health server, poll loop, manual offset commit, crash-on-unhandled-exception pattern
- Both ledger inserts (DEBIT + CREDIT) are written in a single `engine.begin()` context, allowing the DEFERRABLE INITIALLY DEFERRED balance trigger to enforce SUM=0 at commit time
- Prometheus counters: `ledger_entries_written_total`, `ledger_settled_total`, `ledger_dlq_total`

## Task Commits

Each task was committed atomically:

1. **Task 1: LedgerConsumer + Unit Tests** - `1287a2f` (feat)
2. **Task 2: Docker + E2E Tests** - `f230785` (feat)

## Files Created/Modified

- `payment-backend/kafka/consumers/ledger_consumer.py` - LedgerConsumer (146 lines), SOURCE_TOPIC="payment.ledger.entry", CONSUMER_GROUP="ledger-service", HEALTH_PORT=8004
- `payment-backend/infra/Dockerfile.ledger-consumer` - python:3.11-slim, EXPOSE 8004, urllib healthcheck, CMD ledger_consumer
- `payment-backend/infra/docker-compose.yml` - Added ledger-consumer service on port 8004 with depends_on kafka/postgres/redis (service_healthy)
- `payment-backend/tests/unit/test_ledger_consumer.py` - 12 unit tests covering all error paths
- `payment-backend/tests/e2e/test_ledger_e2e.py` - 4 E2E tests (skip when services not running)

## Decisions Made

- **OperationalError crashes (not DLQ):** DB connection failures are systemic outages — crash triggers Docker restart, Kafka replays from last committed offset. Routing to DLQ would swallow the message permanently (per D-08, D-10).
- **IntegrityError/InternalError → DLQ:** Constraint violations (including DEFERRABLE balance trigger rejection) are non-retryable — the payload itself is malformed. DLQ with LEDGER_WRITE_FAIL preserves the event for auditing.
- **Idempotency via SETTLED state check:** Checking `payment_state_log WHERE to_state = 'SETTLED'` is the canonical idempotency guard — consistent with ScoringConsumer's `_is_duplicate_scoring` pattern.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all fields wired to real data sources. LedgerConsumer reads real Kafka events and writes real PostgreSQL rows.

## Issues Encountered

None — TDD RED→GREEN→commit in single pass for both tasks.

## Self-Check: PASSED

- `payment-backend/kafka/consumers/ledger_consumer.py`: FOUND
- `payment-backend/infra/Dockerfile.ledger-consumer`: FOUND
- `payment-backend/infra/docker-compose.yml` contains `ledger-consumer`: FOUND
- `payment-backend/tests/unit/test_ledger_consumer.py`: FOUND
- `payment-backend/tests/e2e/test_ledger_e2e.py`: FOUND
- Task 1 commit `1287a2f`: FOUND
- Task 2 commit `f230785`: FOUND
- 55/55 unit tests: PASSED
- 4/4 E2E tests: SKIPPED (services not running — expected behavior)

---
*Phase: 06-financial-ledger*
*Completed: 2026-03-27*
