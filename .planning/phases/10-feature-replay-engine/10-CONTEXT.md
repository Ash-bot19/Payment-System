# Phase 10: Feature Replay Engine - Context

**Gathered:** 2026-03-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Produce a training-ready 8-feature Parquet dataset by reconstructing ML features from historical PostgreSQL data. The primary deliverable is `data/feature_store/features_YYYYMMDD.parquet` — a dated file with the exact feature schema that `ml/train.py` expects. This phase does NOT retrain the model, warm up Redis, or write to BigQuery. It goes from an empty `replay/` directory to a working feature reconstruction script.

</domain>

<decisions>
## Implementation Decisions

### Replay Mechanism
- **D-01:** Pure Python batch script — `replay/feature_reconstruction.py`. No Kafka, no Spark, no JVM.
- **D-02:** Calls `spark/feature_functions.py` functions directly (they are pure Python, not Spark-dependent). Existing `compute_hour_of_day`, `compute_weekend_flag`, `compute_amount_cents_log`, `update_merchant_stats_and_zscore` are all reusable.
- **D-03:** Reads from PostgreSQL via psycopg2 (same pattern as `scripts/seed_demo_data.py`). Uses `payment_state_log` + `ledger_entries` for historical event data.
- **D-04:** Honest naming — the README/docstring must describe this as "feature reconstruction" not "event replay." The raw Stripe event payloads are not persisted; we're reconstructing features from state transitions and ledger data, not replaying events.
- **D-05:** `--date-range` CLI argument (`--start-date`, `--end-date`, both optional, default to all history). Makes the script schedulable for incremental backfills. Use `argparse`.

### Feature Fidelity
- **D-06:** Reconstruct deterministically where possible:
  - `hour_of_day` — from `payment_state_log.created_at` (INITIATED row) via `compute_hour_of_day()`
  - `weekend_flag` — same timestamp, via `compute_weekend_flag()`
  - `amount_cents_log` — from `ledger_entries.amount_cents` (DEBIT row) via `compute_amount_cents_log()`
  - `merchant_risk_score` — read from Redis `merchant:risk:{merchant_id}` if available; fall back to 0.5 default (same as live pipeline fallback)
- **D-07:** Approximate with SQL window functions:
  - `tx_velocity_1m` — `COUNT(*) OVER (PARTITION BY merchant_id ORDER BY created_at RANGE INTERVAL '1 minute' PRECEDING)`
  - `tx_velocity_5m` — same with `INTERVAL '5 minutes' PRECEDING`
  - Both over `payment_state_log` rows at INITIATED state, ordered by timestamp
- **D-08:** Approximate amount_zscore statically:
  - Compute `mean` and `std` over the full historical batch of `amount_cents` values for each merchant
  - `amount_zscore = (amount_cents - mean) / std` (use 0.0 if std = 0)
  - This is NOT the Welford streaming approximation — it's a batch retrospective approximation
- **D-09:** `device_switch_flag` — set to 0 for all historical rows. The device sequence is not stored in PostgreSQL; the flag is not reconstructible. Document this explicitly in the output Parquet schema (a `reconstruction_notes` column or docstring, not an extra column).
- **D-10:** All approximations must be documented in the script docstring under a section titled `"Feature Fidelity Notes"`. This is a deliberate portfolio signal — document the trade-off, not hide it.

### Output Target
- **D-11:** Write a dated Parquet file: `data/feature_store/features_YYYYMMDD.parquet` where the date is the run date (not the data date range).
- **D-12:** Use `pandas.DataFrame.to_parquet()` with `pyarrow` engine. Schema must match the 8-feature contract in CLAUDE.md exactly: `[tx_velocity_1m, tx_velocity_5m, amount_zscore, merchant_risk_score, device_switch_flag, hour_of_day, weekend_flag, amount_cents_log]`.
- **D-13:** Include metadata columns alongside features: `transaction_id`, `merchant_id`, `event_id`, `settled_at` (from ledger DEBIT row). These are not ML features — they're row identifiers. The planner should decide whether to keep them in the same file or write a separate metadata Parquet.
- **D-14:** No Redis warm-up. No BigQuery write. Risk of overwriting live Redis keys is unacceptable.
- **D-15:** BigQuery append to a `historical_features` table is a stretch goal only — implement only after D-11 through D-13 are complete and tested.

