---
phase: 06-financial-ledger
plan: 01
subsystem: database
tags: [sqlalchemy, alembic, postgresql, kafka, prometheus, ledger, double-entry]

# Dependency graph
requires:
  - phase: 05-ml-risk-scoring
    provides: ScoredPaymentEvent, ScoringConsumer, PaymentState enum (AUTHORIZED/FLAGGED)
provides:
  - LedgerEntry SQLAlchemy ORM model (ledger_entries table)
  - ManualReviewQueueEntry SQLAlchemy ORM model (manual_review_queue table)
  - Alembic migration 002: manual_review_queue with append-only triggers
  - Alembic migration 003: ledger_entries with append-only + DEFERRABLE INITIALLY DEFERRED balance trigger
  - LedgerEntryProducer: publishes AUTHORIZED events to payment.ledger.entry
  - ManualReviewRepository: inserts PENDING rows into manual_review_queue
  - ScoringConsumer wired: AUTHORIZED -> ledger publish, FLAGGED+manual_review -> review queue
affects:
  - 06-02: ledger consumer will consume from payment.ledger.entry, write 2 ledger rows per SETTLED tx
  - future reconciliation: ledger_entries table is the source of truth for balance checks

# Tech tracking
tech-stack:
  added: []
  patterns:
    - DEFERRABLE INITIALLY DEFERRED PostgreSQL constraint trigger for balanced double-entry enforcement
    - SQLAlchemy Core insert() for append-only ledger writes (same pattern as PaymentStateMachine)
    - Kafka producer with 3-retry + backoff [1,2,4]s crash-on-exhaustion (same pattern as AlertProducer)

key-files:
  created:
    - payment-backend/models/ledger.py
    - payment-backend/db/migrations/versions/002_create_manual_review_queue.py
    - payment-backend/db/migrations/versions/003_create_ledger_entries.py
    - payment-backend/kafka/producers/ledger_entry_producer.py
    - payment-backend/services/manual_review_repository.py
    - payment-backend/tests/unit/test_ledger_models.py
    - payment-backend/tests/unit/test_ledger_entry_producer.py
    - payment-backend/tests/unit/test_manual_review_repository.py
    - payment-backend/tests/unit/test_scoring_consumer_ledger.py
  modified:
    - payment-backend/models/state_machine.py
    - payment-backend/kafka/consumers/scoring_consumer.py

key-decisions:
  - "DEFERRABLE INITIALLY DEFERRED for ledger balance trigger — allows both DEBIT and CREDIT to be inserted in one transaction before balance is checked at commit"
  - "LedgerEntryProducer mirrors AlertProducer exactly (retry, backoff, crash-on-exhaustion) for consistency"
  - "ManualReviewRepository uses SQLAlchemy Core insert() for explicit append-only semantics — same pattern as PaymentStateMachine"
  - "Prometheus Counter patched in test setup to avoid duplicate registry errors across test instances"

patterns-established:
  - "Pattern 1: Kafka producer mirroring — new topics reuse AlertProducer pattern verbatim (retry, backoff, delivery_report, flush)"
  - "Pattern 2: Repository pattern for append-only DB writes via engine.begin() + Core insert()"
  - "Pattern 3: DEFERRABLE INITIALLY DEFERRED trigger for multi-row invariant enforcement"

requirements-completed:
  - D-01
  - D-02
  - D-03
  - D-04
  - D-05
  - D-06
  - D-07
  - D-11
  - D-12
  - D-13
  - D-14
  - D-15
  - D-16

# Metrics
duration: 7min
completed: 2026-03-27
---

# Phase 06 Plan 01: Financial Ledger Foundation Summary

**Double-entry ledger schema foundation: LedgerEntry + ManualReviewQueueEntry ORM models, 2 Alembic migrations with append-only and DEFERRABLE balance triggers, LedgerEntryProducer, ManualReviewRepository, and ScoringConsumer wired to route AUTHORIZED events to payment.ledger.entry and FLAGGED+manual_review events to manual_review_queue**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-27T05:49:30Z
- **Completed:** 2026-03-27T05:56:12Z
- **Tasks:** 2 completed
- **Files modified:** 11 (9 created + 2 modified)

## Accomplishments

- PaymentState enum extended with SETTLED and MANUAL_REVIEW values for Phase 06 flow
- Alembic migrations 002 + 003 establish append-only tables with PL/pgSQL triggers; migration 003 uses DEFERRABLE INITIALLY DEFERRED constraint trigger to enforce ledger balance at commit time
- ScoringConsumer now routes AUTHORIZED events to payment.ledger.entry and FLAGGED+manual_review events to manual_review_queue (FLAGGED events never touch the ledger)

## Task Commits

Each task was committed atomically:

