# Phase 7: Reconciliation + Airflow — Context

**Gathered:** 2026-03-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Build a nightly Airflow DAG that reconciles the internal `ledger_entries` table against Stripe's API, publishes discrepancies to `payment.reconciliation.queue`, and surfaces three types of mismatches: missing internally (Type 1), amount mismatch (Type 3), and duplicate ledger entries (Type 4).

Explicitly out of scope: manual discrepancy resolution UI, re-settlement triggers, multi-currency comparison, BigQuery ingestion of reconciliation messages (Phase 8), dbt `reconciliation_summary` mart (Phase 8).

</domain>

<decisions>
## Implementation Decisions

### A. Discrepancy Detection Scope

- **D-01:** Detect three discrepancy types only:
  - **Type 1 — MISSING_INTERNALLY**: Stripe has a `succeeded` PaymentIntent for the window; no matching row in `ledger_entries`.
  - **Type 3 — AMOUNT_MISMATCH**: Both sides have the record; `amount_cents` differs between `ledger_entries` and Stripe's `amount_received`.
  - **Type 4 — DUPLICATE_LEDGER**: More than 2 ledger rows exist for a single `transaction_id` in the window.
- **D-02:** Drop Type 2 (missing in Stripe). The write path is event-driven — you cannot ledger a transaction without a Stripe webhook triggering it. The condition is architecturally prevented upstream. Adding it would require fetching every internal row and cross-checking Stripe, at high cost for near-zero real-world occurrence. If asked: "ghost ledger entries are architecturally prevented by the event-driven write path."
- **D-03:** Match on `transaction_id` (the PaymentIntent ID). `source_event_id` is a webhook artifact — Stripe's API does not index by it. `PaymentIntent.retrieve(transaction_id)` works directly. Both fields are stored in `ledger_entries`.
- **D-04:** No age cutoff on discrepancies. Every discrepancy in the window is reported. Idempotency is handled by the fixed time window design (Area B), not by age filters. Re-reporting a known open discrepancy across multiple nights is correct reconciliation behavior.
- **D-05:** Publish-only. No DB state writes for discrepancies. The Kafka message on `payment.reconciliation.queue` is the authoritative signal. Phase 8 sources `reconciliation_summary` from BigQuery (via Kafka), not PostgreSQL. A `reconciliation_results` table would have no consumer until Phase 8 — and Phase 8 reads BigQuery anyway.

### B. Reconciliation Time Window

- **D-06:** Fixed window: midnight-to-midnight UTC. Derived from Airflow's `{{ ds }}` data interval start variable. Deterministic re-runs are non-negotiable for a reconciliation DAG — rolling windows make it impossible to reproduce "what did this run see?"
- **D-07:** Window keyed on `ledger_entries.created_at`. `payment_state_log` SETTLED timestamp would require a join; in this pipeline, the scoring consumer writes both atomically, so the timestamps are functionally equivalent. "What got ledgered yesterday" is the correct framing for a ledger audit.
- **D-08:** Re-runs are idempotent in input (same window = same source data), but duplicates are accepted in Kafka output. Deduplicating against Kafka would require a tracking table or Redis processed-IDs set — unjustified complexity for a portfolio project. **Constraint locked for Phase 8:** the `reconciliation_summary` dbt mart must tolerate duplicate messages (use `COUNT DISTINCT` or `ROW_NUMBER()` dedup).
- **D-09:** Backfill supported natively via `{{ ds }}`. The window start/end is always derived from the Airflow data interval, never hardcoded as `today - 1`. This costs nothing and enables arbitrary historical re-runs.

### C. `payment.reconciliation.queue` Message Contract

- **D-10:** One Kafka message per discrepancy (not one batch summary per run). Phase 8 needs per-transaction queryability for `WHERE discrepancy_type = 'AMOUNT_MISMATCH' AND merchant_id = X`. Batch summaries are a reporting artifact, not a data contract.
- **D-11:** Message schema (all fields present on every message; null where not applicable per type):

```
transaction_id            string          — PaymentIntent ID (always present)
discrepancy_type          string (enum)   — MISSING_INTERNALLY | AMOUNT_MISMATCH | DUPLICATE_LEDGER
stripe_payment_intent_id  string | null   — null for Type 4 (redundant with transaction_id)
internal_amount_cents     int | null      — null for Type 1 (no internal row exists)
stripe_amount_cents       int | null      — null for Type 3/4 (no Stripe comparison needed)
diff_cents                int | null      — null for Type 1/4; (stripe - internal) for Type 3
currency                  string          — always present; USD for MVP
merchant_id               string          — always present
stripe_created_at         timestamp | null — Stripe PaymentIntent created_at; null for Type 3/4
run_date                  date            — Airflow {{ ds }}; enables BigQuery partitioning by run
```

- **D-12:** Note for Phase 8 planner — for Type 4, `stripe_payment_intent_id` is null because `transaction_id` already IS the PaymentIntent ID. Not a gap; they refer to the same entity.
- **D-13:** Full Stripe PaymentIntent object is NOT included. Only the five fields needed for a meaningful discrepancy row. If Phase 9 needs additional Stripe fields, re-fetch at display time.

### D. Stripe API Pull Scope

