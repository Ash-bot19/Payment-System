---
status: passed
phase: 06-financial-ledger
source: [06-VERIFICATION.md]
started: 2026-03-27T11:40:00Z
updated: 2026-03-27T11:50:00Z
---

## Current Test

All tests passed 2026-03-27.

## Tests

### 1. Docker Stack Integration Test
expected: Run `docker-compose up --build ledger-consumer` against a running stack (kafka + postgres + redis already healthy). Send a message to `payment.ledger.entry`. Container reaches `healthy` status; logs show `ledger_consumer_started`; two rows appear in `ledger_entries` table (1 DEBIT + 1 CREDIT); `payment_state_log` gains a row with `to_state = 'SETTLED'`.
result: PASS — Container started and logged `ledger_consumer_started` + `health_server_started`. Sent two messages (uat-ledger-evt-001 + uat-ledger-evt-002). Both processed: 6 rows in `ledger_entries` (3 DEBIT+CREDIT pairs), 2 `AUTHORIZED → SETTLED` rows in `payment_state_log`. Idempotency replay of evt-001 correctly skipped with `ledger_duplicate_settled_skip`. Fixed bug: added `enable.auto.offset.store=false` to Kafka consumer config (commit a875500).

### 2. Alembic Migration Apply — Live DB
expected: Run `alembic upgrade head` against a running PostgreSQL instance with migration 001 already applied. Migrations 002 and 003 apply cleanly; `\d manual_review_queue` shows all columns including JSONB payload; `\d ledger_entries` shows all columns; `\dS ledger_entries` shows all three triggers (`no_update_ledger_entries`, `no_delete_ledger_entries`, `check_ledger_balance`).
result: PASS — `alembic upgrade head` applied 001→002→003 cleanly. `manual_review_queue` verified: id, transaction_id, risk_score, payload JSONB, created_at, status columns + 2 append-only triggers. `ledger_entries` verified: all 8 columns + 3 triggers (`no_update_ledger_entries`, `no_delete_ledger_entries`, `check_ledger_balance DEFERRABLE INITIALLY DEFERRED`).

### 3. Balance Trigger Enforcement
expected: Attempt to insert a single DEBIT row into `ledger_entries` without a matching CREDIT row in the same transaction. PostgreSQL raises `Ledger imbalance for transaction_id ...: balance = ... (must be 0)` at commit time. Verify DEFERRED nature — both rows in one transaction succeed, one row alone fails at commit.
result: PASS — Single DEBIT (amount_cents=9999) raised `ERROR: Ledger imbalance for transaction_id test-tx-uat-2: balance = -9999 (must be 0)`. Balanced pair (DEBIT=5000, CREDIT=5000) committed successfully and resulted in 2 rows in the table. Append-only enforcement also verified: `UPDATE` raised `ERROR: ledger_entries is append-only`.

## Summary

total: 3
passed: 3
issues: 1
pending: 0
skipped: 0
blocked: 0

## Gaps

### Issue found and fixed: store_offsets KafkaException
- **Symptom:** LedgerConsumer crashed after processing first message with `KafkaError{code=_INVALID_ARG}: StoreOffsets failed: Local: Invalid argument or configuration`
- **Root cause:** Missing `enable.auto.offset.store: false` in Kafka Consumer config — required when using `store_offsets()` for manual offset management
- **Fix:** Added `"enable.auto.offset.store": False` to Consumer config in `ledger_consumer.py`
- **Commit:** a875500
- **Status:** resolved
