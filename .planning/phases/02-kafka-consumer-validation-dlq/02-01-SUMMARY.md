---
phase: 02-kafka-consumer-validation-dlq
plan: 01
subsystem: payments
tags: [kafka, pydantic, validation, dlq, structlog]

# Dependency graph
requires:
  - phase: 01-foundation-ingestion
    provides: WebhookReceivedMessage model and WebhookProducer pattern used as base for DLQProducer
provides:
  - ValidatedPaymentEvent Pydantic v2 model (6 fields) — canonical output for all downstream consumers
  - DLQMessage Pydantic v2 model (6 fields) — locked DLQ contract enforced in model layer
  - validate_event() function — schema + business-rule validation with SUPPORTED_EVENT_TYPES allowlist
  - ValidationError exception class — carries reason + detail for DLQ routing
  - DLQProducer class — publishes to payment.dlq with 3-retry exponential backoff
  - 11 unit tests covering valid events, schema failures, and business-rule failures
affects:
  - 02-02-kafka-consumer (wires DLQProducer and validate_event into poll loop)
  - 03-state-machine (uses ValidatedPaymentEvent as input)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ValidationError carries structured reason (DLQ enum) and detail (human-readable) — consumer uses reason to populate DLQMessage.failure_reason"
    - "validate_event is pure function — no I/O, no Kafka dependency, fully unit-testable in isolation"
    - "DLQProducer mirrors WebhookProducer with added retry loop: backoff_seconds=[1,2,4], logger.critical + re-raise on exhaustion"
    - "SUPPORTED_EVENT_TYPES is a frozenset constant — immutable, O(1) lookup, locked to exactly 3 types"

key-files:
  created:
    - payment-backend/models/validation.py
    - payment-backend/kafka/consumers/validation_logic.py
    - payment-backend/kafka/producers/dlq_producer.py
    - payment-backend/tests/unit/test_validation_logic.py
  modified: []

key-decisions:
  - "validate_event raises ValidationError (not returns None/optional) — consumer catches it and routes to DLQ; no ambiguity on failure path"
  - "Amount positivity enforced for payment_intent.succeeded only (D-12) — canceled/failed events may have amount=0"
  - "Negative amounts on succeeded log logger.warning before raising (D-13) — preserves observability for anomalous data"
  - "DLQProducer retries 3x with backoff [1,2,4]s then crashes (D-17) — crash-and-restart preferred over silent drop"

patterns-established:
  - "Pure validation functions in kafka/consumers/ — no external dependencies, easy to unit test"
  - "DLQ failure_reason is a string matching the locked enum, not a Python Enum class — simpler serialization to Kafka JSON"

requirements-completed:
  - VALID-01
  - VALID-02
  - VALID-03
  - QUAL-02

# Metrics
duration: pre-committed (implementation complete before this executor run)
completed: 2026-03-23
---

# Phase 02 Plan 01: Validation Models, Logic, and DLQ Producer Summary

**Pydantic v2 validation models (ValidatedPaymentEvent, DLQMessage), pure validate_event function with schema + business-rule checks, DLQProducer with 3-retry exponential backoff, and 11 passing unit tests**

## Performance

- **Duration:** pre-committed (implementation already complete)
- **Started:** 2026-03-23
- **Completed:** 2026-03-23
- **Tasks:** 3 (Task 1 TDD, Task 2, Task 3 TDD)
- **Files modified:** 4

## Accomplishments

- ValidatedPaymentEvent and DLQMessage Pydantic v2 models enforce the locked DLQ contract at the type level
- validate_event() enforces SUPPORTED_EVENT_TYPES allowlist (3 types), amount extraction with no fallback (D-10/D-11), and amount positivity for succeeded only (D-12/D-13)
- DLQProducer mirrors WebhookProducer exactly, adds 3-retry loop with exponential backoff [1,2,4]s, crashes on exhaustion per D-17 so Docker restarts and replays the message
- 11 unit tests pass: 3 happy-path, 3 schema failures, 2 business-rule failures, 2 DLQMessage model tests, 1 SUPPORTED_EVENT_TYPES invariant

## Task Commits

Each task was committed atomically:

1. **Task 1+3: Validation models, logic, and unit tests (TDD)** - `f209964` (feat)
2. **Task 2: DLQProducer class** - `5670002` (feat)

**Plan metadata:** (this SUMMARY commit)

## Files Created/Modified

- `payment-backend/models/validation.py` - ValidatedPaymentEvent (6 fields) and DLQMessage (6 fields) Pydantic v2 models
- `payment-backend/kafka/consumers/validation_logic.py` - SUPPORTED_EVENT_TYPES frozenset, ValidationError exception, validate_event() function
- `payment-backend/kafka/producers/dlq_producer.py` - DLQProducer class mirroring WebhookProducer with retry logic
- `payment-backend/tests/unit/test_validation_logic.py` - 11 unit tests, all passing

## Decisions Made

- validate_event raises ValidationError (not returns optional) — unambiguous failure path, consumer always knows it must DLQ
- Amount positivity applied to payment_intent.succeeded only; canceled/failed events allowed to have amount=0 per D-12
- DLQProducer crash-on-exhaustion pattern (re-raise KafkaException) preferred over silent drop — Docker restart replays the message

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- All building blocks ready for 02-02: validate_event, DLQProducer, and ValidatedPaymentEvent are importable and tested
- 02-02 consumer poll loop just needs to call validate_event(), catch ValidationError, and call dlq_producer.publish()
- No blockers

---
*Phase: 02-kafka-consumer-validation-dlq*
*Completed: 2026-03-23*
