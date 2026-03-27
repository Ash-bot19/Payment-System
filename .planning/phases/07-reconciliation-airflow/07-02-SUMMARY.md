---
phase: 07-reconciliation-airflow
plan: 02
subsystem: payments
tags: [airflow, dag, taskflow-api, stripe, sqlalchemy, reconciliation, tdd, structlog]

# Dependency graph
requires:
  - phase: 07-01
    provides: ReconciliationMessage Pydantic model + ReconciliationProducer
  - phase: 06-financial-ledger
    provides: ledger_entries table (queried for DEBIT rows and duplicate detection)
provides:
  - nightly_reconciliation Airflow DAG with 3 TaskFlow tasks
  - detect_duplicates(): DUPLICATE_LEDGER detection via GROUP BY HAVING COUNT(*) > 2
  - fetch_stripe_window(): Stripe API pagination, succeeded filter, epoch window
  - compare_and_publish(): MISSING_INTERNALLY + AMOUNT_MISMATCH detection
  - 19 unit tests covering all 3 task functions with mocked dependencies
affects: [07-03-reconciliation-consumer]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "TaskFlow API @task at module level (not nested) — task functions importable as plain callables for unit tests"
    - "Airflow stub for unit tests: mock @dag/@task decorators via sys.modules injection before import"
    - "_InDagContext flag distinguishes @dag body execution (return sentinel) from direct test call (run real logic)"
    - "stripe.max_network_retries = 3 per D-17 for transient network failures"
    - "ds param uses Airflow {{ ds }} template — deterministic midnight-to-midnight UTC window, never datetime.now()"

key-files:
  created:
    - payment-backend/airflow/dags/__init__.py
    - payment-backend/airflow/dags/nightly_reconciliation.py
    - payment-backend/tests/unit/test_nightly_reconciliation.py
  modified: []

key-decisions:
  - "Module-level @task functions (not nested inside @dag body) — makes them directly callable in unit tests without Airflow context"
  - "Airflow stub uses _InDagContext flag: @task wrappers return MagicMock sentinel inside @dag body, run real logic when called directly in tests"
  - "AMOUNT_MISMATCH stripe_amount_cents=None (not populated) per D-11 — diff_cents = stripe - internal captures the difference; internal_amount_cents is the reference"
  - "detect_duplicates and fetch_stripe_window wired in parallel in DAG body; compare_and_publish receives fetch_stripe_window XCom output"

patterns-established:
  - "airflow.decorators stub pattern for unit-testing TaskFlow DAGs without installing Apache Airflow"

requirements-completed: []

# Metrics
duration: 7min
completed: 2026-03-27
---

# Phase 7 Plan 2: nightly_reconciliation Airflow DAG Summary

**Airflow TaskFlow DAG with 3 module-level tasks detecting all 3 reconciliation discrepancy types (DUPLICATE_LEDGER, MISSING_INTERNALLY, AMOUNT_MISMATCH) with 19 unit tests using stubbed decorators — no Airflow/DB/Stripe required**

## Performance

- **Duration:** ~7 min
- **Started:** 2026-03-27T18:42:17Z
- **Completed:** 2026-03-27T18:49:01Z
- **Tasks:** 1 of 1
- **Files modified:** 3

## Accomplishments

- `nightly_reconciliation` Airflow DAG (dag_id, schedule=@daily, catchup=False)
- `detect_duplicates()`: SQL GROUP BY transaction_id HAVING COUNT(*) > 2, merchant subquery, publishes DUPLICATE_LEDGER via ReconciliationProducer.publish_batch()
- `fetch_stripe_window()`: stripe.PaymentIntent.list() auto-pagination, succeeded filter, epoch window from ds param, max_network_retries=3
- `compare_and_publish()`: queries DEBIT-only ledger rows, MISSING_INTERNALLY for Stripe-only PIs, AMOUNT_MISMATCH for amount differences, skips clean matches
- DAG wiring: detect_duplicates and fetch_stripe_window run in parallel; compare_and_publish depends on fetch_stripe_window XCom
- 19 unit tests (TDD): 5 detect_duplicates + 4 fetch_stripe_window + 5 compare_and_publish + 4 dag structure; all pass with mocked dependencies

## Task Commits

1. **Task 1: nightly_reconciliation DAG + unit tests** - `3601568` (feat, TDD)

_Note: TDD — tests written first (RED: ImportError for missing module), implementation written to pass (GREEN: 19/19)._

## Files Created/Modified

- `payment-backend/airflow/dags/__init__.py` — Package init for dags namespace
- `payment-backend/airflow/dags/nightly_reconciliation.py` — Airflow DAG with 3 TaskFlow tasks
- `payment-backend/tests/unit/test_nightly_reconciliation.py` — 19 unit tests

## Decisions Made

- Module-level `@task` functions (not nested inside `@dag` body) — makes each task directly importable and callable in unit tests without any Airflow runtime context
- `_InDagContext` flag in test stub: `@task` wrappers return `MagicMock` sentinel when called inside `@dag` body (prevents DB/Stripe execution during module import), run real logic when called directly in tests
- `AMOUNT_MISMATCH` sets `stripe_amount_cents=None` per D-11 — `diff_cents = stripe - internal` fully captures the discrepancy; `internal_amount_cents` is the reference value
- `detect_duplicates` and `fetch_stripe_window` wired as parallel branches in DAG body; `compare_and_publish` receives `fetch_stripe_window` XCom as its sole input

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None — all 3 discrepancy types are fully implemented and tested.

## Issues Encountered

Minor: Initial test stub using `@dag` as a no-op wrapper caused `nightly_reconciliation()` at module level to invoke task functions immediately, triggering PostgreSQL connection attempts. Fixed by introducing `_InDagContext` flag so `@task` wrappers distinguish DAG-body calls (return sentinel) from direct test calls (run real logic). This is Rule 3 (blocking issue) auto-fixed.

## User Setup Required

None — tests run without Airflow, PostgreSQL, Stripe, or Kafka. DAG requires these at runtime only.

## Next Phase Readiness

- `nightly_reconciliation` DAG is production-ready for Airflow deployment
- Plan 03 (reconciliation consumer) can import and depend on `payment.reconciliation.queue` messages
- DAG tasks use `ds` Airflow template variable — deterministic replay is built in

---
*Phase: 07-reconciliation-airflow*
*Completed: 2026-03-27*

## Self-Check: PASSED

- FOUND: payment-backend/airflow/dags/nightly_reconciliation.py
- FOUND: payment-backend/tests/unit/test_nightly_reconciliation.py
- FOUND: .planning/phases/07-reconciliation-airflow/07-02-SUMMARY.md
- FOUND: commit 3601568
