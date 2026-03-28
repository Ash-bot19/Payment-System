---
phase: 8
name: bigquery-dbt
milestone: v2.0
status: context-complete
created: 2026-03-28
---

# Phase 8 Context: BigQuery + dbt

## Phase Goal

Stream ledger and transaction data to BigQuery, build dbt models
(staging → dimensions → facts → marts), run custom `assert_ledger_balanced`
test. BigQuery does NOT need to be live in this phase — Phase 11 handles
GCP setup. Local dev runs dbt against PostgreSQL.

---

## Area 1: BigQuery Ingestion Mechanism

### Decisions

- **Ingestion timing: nightly batch via Airflow.** dbt marts are aggregations
  (hourly_payment_volume, fraud_metrics) — they need correct, reconciled data,
  not sub-minute freshness. Near-real-time BQ ingestion adds exactly-once
  complexity and BQ streaming insert costs for zero analytical benefit.

- **Ingestion path: PostgreSQL → BigQuery directly, via new Airflow tasks.**
  Kafka topics are ephemeral processing channels, not replay stores. PostgreSQL
  is the system of record with reconciled, settled state. No new Kafka sink
  consumer service.

- **Incremental cursor: `ledger_entries.created_at`.** Ledger rows are
  append-only and only created on SETTLED transitions — `created_at` is
  effectively `settled_at`. No new column needed.

- **BQ not live in Phase 8.** Use mock BQ client + `@pytest.mark.skipif` guards,
  same pattern as Airflow E2E tests. Document `gcloud` bootstrap steps for
  Phase 11.

- **Analytics-only.** BigQuery in Phase 8 is for mart aggregations only — not
  an offline feature store. Point-in-time correct feature snapshots are Phase
  10's concern (Feature Replay Engine). Phase 9 (Dashboard) uses BQ marts for
  monitoring, not ML training data.

---

## Area 2: Local Dev BigQuery Strategy

### Decisions

- **Two dbt profiles — `dev` (dbt-postgres) and `prod` (dbt-bigquery).**
  `dev` profile hits existing docker-compose PostgreSQL. `prod` profile hits
  BigQuery (Phase 11). Single-adapter mock diverges from real BQ SQL dialect
  over time; two profiles give real query execution locally with zero Phase 11
  rework.

- **`assert_ledger_balanced` runs against PostgreSQL in local dev.** The test
  validates business logic (debits = credits), not BQ infrastructure. Running
  against existing docker-compose `ledger_entries` data gives real assertions
  on real rows. BQ-only means a dead test until Phase 11.

- **GCP project: placeholder env var.** No GCP project exists yet. Use
  `GCP_PROJECT_ID=your-gcp-project-id` in `.env.example` with a
  `# TODO: set in Phase 11` comment. GCP free tier ($300 credit) covers the
  full project; setup deferred to Phase 11.

- **Full dbt scaffold.** `models/`, `tests/`, `macros/`, `seeds/`,
  `profiles.yml` — standard dbt project shape from the start. No minimal
  structure that forces a mid-phase refactor.

---

## Area 3: dbt Sources and Grain

### Decisions

- **`stg_transactions` source:** `payment_state_log` (latest state per
  transaction via `DISTINCT ON (transaction_id) ORDER BY created_at DESC`)
  JOINed to `ledger_entries` DEBIT row for `amount_cents`. Rationale:
  `payment_state_log` has no `amount_cents` column — amount lives only in
  `ledger_entries`. No new PostgreSQL summary table — normalization is dbt's
  job.

- **`dim_merchants` / `dim_users`: stub dimensions (ID only).** Deriving from
  `payment_state_log` distinct merchant_ids is sparse with no attributes
  (option A = option C semantically). Seed files with fake data are dishonest.
  Stub dimensions are expandable in Phase 9 when the dashboard needs real
  attributes.

- **`fact_ledger_balanced`: fact table, one row per transaction.** Columns:
  `transaction_id`, `debit_amount_cents`, `credit_amount_cents`,
  `merchant_id`, `currency`, `settled_at`. The `assert_ledger_balanced` custom
  test is a *separate* dbt singular test that queries this table and returns
  rows on failure. The fact table is the asset; the test is the guard on it.

- **`reconciliation_summary` source: new `reconciliation_discrepancies`
  PostgreSQL table.** The current Airflow DAG computes discrepancy records in
  memory and publishes to Kafka only — no PostgreSQL persistence. Phase 8 adds
  a `persist_discrepancies` Airflow task to the existing DAG + Alembic
  migration 004 for the new table. This becomes the dbt source for the mart.

### dbt Model Build Order (CLAUDE.md locked)

```
Staging:    stg_transactions, stg_ledger_entries
Dimensions: dim_merchants, dim_users
Facts:      fact_transactions, fact_ledger_balanced
Marts:      reconciliation_summary, fraud_metrics, hourly_payment_volume,
            merchant_performance
Custom test: assert_ledger_balanced (singular test on fact_ledger_balanced)
```

### Source Tables (PostgreSQL, local dev)

| dbt source | PostgreSQL table(s) |
|---|---|
| stg_transactions | payment_state_log + ledger_entries (DEBIT JOIN) |
| stg_ledger_entries | ledger_entries |
| dim_merchants | ledger_entries DISTINCT merchant_id |
| dim_users | payment_state_log DISTINCT (stub) |
| fact_transactions | stg_transactions |
| fact_ledger_balanced | stg_ledger_entries grouped by transaction_id |
| reconciliation_summary | reconciliation_discrepancies (new, migration 004) |
| fraud_metrics | fact_transactions + payment_state_log |
| hourly_payment_volume | fact_transactions |
| merchant_performance | fact_transactions + fact_ledger_balanced |

---

## Area 4: Test Strategy

### Decisions

- **dbt model correctness: `dbt test` against docker-compose PostgreSQL.**
  Python unit tests that mock SQL results test the mock, not the SQL. The
  `dbt-postgres` dev profile from Area 2 makes this possible without any new
  infra.

- **`assert_ledger_balanced` failure path: dbt seed with known-bad row.**
  One seed file with a single imbalanced transaction (debit ≠ credit) exercises
  the guard. A test that only runs on clean data is a tautology — the broken-SQL
  case would silently pass forever without the bad seed.

- **Test count reporting: two separate counts.**
  `174 pytest tests + N dbt tests`. dbt-generated schema contract tests
  (`not_null`, `unique`, `accepted_values`, `relationships`) are not behavioral
  tests. Mixing them inflates the metric. Two separate counts is accurate and
  more useful in a portfolio context.

- **`persist_discrepancies` unit tests: mock `create_engine`, assert Core
  `insert()`.** Uses SQLAlchemy Core `insert()` + `engine.begin()` — same
  append-only pattern as all prior tables (ledger_entries, manual_review_queue,
  payment_state_log). Existing `patch("nightly_reconciliation.create_engine")`
  mock transfers directly. No new mock setup.

---

## Code Context (Reusable Assets)

### Existing PostgreSQL tables (docker-compose)

| Table | Key columns | Phase written |
|---|---|---|
| payment_state_log | transaction_id, from_state, to_state, event_id, merchant_id, created_at | Phase 3 |
| ledger_entries | transaction_id, amount_cents, entry_type (DEBIT/CREDIT), merchant_id, currency, source_event_id, created_at | Phase 6 |
| manual_review_queue | transaction_id, risk_score, payload (JSONB), created_at, status | Phase 6 |

### Existing Airflow DAG tasks (extension point)

- `detect_duplicates` — queries `ledger_entries`, publishes DUPLICATE_LEDGER to Kafka
- `fetch_stripe_window` — fetches Stripe PaymentIntents for UTC day
- `compare_and_publish` — detects MISSING_INTERNALLY + AMOUNT_MISMATCH, publishes to Kafka

**Phase 8 adds:** `persist_discrepancies` task — receives discrepancy list via
XCom, writes to `reconciliation_discrepancies` table using `engine.begin()` +
Core `insert()`. Wired after `compare_and_publish` in the DAG.

### Alembic migration needed

- **Migration 004**: `reconciliation_discrepancies` table
  - Columns: id (BIGSERIAL PK), transaction_id (TEXT), discrepancy_type (TEXT),
    stripe_payment_intent_id (TEXT nullable), internal_amount_cents (BIGINT nullable),
    stripe_amount_cents (BIGINT nullable), diff_cents (BIGINT nullable),
    currency (TEXT, default 'USD'), merchant_id (TEXT), stripe_created_at
    (TIMESTAMPTZ nullable), run_date (DATE), created_at (TIMESTAMPTZ, NOW())
  - Append-only trigger (same pattern as migration 001/002/003)

### dbt profiles.yml shape

```yaml
payment_system:
  target: dev
  outputs:
    dev:
      type: postgres
      host: "{{ env_var('POSTGRES_HOST', 'localhost') }}"
      port: 5432
      dbname: payment_db
      user: payment
      password: payment
      schema: dbt_dev
      threads: 4
    prod:
      type: bigquery
      method: oauth
      project: "{{ env_var('GCP_PROJECT_ID') }}"
      dataset: payment_analytics
      threads: 4
      # TODO: Phase 11 — set GCP_PROJECT_ID and configure service account
```

### BigQuery ingestion (Phase 11 prep)

The Airflow DAG will gain a `export_to_bigquery` task in Phase 11 that reads
from `ledger_entries` and `payment_state_log` using `created_at` as the
incremental cursor and writes to BigQuery via `google-cloud-bigquery` Python
client. **Not built in Phase 8** — placeholder env vars only.

---

## Deferred Ideas (Captured, Not Acted On)

- **Offline feature store via BigQuery** — point-in-time correct feature
  snapshots for XGBoost retraining. Phase 10 concern, requires different
  partitioning than mart aggregations.
- **Near-real-time BQ ingestion via Kafka sink** — only worthwhile if sub-5-min
  BQ latency is needed. Ruled out for this project stage.
- **dim_merchants / dim_users with real attributes** — expand in Phase 9 when
  Streamlit dashboard needs merchant names, user tiers, etc.
