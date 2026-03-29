---
phase: 10-feature-replay-engine
plan: "01"
subsystem: replay
tags: [feature-engineering, batch, parquet, ml, postgresql, redis]
dependency_graph:
  requires:
    - payment-backend/spark/feature_functions.py
    - payment-backend/ml/train.py (FEATURE_NAMES contract)
    - payment-backend/db/ (payment_state_log + ledger_entries schema)
  provides:
    - payment-backend/replay/feature_reconstruction.py
    - data/feature_store/features_YYYYMMDD.parquet (runtime output)
  affects:
    - payment-backend/requirements.txt (pyarrow added)
tech_stack:
  added:
    - pyarrow==23.0.1 (Parquet engine, added to requirements.txt)
  patterns:
    - psycopg2 direct connection (matching seed_demo_data.py pattern)
    - SQL window functions for velocity (COUNT(*) OVER RANGE PRECEDING)
    - Batch retrospective z-score (pandas groupby mean/std, not Welford)
    - Redis GET with 0.5 fallback (matching live ScoringConsumer default)
    - argparse CLI with date range filtering
    - pandas DataFrame.to_parquet(engine='pyarrow')
key_files:
  created:
    - payment-backend/replay/__init__.py
    - payment-backend/replay/feature_reconstruction.py
    - payment-backend/tests/unit/test_feature_reconstruction.py
  modified:
    - payment-backend/requirements.txt (added pyarrow==23.0.1)
decisions:
  - "device_switch_flag=0 always: device sequence not stored in PostgreSQL (D-09); reconstructing from cold storage impossible without device event log"
  - "Batch retrospective z-score (pandas groupby) instead of Welford: Welford requires streaming state; batch mean/std is correct for historical data"
  - "SQL window functions for velocity: COUNT(*) OVER RANGE PRECEDING approximates live Spark tumbling windows without JVM/Spark dependency"
  - "No label column in output Parquet: labeling strategy out of scope (D-18); caller responsible for joining labels before training"
  - "psycopg2 direct (not SQLAlchemy): matches seed_demo_data.py pattern; no ORM needed for read-only batch query"
metrics:
  duration: "3 minutes"
  completed_date: "2026-03-30"
  tasks_completed: 2
  files_created: 3
  files_modified: 1
---

# Phase 10 Plan 01: Feature Reconstruction Script Summary

Batch ML feature reconstruction script that queries PostgreSQL (payment_state_log + ledger_entries) and produces a training-ready Parquet file with all 8 ML features via SQL window functions, batch z-score, and Redis merchant risk lookup.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create replay package and feature reconstruction script | 95868bf | replay/__init__.py, replay/feature_reconstruction.py |
| 2 | Unit tests for feature reconstruction | 8283f9d | tests/unit/test_feature_reconstruction.py, requirements.txt |

## What Was Built

**`replay/feature_reconstruction.py`** (458 lines) — batch feature reconstruction script:

- `build_query(start_date, end_date)`: Returns parameterized SQL with COUNT(*) OVER window functions for tx_velocity_1m (1-minute) and tx_velocity_5m (5-minute), INNER JOIN on DEBIT ledger entries to filter SETTLED transactions only.
- `compute_batch_zscore(df)`: Retrospective per-merchant z-score via pandas groupby mean/std. Returns 0.0 when std=0 (single-transaction merchant).
- `get_merchant_risk_scores(merchant_ids)`: Redis GET from `merchant:risk:{id}`, fallback 0.5 on any exception.
- `reconstruct_features(conn, start_date, end_date)`: Orchestrates all feature computation. Uses `spark.feature_functions` for hour_of_day, weekend_flag, amount_cents_log. Sets device_switch_flag=0 for all rows.
- `write_parquet(df, output_dir)`: Writes to `data/feature_store/features_YYYYMMDD.parquet` using pyarrow engine.
- `parse_args(argv)`: `--start-date` and `--end-date` CLI flags parsed to `date` objects.

**Module docstring** includes:
- "Feature Fidelity Notes" section documenting all 8 feature approximations
- "No Label Column" section explaining why no fraud labels in output (D-18)

**`tests/unit/test_feature_reconstruction.py`** (16 tests, all passing):
- 4 cases for `compute_batch_zscore`
- 3 cases for `build_query`
- 3 cases for `parse_args`
- 1 case for `write_parquet` (column schema + filename pattern)
- 2 cases for `get_merchant_risk_scores` (Redis unavailable fallback)
- 2 cases for constants validation (FEATURE_COLUMNS, METADATA_COLUMNS)
- 1 case for device_switch_flag always 0

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added pyarrow to requirements.txt**
- **Found during:** Task 2 — write_parquet test failed with `Missing optional dependency 'pyarrow'`
- **Issue:** pyarrow not installed in venv; pandas requires it explicitly for Parquet I/O
- **Fix:** `pip install pyarrow==23.0.1` and added `pyarrow==23.0.1` to requirements.txt
- **Files modified:** payment-backend/requirements.txt
- **Commit:** 8283f9d

## Known Stubs

None — all features are computed from real logic. Merchant risk scores fall back to 0.5 when Redis is unavailable, which is documented behaviour matching the live pipeline (not a stub).

## Self-Check: PASSED

- [x] replay/__init__.py exists
- [x] replay/feature_reconstruction.py exists (458 lines, > 150 min_lines)
- [x] tests/unit/test_feature_reconstruction.py exists (263 lines, > 80 min_lines)
- [x] Commit 95868bf exists
- [x] Commit 8283f9d exists
- [x] 16 unit tests all pass
- [x] Feature Fidelity Notes in module docstring
- [x] No update_merchant_stats_and_zscore in feature_reconstruction.py
- [x] No print() in feature_reconstruction.py
- [x] FEATURE_COLUMNS has exactly 8 items matching CLAUDE.md ML contract
- [x] SQL contains "INTERVAL '1 minute' PRECEDING" and "INTERVAL '5 minutes' PRECEDING"
