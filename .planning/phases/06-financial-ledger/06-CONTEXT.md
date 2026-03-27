# Phase 6: Financial Ledger - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Consume `payment.ledger.entry`, write balanced double-entry ledger entries to PostgreSQL (append-only, DB trigger enforces SUM=0 per transaction_id), update state to SETTLED/FLAGGED.

Explicitly out of scope: manual review UI, human approval workflows, multi-currency support beyond schema future-proofing, settlement routing as a standalone service.

</domain>

<decisions>
## Implementation Decisions

### Consumer Design
- **D-01:** Ledger consumer reads from `payment.ledger.entry` (locked per ROADMAP).
- **D-02:** The scoring consumer (Phase 5) is extended to publish to `payment.ledger.entry` for AUTHORIZED events, via a new `LedgerEntryProducer` class — same isolation pattern as `AlertProducer` and `ScoredEventProducer`. The consumer calls it; it doesn't own the logic.
- **D-03:** Accepted trade-off: scoring consumer now triggers 3 downstream systems (scored event, alert, ledger entry). In production Stripe, a dedicated settlement service would own this. For this project, this is a conscious simplification — call it out in README as the production alternative.
- **D-04:** FLAGGED events never touch `payment.ledger.entry`. A ledger entry without settlement is meaningless. The FLAGGED → MANUAL_REVIEW boundary is clean.

### Manual Review Queue
- **D-05:** The scoring consumer writes a `manual_review_queue` row on every MANUAL_REVIEW state transition. Table schema: `transaction_id`, `risk_score`, `payload JSONB`, `created_at`, `status = PENDING`.
- **D-06:** Isolated into a `ManualReviewRepository` class — scoring consumer calls it, repository owns the insert logic. Consumer stays clean.
- **D-07:** This table is the queryable surface for Phase 8 (Dashboard) and Phase 9 (Feature Replay). Without it, those phases build on sand.

### Error Handling & DLQ
- **D-08:** Ledger consumer crashes on DB failure — no retry logic. Docker restarts the consumer; Kafka replays the message; idempotency guard catches the duplicate. Crash = safe because both ledger inserts are inside a single transaction AND the consumer uses manual offset commit.
- **D-09:** DLQ (`failure_reason: LEDGER_WRITE_FAIL`) is only for non-retryable errors: DB constraint violations (SUM≠0 trigger), malformed payload (missing/non-integer `amount_cents`). DLQ is NOT used for systemic DB outages — crash-and-restart handles those.
- **D-10:** "Keep running on DLQ" is wrong for DB failures (if DB is down, every message would DLQ-flood). Crash-and-restart is the correct recovery mechanism here, unlike the Redis per-event miss pattern in the scoring consumer.

### Ledger Schema
- **D-11:** `ledger_entries` columns (all load-bearing, none optional):
  - `id` BIGSERIAL PRIMARY KEY
  - `transaction_id` TEXT NOT NULL
  - `amount_cents` BIGINT NOT NULL (always positive)
  - `entry_type` TEXT NOT NULL (`DEBIT` or `CREDIT`)
  - `merchant_id` TEXT NOT NULL
  - `currency` TEXT NOT NULL DEFAULT 'USD'
  - `source_event_id` TEXT NOT NULL (the Stripe event_id — enables ledger → Kafka → Stripe tracing)
  - `created_at` TIMESTAMPTZ NOT NULL DEFAULT NOW() (server-side only)
- **D-12:** No `description`/`notes` column. Labels are constructed at query time in dbt marts (`entry_type || ' for ' || source_event_id`). A column that duplicates derivable info is noise in an audit table.
- **D-13:** `merchant_id` and `source_event_id` are load-bearing for Phase 7 dbt reconciliation — without them, `stg_ledger_entries` requires two-hop joins through `payment_state_log` with filtering complexity.
- **D-14:** `currency` is included as cheap future-proofing. MVP is USD-only per CLAUDE.md; the column costs nothing now and avoids a migration later.

### Append-Only Enforcement
- **D-15:** `ledger_entries` gets the same PL/pgSQL trigger pattern as `payment_state_log` — `BEFORE UPDATE` and `BEFORE DELETE` triggers that RAISE EXCEPTION. Enforced at DB level, not application level.
- **D-16:** Every SETTLED transaction = exactly 2 rows: one `DEBIT`, one `CREDIT`, both with the same `transaction_id`. DB trigger enforces `SUM(amount_cents WHERE entry_type='CREDIT') - SUM(amount_cents WHERE entry_type='DEBIT') = 0` per `transaction_id`. This is a separate trigger from the append-only one.