1. **Task 1: Models + Migrations + LedgerEntryProducer + ManualReviewRepository** - `53abce6` (feat)
2. **Task 2: Wire LedgerEntryProducer + ManualReviewRepository into ScoringConsumer** - `ea92b20` (feat)

## Files Created/Modified

- `payment-backend/models/state_machine.py` - Added SETTLED + MANUAL_REVIEW to PaymentState enum
- `payment-backend/models/ledger.py` - LedgerEntry (ledger_entries) + ManualReviewQueueEntry (manual_review_queue) ORM models
- `payment-backend/db/migrations/versions/002_create_manual_review_queue.py` - Alembic migration: manual_review_queue with append-only triggers
- `payment-backend/db/migrations/versions/003_create_ledger_entries.py` - Alembic migration: ledger_entries with append-only + DEFERRABLE INITIALLY DEFERRED balance trigger
- `payment-backend/kafka/producers/ledger_entry_producer.py` - LedgerEntryProducer publishing to payment.ledger.entry
- `payment-backend/services/manual_review_repository.py` - ManualReviewRepository inserting PENDING rows
- `payment-backend/kafka/consumers/scoring_consumer.py` - Wired ledger + review routing (steps 12a, 12b)
- `payment-backend/tests/unit/test_ledger_models.py` - 22 tests for enum + ORM models
- `payment-backend/tests/unit/test_ledger_entry_producer.py` - 8 tests for LedgerEntryProducer
- `payment-backend/tests/unit/test_manual_review_repository.py` - 5 tests for ManualReviewRepository
- `payment-backend/tests/unit/test_scoring_consumer_ledger.py` - 8 tests for ScoringConsumer wiring

## Decisions Made

- **DEFERRABLE INITIALLY DEFERRED balance trigger:** The balance check trigger fires at transaction commit, not after each INSERT. This allows a single transaction to write both DEBIT and CREDIT entries before the SUM=0 constraint is verified — essential for correct double-entry bookkeeping.
- **LedgerEntryProducer mirrors AlertProducer:** Same retry logic (3 attempts, backoff [1,2,4]s), same crash-on-exhaustion pattern — consistency across all Kafka producers in the service.
- **Prometheus Counter patched in tests:** Creating multiple ScoringConsumer instances in the same test session causes duplicate counter registration errors. Patching `Counter` in `_make_consumer_with_mocks()` avoids this without changing production code.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed Prometheus duplicate counter registration in tests**
- **Found during:** Task 2 (test_scoring_consumer_ledger.py)
- **Issue:** Creating multiple ScoringConsumer instances in the same pytest session caused `ValueError: Duplicated timeseries in CollectorRegistry` because Prometheus counters are registered globally
- **Fix:** Patched `kafka.consumers.scoring_consumer.Counter` with a MagicMock in `_make_consumer_with_mocks()` test helper
- **Files modified:** `payment-backend/tests/unit/test_scoring_consumer_ledger.py`
- **Verification:** All 8 tests pass without registration errors
- **Committed in:** `ea92b20` (Task 2 commit)

**2. [Rule 1 - Bug] Fixed test_flagged_without_manual_review test — Redis mock returning empty**
- **Found during:** Task 2 test execution
- **Issue:** Test expected `manual_review=False` but step 7 (belt-and-suspenders) overrides `manual_review=True` when `features_available=False`. Redis was returning empty dict in test → features_available=False → override fired
- **Fix:** Changed `_setup_flagged(manual_review=False)` to return a full feature dict from Redis mock, so `features_available=True` and step 7 doesn't override scorer output
- **Files modified:** `payment-backend/tests/unit/test_scoring_consumer_ledger.py`
- **Verification:** Test now correctly verifies the `manual_review=False` path
- **Committed in:** `ea92b20` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 1 - bug in test setup)
**Impact on plan:** Both fixes were test-layer corrections. Production code implemented exactly as planned.

## Issues Encountered

None — both deviations were caught and fixed within the same task commit.

## Known Stubs

None — all fields are wired to real data sources. LedgerEntryProducer publishes real event data. ManualReviewRepository inserts real scored event payloads.

## User Setup Required

None — no external service configuration required. Alembic migrations (002, 003) will be applied when the ValidationConsumer container starts (it owns the schema per D-D2).

## Next Phase Readiness

- Plan 06-02 can now consume from `payment.ledger.entry` and write 2 balanced ledger entries (1 DEBIT + 1 CREDIT) per event
- The `enforce_ledger_balance` DEFERRABLE trigger is in migration 003, ready to enforce SUM=0 constraint
- `ManualReviewRepository` rows in `manual_review_queue` are ready for Phase 06-02 to finalize MANUAL_REVIEW state transitions

## Self-Check: PASSED

- All 9 created files: FOUND
- Task commit 53abce6: FOUND
- Task commit ea92b20: FOUND
- Full test suite: 43/43 PASSED

---
*Phase: 06-financial-ledger*
*Completed: 2026-03-27*
