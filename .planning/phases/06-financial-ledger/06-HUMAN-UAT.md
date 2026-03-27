---
status: partial
phase: 06-financial-ledger
source: [06-VERIFICATION.md]
started: 2026-03-27T11:40:00Z
updated: 2026-03-27T11:40:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Docker Stack Integration Test
expected: Run `docker-compose up --build ledger-consumer` against a running stack (kafka + postgres + redis already healthy). Send a message to `payment.ledger.entry`. Container reaches `healthy` status; logs show `ledger_consumer_started`; two rows appear in `ledger_entries` table (1 DEBIT + 1 CREDIT); `payment_state_log` gains a row with `to_state = 'SETTLED'`.
result: [pending]

### 2. Alembic Migration Apply — Live DB
expected: Run `alembic upgrade head` against a running PostgreSQL instance with migration 001 already applied. Migrations 002 and 003 apply cleanly; `\d manual_review_queue` shows all columns including JSONB payload; `\d ledger_entries` shows all columns; `\dS ledger_entries` shows all three triggers (`no_update_ledger_entries`, `no_delete_ledger_entries`, `check_ledger_balance`).
result: [pending]

### 3. Balance Trigger Enforcement
expected: Attempt to insert a single DEBIT row into `ledger_entries` without a matching CREDIT row in the same transaction. PostgreSQL raises `Ledger imbalance for transaction_id ...: balance = ... (must be 0)` at commit time. Verify DEFERRED nature — both rows in one transaction succeed, one row alone fails at commit.
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps
