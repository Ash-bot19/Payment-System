---
phase: "08-bigquery-dbt"
plan: "01"
subsystem: "airflow-dag / db-migrations / orm-models"
tags: ["airflow", "alembic", "sqlalchemy", "reconciliation", "postgresql"]
dependency_graph:
  requires: ["07-03"]
  provides: ["reconciliation_discrepancies table", "persist_discrepancies Airflow task"]
  affects: ["08-02 (dbt reconciliation_summary mart source)"]
tech_stack:
  added: []
  patterns:
    - "SQLAlchemy Core insert() + engine.begin() for append-only writes"
    - "Airflow TaskFlow XCom: compare_and_publish -> persist_discrepancies"
    - "Explicit Table() schema without autoload_with (no DB connection at DAG parse time)"
key_files:
  created:
    - "payment-backend/db/migrations/versions/004_create_reconciliation_discrepancies.py"
    - "payment-backend/models/reconciliation_discrepancy.py"
  modified:
    - "payment-backend/airflow/dags/nightly_reconciliation.py"
    - "payment-backend/tests/unit/test_nightly_reconciliation.py"
decisions:
  - "Table defined with explicit Column() list in persist_discrepancies (not autoload_with) to prevent DB connection at DAG parse time"
  - "id and created_at omitted from Table() insert schema — PostgreSQL assigns BIGSERIAL and NOW() server defaults automatically"
  - "compare_and_publish returns messages list directly (not a separate serialization step)"
metrics:
  duration_minutes: 4
  completed_date: "2026-03-28"
  tasks_completed: 2
  files_created: 2
  files_modified: 2
---

# Phase 8 Plan 1: Reconciliation Discrepancies — DB Foundation + DAG Extension Summary

**One-liner:** Alembic migration 004 creates append-only `reconciliation_discrepancies` table; Airflow DAG extended with `persist_discrepancies` task wired via XCom after `compare_and_publish`.

---

## What Was Built

### Migration 004: `reconciliation_discrepancies`

New append-only PostgreSQL table as the dbt source for `reconciliation_summary` mart.

Columns (12 total):

| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL PK | auto-assigned |
| transaction_id | TEXT NOT NULL | indexed |
| discrepancy_type | TEXT NOT NULL | MISSING_INTERNALLY / AMOUNT_MISMATCH / DUPLICATE_LEDGER; indexed |
| stripe_payment_intent_id | TEXT nullable | |
| internal_amount_cents | BIGINT nullable | |
| stripe_amount_cents | BIGINT nullable | |
| diff_cents | BIGINT nullable | |
| currency | TEXT NOT NULL | default 'USD' |
| merchant_id | TEXT NOT NULL | |
| stripe_created_at | TIMESTAMPTZ nullable | |
| run_date | DATE NOT NULL | indexed |
| created_at | TIMESTAMPTZ NOT NULL | server default NOW() |

Append-only triggers: `no_update_reconciliation_discrepancies` + `no_delete_reconciliation_discrepancies` via `prevent_discrepancies_mutation()` PL/pgSQL function.

Revision chain: `003` → `004`.

---

### ORM Model: `ReconciliationDiscrepancy`

`payment-backend/models/reconciliation_discrepancy.py` — SQLAlchemy 2.0 `mapped_column` style, inherits from `models.state_machine.Base`. Mirrors all 12 columns exactly.

---

### DAG Extension: `persist_discrepancies` task

`payment-backend/airflow/dags/nightly_reconciliation.py` changes:

1. `compare_and_publish` return type: `None` → `list[dict[str, Any]]`; `return messages` added at end (returns `[]` if no discrepancies).

2. New `@task persist_discrepancies(discrepancies, ds) -> int`:
   - No-ops and returns `0` for empty list (logs `persist_discrepancies_skipped`)
   - Defines `Table("reconciliation_discrepancies", ...)` with explicit `Column()` list — no `autoload_with`
   - Uses `engine.begin()` + `conn.execute(insert(recon_table), discrepancies)`
   - Returns `len(discrepancies)`; logs `persist_discrepancies_written` with count

3. DAG body updated:
   ```python
   discrepancy_list = compare_and_publish(stripe_data)
   persist_discrepancies(discrepancy_list)
   ```

---

## Unit Tests

**27 unit tests in `test_nightly_reconciliation.py`** (all passing).

Breakdown by class:

| Class | Count | Notes |
|-------|-------|-------|
| TestDetectDuplicates | 5 | unchanged |
| TestFetchStripeWindow | 4 | unchanged |
| TestCompareAndPublish | 7 | +2 new: returns_discrepancy_list, empty_stripe_no_internal |
| TestPersistDiscrepancies | 5 | new: empty_list, inserts_rows, returns_count, logs_written, skipped_logs |
| TestDagStructure | 6 | +1 new: has_persist_discrepancies_function |

---

## Deviations from Plan

None — plan executed exactly as written.

---

## Commits

| Hash | Message |
|------|---------|
| d306c06 | feat(08-01): Alembic migration 004 + ReconciliationDiscrepancy ORM model |
| 3456bed | feat(08-01): extend DAG with persist_discrepancies task + 8 new unit tests |

---

## Self-Check: PASSED

- `payment-backend/db/migrations/versions/004_create_reconciliation_discrepancies.py` — FOUND
- `payment-backend/models/reconciliation_discrepancy.py` — FOUND
- `payment-backend/airflow/dags/nightly_reconciliation.py` — FOUND (persist_discrepancies present)
- `payment-backend/tests/unit/test_nightly_reconciliation.py` — FOUND (27 tests pass)
- Commit d306c06 — FOUND
- Commit 3456bed — FOUND