- **D-14:** Use `stripe.PaymentIntent.list(created={"gte": window_start, "lte": window_end})` with auto-pagination. One-by-one lookup (`retrieve` per transaction) means N API calls for N transactions. Bulk list means a handful of paginated calls regardless of volume. Fetch-then-compare in memory; discard clean matches.
- **D-15:** Filter Stripe results to `status = "succeeded"` only. `requires_capture` means authorized-but-not-captured — it never fired a `payment_intent.succeeded` webhook, was never ledgered, and flagging it would be a false positive. `requires_capture` detection is a separate business ops concern.
- **D-16:** Type 4 (DUPLICATE_LEDGER) is detected via pure SQL — no Stripe API call needed:
  ```sql
  SELECT transaction_id, COUNT(*) as row_count
  FROM ledger_entries
  WHERE created_at BETWEEN window_start AND window_end
  GROUP BY transaction_id
  HAVING COUNT(*) > 2
  ```
  Run this as a separate DAG task before the Stripe pull. No API rate limit consumed.
- **D-17:** Rate limiting: set `stripe.max_network_retries = 3` only. The Stripe Python SDK provides built-in exponential backoff for free. No custom rate limiter. For a test dataset, paginated list calls will not approach Stripe's 25 req/s limit. **Document in README:** production would need a token bucket or Stripe's dedicated reconciliation API.

### DAG Structure (Claude's Discretion — Guidance Only)

The planner should consider a 3-task DAG:
1. `detect_duplicates` — pure SQL Type 4 check, publish results to Kafka
2. `fetch_stripe_window` — `PaymentIntent.list()` for the window, build in-memory set
3. `compare_and_publish` — Type 1 + Type 3 comparison against `ledger_entries`, publish discrepancies

Tasks 1 and 2 can run in parallel (no dependency). Task 3 depends on Task 2.

</decisions>

<specifics>
## Specific Constraints

- `payment.reconciliation.queue` topic name is LOCKED (CLAUDE.md §"Kafka Topics")
- Airflow consumer group: suggest `reconciliation-service`
- DAG ID: suggest `nightly_reconciliation`
- Schedule: `@daily` (Airflow cron — runs at midnight UTC)
- Airflow version: 2.9+ (CLAUDE.md stack)
- All Pydantic message models go in `models/` — no inline schemas in the DAG file
- structlog for all logging — no print()
- Phase 8 dbt `reconciliation_summary` mart depends on this message contract being stable — treat D-11 schema as locked post-planning

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Locked Rules (CLAUDE.md)
- `CLAUDE.md` §"Kafka Topics" — `payment.reconciliation.queue` topic name locked
- `CLAUDE.md` §"DLQ Contract" — if Airflow publishes to DLQ, all fields required
- `CLAUDE.md` §"Ledger Rules" — `ledger_entries` append-only, amount_cents always positive BIGINT
- `CLAUDE.md` §"dbt Models" — `reconciliation_summary` mart will consume this phase's Kafka output

### Existing Patterns to Replicate
- `payment-backend/kafka/producers/alert_producer.py` — Kafka producer class isolation pattern (ReconciliationProducer should replicate this)
- `payment-backend/kafka/consumers/ledger_consumer.py` — consumer poll loop, manual offset commit, crash-on-OperationalError pattern
- `payment-backend/models/` — all Pydantic schemas live here; ReconciliationMessage model goes here
- `payment-backend/db/migrations/versions/` — Alembic migration pattern if any new tables needed (none expected this phase)

### Prior Phase Context
- `06-CONTEXT.md` D-13: `merchant_id` and `source_event_id` are load-bearing in `ledger_entries` — available for joins
- `06-CONTEXT.md` D-11: `ledger_entries` schema — `transaction_id`, `amount_cents`, `entry_type`, `merchant_id`, `currency`, `source_event_id`, `created_at`
- `STATE.md` — Phase 8 BigQuery + dbt depends on this phase's Kafka output; `reconciliation_summary` mart must handle duplicate messages

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `DLQProducer` (`kafka/producers/dlq_producer.py`) — reuse as-is if DAG needs DLQ routing
- `AlertProducer` (`kafka/producers/alert_producer.py`) — copy pattern for `ReconciliationProducer` (retry + backoff + crash-on-exhaustion)
- `LedgerEntry` ORM model (`models/ledger.py`) — use for SQL queries against `ledger_entries`
- `PaymentStateMachine` (`services/state_machine.py`) — no writes needed this phase, but import pattern is established
- Airflow `dags/` directory exists at `payment-backend/airflow/dags/` — ready for the DAG file

### Integration Points
- **New DAG**: `payment-backend/airflow/dags/nightly_reconciliation.py`
- **New Pydantic model**: `payment-backend/models/reconciliation.py` — `ReconciliationMessage` with D-11 schema
- **New Kafka producer**: `payment-backend/kafka/producers/reconciliation_producer.py`
- **No new DB migrations** expected — reads `ledger_entries` (existing), writes only to Kafka
- **docker-compose.yml**: Airflow service may need adding if not yet present

### Established Patterns
- DB write before Kafka publish — maintain for consistency even in DAG context
- `structlog` bound to run context (`run_date`, `window_start`, `window_end`)
- Manual offset commit not applicable to Airflow DAG (not a Kafka consumer) — DAG uses `at-least-once` publish semantics; duplicates handled downstream per D-08

</code_context>

<deferred>
## Deferred Ideas

- Manual discrepancy resolution workflow (human marks MISSING_INTERNALLY → investigated → resolved) — Phase 9 or separate phase
- Stripe webhook-based reconciliation trigger (real-time instead of nightly batch) — post-MVP, different architecture
- Type 2 detection (missing in Stripe / ghost ledger entries) — explicitly dropped; production alternative is audit log cross-check
- Token bucket rate limiting for Stripe API at scale — document in README as production gap per D-17
- `reconciliation_results` PostgreSQL table for queryable discrepancy history — only valuable if something queries PostgreSQL directly; Phase 8 uses BigQuery

</deferred>

---

*Phase: 07-reconciliation-airflow*
*Context gathered: 2026-03-27*
