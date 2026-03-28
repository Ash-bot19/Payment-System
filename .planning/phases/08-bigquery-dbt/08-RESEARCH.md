# Phase 8: BigQuery + dbt — Research

**Researched:** 2026-03-28
**Domain:** dbt 1.8 (dbt-postgres), Alembic migrations, Airflow TaskFlow extension, BigQuery prep
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**BigQuery Ingestion:**
- Nightly batch via Airflow (not near-real-time). dbt marts are aggregations — sub-minute BQ freshness has no analytical benefit.
- Ingestion path: PostgreSQL → BigQuery directly, via new Airflow tasks. No new Kafka sink consumer.
- Incremental cursor: `ledger_entries.created_at` (append-only, effectively `settled_at`). No new column.
- BQ NOT live in Phase 8. Use mock BQ client + `@pytest.mark.skipif` guards. Document `gcloud` bootstrap for Phase 11.
- BigQuery is analytics-only in Phase 8 (not offline feature store — that is Phase 10).

**Local Dev BigQuery Strategy:**
- Two dbt profiles: `dev` (dbt-postgres hitting docker-compose PostgreSQL) and `prod` (dbt-bigquery, Phase 11).
- `assert_ledger_balanced` runs against PostgreSQL in local dev — real assertions on real rows.
- GCP project: placeholder env var `GCP_PROJECT_ID=your-gcp-project-id` in `.env.example` with `# TODO: set in Phase 11`.
- Full dbt scaffold from the start: `models/`, `tests/`, `macros/`, `seeds/`, `profiles.yml`.

**dbt Sources and Grain:**
- `stg_transactions`: `payment_state_log` DISTINCT ON latest state per transaction_id JOINed to `ledger_entries` DEBIT for `amount_cents`.
- `dim_merchants` / `dim_users`: stub dimensions (ID only, no fake seed data).
- `fact_ledger_balanced`: fact table with one row per transaction. Columns: `transaction_id`, `debit_amount_cents`, `credit_amount_cents`, `merchant_id`, `currency`, `settled_at`.
- `assert_ledger_balanced`: separate dbt singular test (not part of `fact_ledger_balanced` model), queries the fact table, returns rows on failure.
- `reconciliation_summary` source: new `reconciliation_discrepancies` PostgreSQL table (Alembic migration 004 + new `persist_discrepancies` Airflow task).

**Test Strategy:**
- dbt model correctness via `dbt test` against docker-compose PostgreSQL.
- `assert_ledger_balanced` failure path: dbt seed with one imbalanced transaction row.
- Test count reporting: `174 pytest tests + N dbt tests` separately (do NOT mix counts).
- `persist_discrepancies` unit tests: mock `create_engine`, assert SQLAlchemy Core `insert()`.

### Claude's Discretion

None specified — all decisions are locked.

### Deferred Ideas (OUT OF SCOPE)

- Offline feature store via BigQuery (point-in-time correct feature snapshots for XGBoost retraining) — Phase 10.
- Near-real-time BQ ingestion via Kafka sink — ruled out for this project stage.
- `dim_merchants` / `dim_users` with real attributes — Phase 9 (when Streamlit dashboard needs them).
</user_constraints>

---

## Summary

Phase 8 builds the dbt transformation layer on top of the existing PostgreSQL data that Phases 3–7 have been writing. The dbt project runs against docker-compose PostgreSQL locally using the `dbt-postgres` adapter; no BigQuery infrastructure is needed until Phase 11. The primary work is: (1) scaffold a correct dbt 1.8 project structure, (2) write all eight models in the correct staging → dimensions → facts → marts order, (3) implement the `assert_ledger_balanced` singular test with a seed for the failure path, (4) add Alembic migration 004 for `reconciliation_discrepancies`, (5) extend the existing Airflow DAG with a `persist_discrepancies` TaskFlow task.

The key implementation concern is `stg_transactions`: the SQL must use `DISTINCT ON (transaction_id) ORDER BY created_at DESC` (PostgreSQL-specific) to get the latest state per transaction from `payment_state_log`, then LEFT JOIN to `ledger_entries` filtering `entry_type = 'DEBIT'` to get `amount_cents`. This is the only model with non-trivial SQL.

`dbt-core` and `dbt-postgres` are now decoupled as of dbt 1.8 — both must be installed explicitly. The `data_tests:` key replaces the deprecated `tests:` key in YAML (both work in 1.8 but `data_tests:` is the correct form). Singular tests are SQL files in `tests/` that return failing rows on assertion failure.

**Primary recommendation:** Install `dbt-core==1.8.9` + `dbt-postgres==1.8.2` (latest 1.8.x patch), use `data_tests:` in all YAML, materialize staging as `view`, dimensions/facts/marts as `table`.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| dbt-core | 1.8.9 | dbt CLI, model compilation, test runner | Latest stable 1.8.x; project constraint is "dbt 1.8+" |
| dbt-postgres | 1.8.2 | PostgreSQL adapter for dbt-core | Matches dbt-core 1.8.x series; decoupled since 1.8 |
| dbt-bigquery | 1.8.3 | BigQuery adapter (prod profile, Phase 11) | Matches 1.8.x series; placeholder only in Phase 8 |
| psycopg2-binary | 2.9.9 | PostgreSQL driver (already in requirements.txt) | Already installed in project |

**Version note:** Verified via `pip index versions` on 2026-03-28. Latest dbt-core is 1.9.x/1.10.x but this project pins to 1.8+ per CLAUDE.md. Use 1.8.9 (latest 1.8 patch) for stability.

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| google-cloud-bigquery | 3.x | BQ Python client (Phase 11 prep) | env var placeholders in Phase 8, real calls Phase 11 |
| pandas | 2.2.2 | DataFrame for BQ load_table_from_dataframe | Already installed; used in Phase 11 export task |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| dbt-postgres (dev) | Single dbt-bigquery with BQ emulator | BQ emulator adds Docker complexity, diverges from real BQ SQL edge cases |
| `dbt seed` for failure path | Python fixture inserting bad row | dbt seed is self-documenting and version-controlled; Python fixture couples test infra to dbt |

**Installation:**
```bash
pip install dbt-core==1.8.9 dbt-postgres==1.8.2
# Phase 11 only:
pip install dbt-bigquery==1.8.3
```

**Version verification:** Confirmed via `pip index versions dbt-core` and `pip index versions dbt-postgres` against PyPI on 2026-03-28.

---

## Architecture Patterns

### Recommended dbt Project Structure

```
payment-backend/dbt/
├── dbt_project.yml          # project config, materializations by folder
├── profiles.yml             # dev (postgres) + prod (bigquery) outputs
├── packages.yml             # dbt-utils if needed (optional)
├── models/
│   ├── sources.yml          # declares PostgreSQL tables as dbt sources
│   ├── staging/
│   │   ├── stg_transactions.sql
│   │   └── stg_ledger_entries.sql
│   ├── dimensions/
│   │   ├── dim_merchants.sql
│   │   └── dim_users.sql
│   ├── facts/
│   │   ├── fact_transactions.sql
│   │   └── fact_ledger_balanced.sql
│   └── marts/
│       ├── reconciliation_summary.sql
│       ├── fraud_metrics.sql
│       ├── hourly_payment_volume.sql
│       └── merchant_performance.sql
├── tests/
│   └── assert_ledger_balanced.sql   # singular test — returns rows on failure
├── seeds/
│   └── imbalanced_transaction.csv   # known-bad row for failure path test
├── macros/                          # empty initially
└── snapshots/                       # empty initially
```

### Pattern 1: dbt_project.yml — Required Fields + Materialization Config

```yaml
# Source: https://docs.getdbt.com/reference/dbt_project.yml
name: payment_system
config-version: 2
version: "1.0.0"
profile: payment_system

model-paths: ["models"]
seed-paths: ["seeds"]
test-paths: ["tests"]
macro-paths: ["macros"]
snapshot-paths: ["snapshots"]
clean-targets: ["target", "dbt_packages"]

models:
  payment_system:
    staging:
      +materialized: view
    dimensions:
      +materialized: table
    facts:
      +materialized: table
    marts:
      +materialized: table

seeds:
  payment_system:
    imbalanced_transaction:
      +column_types:
        transaction_id: text
        debit_amount_cents: bigint
        credit_amount_cents: bigint
```

**Key:** `config-version: 2` is required. `profile` must match the top-level key in `profiles.yml`. Folder-level `+materialized` is the canonical pattern — no per-model config needed.

### Pattern 2: profiles.yml — dev (postgres) + prod (bigquery)

Per CONTEXT.md locked decision. Note: profiles.yml is checked in to the dbt project directory (not `~/.dbt/`). Since dbt 1.3+, dbt checks the current working directory before `~/.dbt/`, so `payment-backend/dbt/profiles.yml` takes precedence when running `dbt` from `payment-backend/dbt/`.

```yaml
# Source: https://docs.getdbt.com/docs/core/connect-data-platform/profiles.yml
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
      timeout_seconds: 300
      # TODO: Phase 11 — set GCP_PROJECT_ID and configure service account
```

**Pitfall:** `method: oauth` requires `gcloud auth application-default login` for local prod runs. For CI/CD (Phase 11), use `method: service-account` with `keyfile` path. Not needed for Phase 8.

### Pattern 3: sources.yml — Declaring PostgreSQL Tables

```yaml
# Source: https://docs.getdbt.com/docs/build/sources
# File: payment-backend/dbt/models/sources.yml
version: 2

sources:
  - name: payment_db
    database: payment_db
    schema: public
    tables:
      - name: payment_state_log
        description: "Append-only state machine log. One row per transition."
        columns:
          - name: transaction_id
            data_tests:
              - not_null
          - name: to_state
            data_tests:
              - not_null
      - name: ledger_entries
        description: "Double-entry ledger. Append-only, 1 DEBIT + 1 CREDIT per SETTLED tx."
        columns:
          - name: transaction_id
            data_tests:
              - not_null
          - name: entry_type
            data_tests:
              - not_null
              - accepted_values:
                  values: ['DEBIT', 'CREDIT']
      - name: reconciliation_discrepancies
        description: "Persisted discrepancies from nightly reconciliation DAG (migration 004)."
        columns:
          - name: id
            data_tests:
              - unique
              - not_null
```

**Use `source()` to reference these tables in staging models, `ref()` for all downstream models.**

### Pattern 4: stg_transactions — The Non-Trivial SQL

`payment_state_log` has no `amount_cents`. The CONTEXT.md decision: DISTINCT ON latest state per transaction, LEFT JOIN to DEBIT row for amount.

```sql
-- Source: payment-backend/dbt/models/staging/stg_transactions.sql
-- PostgreSQL DISTINCT ON: keeps the row with the latest created_at per transaction_id.
-- LEFT JOIN to ledger_entries (DEBIT only) provides amount_cents for SETTLED transactions.
-- Non-SETTLED transactions (AUTHORIZED, FLAGGED, etc.) will have amount_cents = NULL.

with latest_state as (
    select distinct on (transaction_id)
        transaction_id,
        merchant_id,
        to_state        as current_state,
        from_state      as previous_state,
        event_id,
        created_at      as state_updated_at
    from {{ source('payment_db', 'payment_state_log') }}
    order by transaction_id, created_at desc
),

debit_amounts as (
    select
        transaction_id,
        amount_cents,
        currency,
        created_at as settled_at
    from {{ source('payment_db', 'ledger_entries') }}
    where entry_type = 'DEBIT'
)

select
    ls.transaction_id,
    ls.merchant_id,
    ls.current_state,
    ls.previous_state,
    ls.event_id,
    ls.state_updated_at,
    da.amount_cents,
    da.currency,
    da.settled_at
from latest_state ls
left join debit_amounts da
    on ls.transaction_id = da.transaction_id
```

**Critical:** `DISTINCT ON` is PostgreSQL-specific syntax. It will not compile against BigQuery (prod profile). For Phase 11, this staging model will need a BigQuery-compatible equivalent using `ROW_NUMBER() OVER (PARTITION BY transaction_id ORDER BY created_at DESC) = 1`. Use a macro or `{{ adapter.type() }}` guard when Phase 11 arrives. For Phase 8 (dev profile only), `DISTINCT ON` is correct.

### Pattern 5: assert_ledger_balanced — Singular Test SQL

Singular tests live in `tests/` and return failing rows. dbt runs them with `dbt test`.

```sql
-- Source: https://docs.getdbt.com/docs/build/data-tests
-- File: payment-backend/dbt/tests/assert_ledger_balanced.sql
-- Returns rows where debit_amount_cents != credit_amount_cents.
-- Returns ZERO rows if all transactions are balanced (test passes).
-- Returns failing rows if any transaction is imbalanced (test fails).

select
    transaction_id,
    debit_amount_cents,
    credit_amount_cents,
    merchant_id
from {{ ref('fact_ledger_balanced') }}
where debit_amount_cents != credit_amount_cents
```

**No semicolon at end** — dbt wraps the query; a trailing semicolon causes a parse error. Uses `ref()` not `source()` because it tests a dbt-built model, not a raw source table.

### Pattern 6: fact_ledger_balanced — Source SQL

```sql
-- File: payment-backend/dbt/models/facts/fact_ledger_balanced.sql
-- One row per transaction_id: debit and credit amounts side by side.
-- For a balanced ledger, debit_amount_cents = credit_amount_cents always.

select
    transaction_id,
    merchant_id,
    currency,
    max(case when entry_type = 'DEBIT' then amount_cents else null end)  as debit_amount_cents,
    max(case when entry_type = 'CREDIT' then amount_cents else null end) as credit_amount_cents,
    min(created_at) as settled_at
from {{ ref('stg_ledger_entries') }}
group by transaction_id, merchant_id, currency
```

### Pattern 7: Seeds for the Failure Path

```csv
-- File: payment-backend/dbt/seeds/imbalanced_transaction.csv
transaction_id,debit_amount_cents,credit_amount_cents
imbalanced_test_001,10000,9999
```

This seed is loaded with `dbt seed` into the `dbt_dev` schema as a table. The singular test `assert_ledger_balanced` can then be run with `--select` targeting a model that reads from this seed directly (or the seed values inserted via a fixture into `ledger_entries` to exercise the real model). See "Common Pitfalls" for the seed isolation approach.

**Seed configuration in dbt_project.yml (column_types must be explicit for bigint):**
```yaml
seeds:
  payment_system:
    imbalanced_transaction:
      +column_types:
        transaction_id: text
        debit_amount_cents: bigint
        credit_amount_cents: bigint
```

### Pattern 8: Airflow persist_discrepancies Task

`compare_and_publish` currently returns `None`. To wire `persist_discrepancies` downstream, change the return type to `list[dict]` and add the new task.

```python
# Source: existing nightly_reconciliation.py pattern + Airflow TaskFlow docs
# https://airflow.apache.org/docs/apache-airflow/stable/tutorial/taskflow.html

@task()
def compare_and_publish(
    stripe_intents: dict[str, Any], ds: str | None = None
) -> list[dict[str, Any]]:
    """...existing docstring...

    Returns:
        List of discrepancy dicts (model_dump output) for persist_discrepancies XCom.
    """
    # ... existing detection logic unchanged ...

    if messages:
        producer = ReconciliationProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
        producer.publish_batch(messages)

    logger.info("reconciliation_comparison_complete", ...)
    return messages  # <-- return list instead of None


@task()
def persist_discrepancies(discrepancies: list[dict[str, Any]], ds: str | None = None) -> int:
    """Persist reconciliation discrepancies to reconciliation_discrepancies table.

    Receives discrepancy list from compare_and_publish via XCom.
    Writes rows using SQLAlchemy Core insert() (append-only pattern).
    No-ops cleanly if discrepancies list is empty.

    Args:
        discrepancies: List of ReconciliationMessage .model_dump() dicts.
        ds: Airflow execution date string.

    Returns:
        Count of rows inserted (for XCom / monitoring).
    """
    if not discrepancies:
        logger.info("persist_discrepancies_skipped", reason="empty_list", run_date=ds)
        return 0

    engine = create_engine(DATABASE_URL_SYNC)

    from sqlalchemy import insert, Table, MetaData
    metadata = MetaData()
    table = Table("reconciliation_discrepancies", metadata, autoload_with=engine)

    with engine.begin() as conn:
        conn.execute(insert(table), discrepancies)

    logger.info("persist_discrepancies_written", count=len(discrepancies), run_date=ds)
    return len(discrepancies)


@dag(...)
def nightly_reconciliation() -> None:
    """Updated DAG wiring — persist_discrepancies depends on compare_and_publish."""
    dupes = detect_duplicates()                    # noqa: F841
    stripe_data = fetch_stripe_window()
    discrepancy_list = compare_and_publish(stripe_data)
    persist_discrepancies(discrepancy_list)         # TaskFlow auto-wires XCom
```

**XCom size constraint:** Discrepancy records are small dicts (~15 fields each). For a nightly run processing hundreds of transactions, the list will be well under the 1MB XCom limit. No staging file needed.

### Pattern 9: Alembic Migration 004

Follows the exact same pattern as migrations 001–003: append-only trigger, no update/delete.

```python
# db/migrations/versions/004_create_reconciliation_discrepancies.py
# revision: "004", down_revision: "003"

op.create_table(
    "reconciliation_discrepancies",
    sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column("transaction_id", sa.Text(), nullable=False),
    sa.Column("discrepancy_type", sa.Text(), nullable=False),
    sa.Column("stripe_payment_intent_id", sa.Text(), nullable=True),
    sa.Column("internal_amount_cents", sa.BigInteger(), nullable=True),
    sa.Column("stripe_amount_cents", sa.BigInteger(), nullable=True),
    sa.Column("diff_cents", sa.BigInteger(), nullable=True),
    sa.Column("currency", sa.Text(), nullable=False, server_default=sa.text("'USD'")),
    sa.Column("merchant_id", sa.Text(), nullable=False),
    sa.Column("stripe_created_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("run_date", sa.Date(), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True),
              server_default=sa.text("NOW()"), nullable=False),
    sa.PrimaryKeyConstraint("id"),
)
# Indexes on transaction_id, run_date, discrepancy_type
# Append-only trigger: prevent_discrepancies_mutation() — same pattern as 001–003
```

### Pattern 10: BigQuery Phase 11 Placeholder Pattern

```python
# In nightly_reconciliation.py — export_to_bigquery task (Phase 11 prep)
# NOT implemented in Phase 8 — documented here for planning

import os

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")  # TODO: set in Phase 11

@task()
def export_to_bigquery(last_cursor: str | None = None) -> None:
    """Export new ledger rows to BigQuery using created_at as incremental cursor.

    PHASE 11 IMPLEMENTATION — skip if GCP_PROJECT_ID not set.

    Pattern:
        from google.cloud import bigquery
        import pandas as pd

        engine = create_engine(DATABASE_URL_SYNC)
        df = pd.read_sql(
            "SELECT * FROM ledger_entries WHERE created_at > %(cursor)s",
            engine,
            params={"cursor": last_cursor or "1970-01-01"},
        )

        client = bigquery.Client(project=GCP_PROJECT_ID)
        table_id = f"{GCP_PROJECT_ID}.payment_analytics.ledger_entries"
        job = client.load_table_from_dataframe(df, table_id,
              job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"))
        job.result()  # wait for completion
    """
    if not GCP_PROJECT_ID:
        logger.info("export_to_bigquery_skipped", reason="GCP_PROJECT_ID_not_set")
        return
```

### Anti-Patterns to Avoid

- **Referencing source tables with `ref()` in staging models:** Always use `source('payment_db', 'table_name')` for raw PostgreSQL tables. `ref()` is only for dbt-built models.
- **Using `tests:` key in YAML:** dbt 1.8 renamed this to `data_tests:`. The old key still works but emits deprecation warnings. Use `data_tests:` throughout.
- **Semicolons in singular test SQL files:** Causes parse errors. Omit completely.
- **Running `dbt` from the repo root:** Run from `payment-backend/dbt/` so that the local `profiles.yml` takes precedence over `~/.dbt/profiles.yml`.
- **Materialized staging models as `table`:** Use `view`. Staging models are building blocks; they should stay in sync with source data without a full refresh.
- **Mixing pytest count and dbt test count:** Report separately as `174 pytest tests` and `N dbt tests`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Schema contract tests on columns | Custom Python assertions | `data_tests: [unique, not_null, accepted_values]` in sources.yml / models.yml | dbt generates and runs these against live data automatically |
| "Latest state per transaction" deduplication | Python ETL loop | `DISTINCT ON` in `stg_transactions.sql` | Single SQL expression; PostgreSQL executes in one pass with index on (transaction_id, created_at) |
| BigQuery export batching | Custom chunking loop | `client.load_table_from_dataframe()` with WRITE_APPEND | BQ client handles batching internally; DataFrame → BQ is the idiomatic Phase 11 pattern |
| dbt test result parsing | Custom Python parser | `dbt test --select assert_ledger_balanced` exit code | Exit code 1 = failure, 0 = pass; no parsing needed |
| Incremental cursor tracking | Custom watermark table | `ledger_entries.created_at` as cursor (append-only, reliable) | No extra table; `created_at` is set by PostgreSQL `NOW()` at insert time |

**Key insight:** dbt's schema contract tests (`not_null`, `unique`, `accepted_values`, `relationships`) run as SQL queries against your data — they are far more reliable than mocked Python assertions because they test the actual persisted rows.

---

## Common Pitfalls

### Pitfall 1: DISTINCT ON Is PostgreSQL-Only
**What goes wrong:** `stg_transactions.sql` uses `DISTINCT ON (transaction_id)` — this is PostgreSQL syntax. If someone runs `dbt compile` targeting the `prod` (BigQuery) profile, it will fail at query compilation time.
**Why it happens:** PostgreSQL-specific syntax not supported by BigQuery standard SQL.
**How to avoid:** Keep `dbt run` / `dbt test` against `dev` profile only in Phase 8. When Phase 11 arrives, replace with `ROW_NUMBER() OVER (PARTITION BY transaction_id ORDER BY created_at DESC)` wrapped in a `WHERE rn = 1` CTE. Document this in a comment in the model file.
**Warning signs:** `dbt compile --target prod` fails with syntax error on `DISTINCT ON`.

### Pitfall 2: dbt Seed Schema Not Isolated from Fact Table
**What goes wrong:** The `imbalanced_transaction` seed loaded into `dbt_dev` schema creates a standalone table. `assert_ledger_balanced` queries `fact_ledger_balanced` — which is built from `stg_ledger_entries` which reads `ledger_entries`. The seed data does NOT flow through the model unless explicitly wired.
**Why it happens:** Seeds are independent tables, not automatically joined into model lineage.
**How to avoid:** Two valid approaches:
  1. Insert the imbalanced seed row directly into `ledger_entries` via a pytest integration test fixture, let `dbt run` build `fact_ledger_balanced` from it, then run `dbt test`. The seed file documents intent; the pytest fixture provides the actual data.
  2. Create a separate `fact_ledger_balanced_test_helper.sql` model that reads from `{{ ref('imbalanced_transaction') }}` and run `assert_ledger_balanced` against that model in a test-specific run.
  Per CONTEXT.md decision: "dbt seed with known-bad imbalanced row" — approach 1 is cleaner for this codebase.
**Warning signs:** `dbt test --select assert_ledger_balanced` always passes even with the seed loaded — means the seed data is not reaching `fact_ledger_balanced`.

### Pitfall 3: compare_and_publish Return Type Change Breaks Unit Tests
**What goes wrong:** Existing unit tests for `compare_and_publish` mock it and expect `None` return. Changing return type to `list[dict]` will cause tests that check `result is None` to fail.
**Why it happens:** 19 unit tests in `test_nightly_reconciliation.py` were written when `compare_and_publish` returned `None`.
**How to avoid:** When updating `compare_and_publish`, audit all test assertions. Replace `assert result is None` with `assert isinstance(result, list)`. Add a new test `test_compare_and_publish_returns_discrepancy_list`.
**Warning signs:** Existing unit test failures on `compare_and_publish` after return type change.