### ML Retrain Scope
- **D-16:** This phase does NOT retrain `ml/models/model.ubj`. The synthetic model remains in production.
- **D-17:** Add a comment block at the top of `ml/train.py` (in the module docstring) showing how to swap synthetic data for the Parquet output — something like: `# To train on real data: df = pd.read_parquet('data/feature_store/features_YYYYMMDD.parquet'); X = df[FEATURE_NAMES].values; y = df['label'].values`. This makes the retrain path discoverable without executing it.
- **D-18:** The Parquet file does NOT include a `label` column (we don't know ground truth for historical transactions). Document this gap — labeling strategy for supervised retraining is out of scope.

### Claude's Discretion
- SQL query structure (CTEs vs. subqueries) for reconstructing features
- Whether to use psycopg2 directly or SQLAlchemy Core (either is fine — match seed_demo_data.py pattern)
- Exact logging verbosity (structlog, as per CLAUDE.md)
- Test strategy (unit tests for feature reconstruction functions, integration test against live DB with --date-range)
- Whether `data/feature_store/` is git-ignored (it should be — Parquet files are generated artifacts)

</decisions>

<specifics>
## Specific Ideas

- "Name it honestly — feature reconstruction, not event replay. The raw payloads aren't there."
- "Document the offline approximation vs. streaming state trade-off explicitly. That documentation is a portfolio signal, not a weakness."
- "--date-range is 5 lines of argparse. Add it. Makes the script schedulable."
- "device_switch_flag = 0 for all historical rows. Don't fake it."
- The train.py swap comment is for interviewers — one comment that answers 'can you retrain with real data?' without building a full retrain pipeline.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### ML Feature Contract (LOCKED)
- `CLAUDE.md` §ML Risk Score Contract — defines the exact 8 input features, their names, and the output contract. This schema is the target for the Parquet file.

### Existing Feature Computation
- `payment-backend/spark/feature_functions.py` — pure Python functions for hour_of_day, weekend_flag, amount_cents_log, Welford z-score. All reusable in batch context.
- `payment-backend/spark/redis_sink.py` — shows Redis key patterns (merchant:risk:{merchant_id}, device_seen:{stripe_customer_id}, feat:{event_id}). Reference for merchant_risk_score fallback logic.

### PostgreSQL Schema (source data)
- `payment-backend/db/` — Alembic migrations define payment_state_log and ledger_entries schemas. Migration 001 = state_log, 003 = ledger_entries (append-only, DEFERRABLE balance trigger).
- `payment-backend/dbt/models/staging/stg_transactions.sql` — shows the canonical join pattern for state_log + ledger_entries. The batch script should use similar logic.

### Script Pattern Reference
- `payment-backend/scripts/seed_demo_data.py` — shows psycopg2 connection pattern, structlog usage, and how to query payment_state_log + ledger_entries from Python. Match this style.

### ML Training
- `payment-backend/ml/train.py` — defines FEATURE_NAMES list and synthetic data generation. D-17 adds a comment block here. The Parquet schema must align with this file's expectations.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `spark/feature_functions.py`: `compute_hour_of_day(str) -> int`, `compute_weekend_flag(str) -> int`, `compute_amount_cents_log(int) -> float`, `update_merchant_stats_and_zscore(redis, merchant_id, amount_cents) -> float` — all pure Python, no Spark dependency, import directly.
- `scripts/seed_demo_data.py`: psycopg2 connection via `DATABASE_URL` env var, structlog setup, insert patterns — copy this boilerplate for the reconstruction script.
- `ml/train.py`: `FEATURE_NAMES` list (the 8-feature schema) — import or copy this list to validate Parquet column names.

### Established Patterns
- `structlog` for all logging — never `print()` (CLAUDE.md rule)
- `python-dotenv` for secrets — `DATABASE_URL`, `REDIS_URL` from `.env`
- `argparse` is the right CLI library (no Click dependency exists in the project)
- Scripts run from `payment-backend/` root directory

### Integration Points
- Reads: `payment_state_log` (state transitions + timestamps) + `ledger_entries` (amount_cents for DEBIT rows)
- Reads: Redis `merchant:risk:{merchant_id}` for merchant_risk_score (optional, falls back to 0.5)
- Writes: `data/feature_store/features_YYYYMMDD.parquet` (new directory, should be git-ignored)
- Touches: `ml/train.py` docstring (D-17 comment block addition only)

### Key Schema Detail
- `payment_state_log.created_at` at `from_state=NULL, to_state='INITIATED'` = transaction start timestamp (use for hour_of_day, weekend_flag, velocity windows)
- `ledger_entries.amount_cents` WHERE `entry_type='DEBIT'` = transaction amount (use for amount_cents_log, amount_zscore)
- Both tables are append-only — no UPDATE/DELETE, safe to read with simple SELECT

</code_context>

<deferred>
## Deferred Ideas

- **Model retraining with real data** — requires a labeling strategy (ground truth fraud labels). Out of scope; train.py comment (D-17) leaves the door open.
- **BigQuery historical_features table write** — stretch goal only (D-15). Not a primary deliverable.
- **Redis warm-up** — explicitly rejected due to key collision risk with live pipeline. Not in any future phase unless Redis is behind a feature flag.
- **Schedulable Airflow DAG wrapping this script** — would be the natural next step for automated nightly backfill. Belongs in Phase 11 or a backlog item, not here.

</deferred>

---

*Phase: 10-feature-replay-engine*
*Context gathered: 2026-03-30*