### Claude's Discretion
- Idempotency guard implementation (Redis key or DB query — same options as scoring consumer)
- Consumer group name (suggest: `ledger-service`)
- Health endpoint port (suggest: 8004)
- Alembic migration numbering and whether `manual_review_queue` migration is 002 and `ledger_entries` is 003 or combined

</decisions>

<specifics>
## Specific Ideas

- "Isolate the ledger publish into a LedgerEntryProducer class — same pattern as AlertProducer and ScoredEventProducer. When ledger conditions change, you open ledger_entry_producer.py, not scoring_consumer.py." — exact pattern mandate from discussion.
- The scoring consumer's stated responsibility: **orchestrate the post-scoring flow**. The ledger producer's responsibility: **know what a valid ledger entry looks like**.
- Known Gotchas addition: "Ledger consumer crashes on DB failure by design — Docker restart + Kafka replay is the recovery mechanism. DLQ is only for non-retryable errors (constraint violations, malformed payload), not systemic DB outages."
- README callout: scoring consumer triggers 3 downstream systems as a conscious simplification; production alternative is a dedicated settlement service.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Locked Rules (CLAUDE.md)
- `CLAUDE.md` §"Ledger Rules" — append-only, 1 DEBIT + 1 CREDIT, SUM=0 trigger, BIGINT cents, USD only
- `CLAUDE.md` §"Payment State Machine" — AUTHORIZED → SETTLED, FLAGGED → MANUAL_REVIEW transitions
- `CLAUDE.md` §"DLQ Contract" — LEDGER_WRITE_FAIL failure reason, required DLQ message fields
- `CLAUDE.md` §"Kafka Topics" — `payment.ledger.entry` topic name locked

### Existing Patterns to Replicate
- `payment-backend/kafka/consumers/scoring_consumer.py` — consumer poll loop, health endpoint, manual offset commit, crash-on-unhandled pattern
- `payment-backend/kafka/producers/alert_producer.py` — producer class isolation pattern (`LedgerEntryProducer` replicates this)
- `payment-backend/services/state_machine.py` — `PaymentStateMachine` with `write_transition()` — ledger consumer reuses this
- `payment-backend/db/migrations/versions/001_create_payment_state_log.py` — append-only trigger pattern for `ledger_entries` and `manual_review_queue` migrations

### dbt Downstream (Phase 7)
- `CLAUDE.md` §"dbt Models" — `stg_ledger_entries`, `fact_ledger_balanced`, `reconciliation_summary` mart — these consume `ledger_entries` columns directly

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `PaymentStateMachine.write_transition()` — ledger consumer calls this for AUTHORIZED → SETTLED and FLAGGED → MANUAL_REVIEW
- `DLQProducer` — reused as-is for `LEDGER_WRITE_FAIL` messages
- `_HealthHandler` (scoring_consumer.py) — copy pattern for ledger consumer health endpoint on port 8004
- `ScoredPaymentEvent` (models/ml_scoring.py) — input model for the ledger consumer; has `risk_score`, `is_high_risk`, `amount_cents`, `merchant_id`, `event_id` all available

### Established Patterns
- Manual offset commit only — `consumer.store_offsets(msg)` then `consumer.commit()` after all DB + Kafka writes
- DB write before Kafka publish (D-D4 from STATE.md) — write ledger entries to DB before publishing downstream
- Crash on unhandled exception — `raise` in except block, Docker restart + Kafka replay
- `structlog` for all logging — no `print()`
- `PaymentStateMachine` injection via constructor — pass `create_engine(db_url)` at startup

### Integration Points
- **Scoring consumer** gains: `LedgerEntryProducer` call (AUTHORIZED path) + `ManualReviewRepository` call (MANUAL_REVIEW path)
- **New Alembic migrations**: `002_create_manual_review_queue.py` + `003_create_ledger_entries.py`
- **New consumer**: `ledger_consumer.py` reads `payment.ledger.entry`, writes 2 ledger rows, updates state to SETTLED
- **docker-compose.yml**: new `ledger-consumer` service on port 8004

</code_context>

<deferred>
## Deferred Ideas

- Manual review approval workflow (human marks PENDING → APPROVED → triggers settlement) — Phase 8 or separate phase
- Multi-currency ledger (non-USD amounts) — post-MVP
- Dedicated settlement routing service (decouples scoring consumer from ledger trigger) — production architecture note only, not implemented here

</deferred>

---

*Phase: 06-financial-ledger*
*Context gathered: 2026-03-27*