### Pitfall 4: dbt profiles.yml Checked into Git with Plaintext Credentials
**What goes wrong:** `profiles.yml` in `payment-backend/dbt/` contains `password: payment`. For the dev profile this is fine (local docker-compose test credentials). But prod profile must never have real GCP credentials in git.
**Why it happens:** Convenience of collocating profiles.yml with dbt project.
**How to avoid:** Use `env_var()` for all credentials: `password: "{{ env_var('POSTGRES_PASSWORD', 'payment') }}"`. For prod BigQuery, `method: oauth` uses ambient GCP auth (no password in file). Add `profiles.yml` to `.gitignore` if it will eventually contain real keys — or keep it in git with env_var() references only.
**Warning signs:** Literal secret value visible in `git diff` for profiles.yml.

### Pitfall 5: SQLAlchemy Table autoload_with Requires Engine Connection
**What goes wrong:** `Table("reconciliation_discrepancies", metadata, autoload_with=engine)` in `persist_discrepancies` will fail if the engine cannot connect to PostgreSQL at DAG import time (before the Airflow task runs).
**Why it happens:** `autoload_with` triggers an immediate `DESCRIBE` query when the `Table()` object is constructed.
**How to avoid:** Move `Table(...)` construction inside `engine.begin()` context, OR define the table schema explicitly in code without `autoload_with`. Explicit definition is more robust:
```python
from sqlalchemy import Table, Column, Text, BigInteger, Date, TIMESTAMP, MetaData
metadata = MetaData()
recon_table = Table(
    "reconciliation_discrepancies", metadata,
    Column("transaction_id", Text),
    Column("discrepancy_type", Text),
    # ... other columns minus id (BIGSERIAL, auto-assigned) and created_at (server default)
)
```
**Warning signs:** `OperationalError: could not connect` during Airflow DAG parsing, not during task execution.

### Pitfall 6: dbt test-paths vs tests Directory
**What goes wrong:** dbt looks for singular tests in `test-paths` (default: `["tests"]` relative to the project root). If the tests directory is missing or misspelled, `dbt test` silently finds no singular tests.
**Why it happens:** `test-paths` in `dbt_project.yml` must match the actual directory name.
**How to avoid:** Verify with `dbt ls --select test_type:singular` — it should list `assert_ledger_balanced`. If the list is empty, check `test-paths` in `dbt_project.yml`.
**Warning signs:** `dbt test` exits 0 but no singular tests appear in the output.

### Pitfall 7: Airflow Decorator Stub Must Be Updated for persist_discrepancies
**What goes wrong:** `test_nightly_reconciliation.py` uses a custom Airflow stub (`_stub_airflow_decorators`) that intercepts `@task` so functions can be called directly in tests. Adding `persist_discrepancies` as a new `@task` requires no stub changes, but the new task will be intercepted by the stub when called from inside the `@dag` body (returns MagicMock, not the real function).
**Why it happens:** The stub is designed to return MagicMock for any task called inside `@dag` context — this is intentional and correct behavior.
**How to avoid:** Unit test `persist_discrepancies` by calling it directly (outside `@dag` context), with `create_engine` mocked. The stub will transparently pass through direct calls. No stub changes needed.
**Warning signs:** Unit test for `persist_discrepancies` returns MagicMock — means it was called inside a `_InDagContext.active=True` block.

---

## Code Examples

### Verified Pattern: sources.yml data_tests Key (dbt 1.8)

```yaml
# Source: https://docs.getdbt.com/reference/resource-properties/data-tests
# Use data_tests: (underscore) NOT tests: (deprecated in 1.8)
# Use data-tests: (hyphen) is WRONG and silently ignored

columns:
  - name: transaction_id
    data_tests:
      - not_null
      - unique
  - name: entry_type
    data_tests:
      - accepted_values:
          values: ['DEBIT', 'CREDIT']
```

### Verified Pattern: Running dbt from a Non-Default profiles.yml Location

```bash
# Run from the dbt project directory (profiles.yml found in CWD since dbt 1.3):
cd payment-backend/dbt
dbt run --target dev
dbt test

# Or use --profiles-dir flag from any directory:
dbt run --profiles-dir payment-backend/dbt --project-dir payment-backend/dbt --target dev
```

### Verified Pattern: SQLAlchemy Core Insert (append-only pattern from existing codebase)

```python
# Source: existing pattern from payment-backend/kafka/producers/*.py
# Uses engine.begin() for implicit COMMIT; insert() for explicit append-only semantics

from sqlalchemy import insert, Table, Column, Text, BigInteger, Date, TIMESTAMP, MetaData

def _build_recon_table() -> Table:
    """Return Table definition for reconciliation_discrepancies (no autoload)."""
    metadata = MetaData()
    return Table(
        "reconciliation_discrepancies",
        metadata,
        Column("transaction_id", Text),
        Column("discrepancy_type", Text),
        Column("stripe_payment_intent_id", Text),
        Column("internal_amount_cents", BigInteger),
        Column("stripe_amount_cents", BigInteger),
        Column("diff_cents", BigInteger),
        Column("currency", Text),
        Column("merchant_id", Text),
        Column("stripe_created_at", TIMESTAMP(timezone=True)),
        Column("run_date", Date),
        # id and created_at have server defaults — omit from insert
    )

@task()
def persist_discrepancies(
    discrepancies: list[dict[str, Any]], ds: str | None = None
) -> int:
    if not discrepancies:
        return 0
    engine = create_engine(DATABASE_URL_SYNC)
    table = _build_recon_table()
    with engine.begin() as conn:
        conn.execute(insert(table), discrepancies)
    logger.info("persist_discrepancies_written", count=len(discrepancies), run_date=ds)
    return len(discrepancies)
```

### Verified Pattern: Mocking persist_discrepancies in Unit Tests

```python
# Following existing test_nightly_reconciliation.py pattern
@patch("nightly_reconciliation.create_engine")
def test_persist_discrepancies_inserts_rows(mock_create_engine):
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.begin.return_value.__exit__ = MagicMock(return_value=False)
    mock_create_engine.return_value = mock_engine

    discrepancies = [
        {"transaction_id": "tx_001", "discrepancy_type": "AMOUNT_MISMATCH",
         "merchant_id": "merch_1", "run_date": "2026-03-27", ...},
    ]

    result = persist_discrepancies(discrepancies, ds="2026-03-27")

    assert result == 1
    mock_conn.execute.assert_called_once()
    call_args = mock_conn.execute.call_args
    # Verify insert() was used (not text() SQL)
    assert hasattr(call_args[0][0], "is_dml")  # SQLAlchemy Insert object
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `tests:` key in YAML | `data_tests:` key | dbt 1.8 | Old key still works but deprecated; use `data_tests:` |
| Installing adapter auto-installs dbt-core | `pip install dbt-core dbt-postgres` (both explicit) | dbt 1.8 | Must pin both packages separately |
| `tests/` directory for singular tests only | `tests/` for singular tests; unit tests in `models/` YAML | dbt 1.8 | `dbt test --select test_type:data` to run only data tests |
| `load_table_from_json()` | `load_table_from_dataframe()` | google-cloud-bigquery 3.x | DataFrame → BQ is the standard 2025 pattern for batch loads |

**Deprecated/outdated:**
- `tests:` YAML key: deprecated in dbt 1.8, will be removed in a future version. Use `data_tests:`.
- Installing `dbt-postgres` alone expecting `dbt-core` to come with it: no longer works since dbt 1.8 decoupling.

---

## Open Questions

1. **dbt seed isolation for assert_ledger_balanced failure path**
   - What we know: The `imbalanced_transaction.csv` seed loads into `dbt_dev` schema as a standalone table. `assert_ledger_balanced` queries `fact_ledger_balanced` which reads real `ledger_entries`.
   - What's unclear: The exact mechanism to exercise the failure path without polluting the real `ledger_entries` table with imbalanced data (which would violate the DB balance trigger).
   - Recommendation: Use a pytest integration test that temporarily disables the balance trigger (or uses a separate test schema), inserts an imbalanced pair, runs `dbt test`, confirms failure. Alternatively, create `fact_ledger_balanced_imbalanced.sql` as a test-only model that reads from the seed and use `assert_ledger_balanced_seed.sql` targeting that model. The planner should decide — document both options in the plan.

2. **dbt schema isolation between test runs**
   - What we know: dev profile uses `schema: dbt_dev`. All dbt objects land in the `dbt_dev` schema of `payment_db`. Multiple dev environments (e.g., CI) would collide.
   - What's unclear: Whether `generate_schema_name` macro is needed for CI isolation.
   - Recommendation: For Phase 8 (local dev only), `dbt_dev` is sufficient. Note in plan that CI will need `DBT_TARGET_SCHEMA` env var + custom macro in Phase 10 CI/CD.

3. **persist_discrepancies XCom type for compare_and_publish return**
   - What we know: XCom serializes return values as JSON. `list[dict]` with date/datetime fields needs `model_dump(mode="json")` which the existing code already does.
   - What's unclear: Whether the existing `messages` list in `compare_and_publish` uses fully JSON-serializable types throughout (date fields are already converted via `model_dump(mode="json")`).
   - Recommendation: Verify that `ReconciliationMessage.model_dump(mode="json")` converts `run_date: date` to ISO string. It does — Pydantic v2 `mode="json"` serializes `date` as `"YYYY-MM-DD"` string. XCom will handle this correctly. No additional serialization needed.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.2.1 + dbt test (separate) |
| Config file | `payment-backend/pytest.ini` or `pyproject.toml` (existing) |
| Quick run command | `cd payment-backend && python -m pytest tests/unit/test_nightly_reconciliation.py -x` |
| Full suite command | `cd payment-backend && python -m pytest tests/ -v && cd dbt && dbt test --target dev` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | Notes |
|--------|----------|-----------|-------------------|-------|
| dbt-01 | dbt project compiles without errors | smoke | `dbt compile --target dev` | Run from `payment-backend/dbt/` |
| dbt-02 | All 8 dbt models build successfully | integration | `dbt run --target dev` | Requires docker-compose postgres |
| dbt-03 | Source schema contract tests pass | integration | `dbt test --target dev --select test_type:data` | Requires postgres with data |
| dbt-04 | `assert_ledger_balanced` returns 0 rows on balanced data | integration | `dbt test --select assert_ledger_balanced` | Requires balanced ledger rows in postgres |
| dbt-05 | `assert_ledger_balanced` returns rows on imbalanced data (failure path) | integration | pytest integration test inserts imbalanced data, `dbt run + test` | See Open Questions #1 |
| persist-01 | `persist_discrepancies` writes rows to reconciliation_discrepancies | unit | `python -m pytest tests/unit/test_nightly_reconciliation.py -k persist` | Mocked engine |
| persist-02 | `persist_discrepancies` no-ops on empty list | unit | same as above | |
| persist-03 | `compare_and_publish` returns list not None | unit | `python -m pytest tests/unit/test_nightly_reconciliation.py -k compare` | Updated existing tests |
| migration-04 | Migration 004 applies cleanly | integration | `alembic upgrade head` then `SELECT * FROM reconciliation_discrepancies LIMIT 1` | Requires postgres |

### Sampling Rate

- **Per task commit:** `python -m pytest tests/unit/test_nightly_reconciliation.py -x`
- **Per wave merge:** Full pytest suite + `dbt compile --target dev`
- **Phase gate:** Full pytest suite + `dbt run --target dev` + `dbt test --target dev` all green

### Wave 0 Gaps

- [ ] `payment-backend/dbt/dbt_project.yml` — does not exist yet (dbt scaffold)
- [ ] `payment-backend/dbt/profiles.yml` — does not exist yet
- [ ] `payment-backend/dbt/models/sources.yml` — does not exist yet
- [ ] All 8 model SQL files — do not exist yet
- [ ] `payment-backend/dbt/tests/assert_ledger_balanced.sql` — does not exist yet
- [ ] `payment-backend/dbt/seeds/imbalanced_transaction.csv` — does not exist yet
- [ ] `payment-backend/db/migrations/versions/004_create_reconciliation_discrepancies.py` — does not exist yet
- [ ] Framework install: `pip install dbt-core==1.8.9 dbt-postgres==1.8.2` — not in requirements.txt

---

## Sources

### Primary (HIGH confidence)

- `pip index versions dbt-core` / `pip index versions dbt-postgres` — verified current versions 2026-03-28 against PyPI
- [dbt_project.yml reference](https://docs.getdbt.com/reference/dbt_project.yml) — required fields, materialization config structure
- [dbt data tests](https://docs.getdbt.com/docs/build/data-tests) — singular test syntax, no-semicolon rule, ref() usage
- [dbt sources](https://docs.getdbt.com/docs/build/sources) — sources.yml YAML format, source() vs ref()
- [dbt seeds](https://docs.getdbt.com/docs/build/seeds) — CSV format, column_types config, ref() in tests
- [dbt materialization best practices](https://docs.getdbt.com/best-practices/materializations/5-best-practices) — view for staging, table for marts
- [dbt upgrading to v1.8](https://docs.getdbt.com/docs/dbt-versions/core-upgrade/upgrading-to-v1.8) — data_tests rename, adapter decoupling
- [dbt profiles.yml](https://docs.getdbt.com/docs/core/connect-data-platform/profiles.yml) — location precedence (CWD first since 1.3)
- Existing codebase: `payment-backend/airflow/dags/nightly_reconciliation.py` — TaskFlow pattern, XCom, SQLAlchemy Core usage
- Existing codebase: `payment-backend/tests/integration/test_phase7_integration.py` — Airflow stub pattern, _insert_ledger_pair pattern
- Existing codebase: `payment-backend/db/migrations/versions/001-003` — Alembic append-only trigger pattern

### Secondary (MEDIUM confidence)

- [dbt profiles location](https://www.restack.io/docs/dbt-core-knowledge-dbt-core-profile-location) — confirms CWD-first behavior since dbt 1.3
- [google-cloud-bigquery load_table_from_dataframe](https://docs.cloud.google.com/python/docs/reference/bigquery/latest) — Phase 11 export pattern
- WebSearch: dbt 1.8 data_tests rename — confirmed by multiple community sources, official deprecation docs

### Tertiary (LOW confidence)

None — all critical findings are HIGH or MEDIUM confidence.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — versions verified via PyPI registry on 2026-03-28
- Architecture: HIGH — verified against official dbt docs, aligned with CONTEXT.md locked decisions
- Pitfalls: HIGH (pitfalls 1, 3, 4, 6, 7) / MEDIUM (pitfalls 2, 5) — based on codebase reading + official docs

**Research date:** 2026-03-28
**Valid until:** 2026-06-28 (dbt 1.8.x is stable; adapter decoupling pattern is not changing)
