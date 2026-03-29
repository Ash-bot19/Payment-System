---
phase: 10-feature-replay-engine
verified: 2026-03-30T00:00:00Z
status: passed
score: 11/11 must-haves verified
gaps: []
---

# Phase 10: Feature Replay Engine Verification Report

**Phase Goal:** Produce a training-ready 8-feature Parquet dataset by reconstructing ML features from historical PostgreSQL data. Primary deliverable: data/feature_store/features_YYYYMMDD.parquet with the exact feature schema that ml/train.py expects.
**Verified:** 2026-03-30
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|----------|
| 1  | Script reads historical transactions from PostgreSQL and produces a Parquet file with all 8 ML features | VERIFIED | `reconstruct_features()` executes SQL against `payment_state_log` + `ledger_entries`, writes via `write_parquet()`; `features_20260330.parquet` exists with 16 rows |
| 2  | Features are reconstructed using existing pure Python functions from spark/feature_functions.py | VERIFIED | `from spark.feature_functions import compute_amount_cents_log, compute_hour_of_day, compute_weekend_flag` at line 85 of feature_reconstruction.py |
| 3  | Velocity features use SQL window functions, not streaming approximation | VERIFIED | `COUNT(*) OVER (PARTITION BY psl.merchant_id ORDER BY psl.created_at RANGE INTERVAL '1 minute' PRECEDING)` and 5-minute equivalent confirmed in build_query() |
| 4  | amount_zscore uses batch retrospective mean/std per merchant, not Welford | VERIFIED | `compute_batch_zscore()` uses pandas groupby + mean/std; `update_merchant_stats_and_zscore` is absent from the file (grep returns 0 matches) |
| 5  | device_switch_flag is always 0 with documented rationale | VERIFIED | `df["device_switch_flag"] = 0` at line 315; "Feature Fidelity Notes" docstring documents D-09 rationale; unit test `TestDeviceSwitchFlag` passes |
| 6  | Script accepts --start-date and --end-date CLI args via argparse | VERIFIED | `parse_args()` implements both args; 3 parse_args unit tests pass |
| 7  | Output Parquet has 8 feature columns + 4 metadata columns | VERIFIED | Parquet file has exactly 12 columns: `['transaction_id', 'merchant_id', 'event_id', 'settled_at', 'tx_velocity_1m', 'tx_velocity_5m', 'amount_zscore', 'merchant_risk_score', 'device_switch_flag', 'hour_of_day', 'weekend_flag', 'amount_cents_log']` |
| 8  | ml/train.py docstring shows how to swap synthetic data for Parquet file | VERIFIED | Lines 14-24 of train.py contain "Retraining with real data:" block with `pd.read_parquet('data/feature_store/features_YYYYMMDD.parquet')` and label gap note |
| 9  | data/feature_store/ directory is git-ignored | VERIFIED | `.gitignore` lines 9-10: `# Generated feature store Parquet files (Phase 10)` + `data/feature_store/` |
| 10 | Script runs end-to-end against live PostgreSQL and produces a valid Parquet file | VERIFIED | `features_20260330.parquet` exists, shape (16, 12); all 4 integration tests pass against live PostgreSQL |
| 11 | Parquet file has exactly 8 feature columns + 4 metadata columns with correct dtypes | VERIFIED | All 8 feature columns are `float64`; 4 metadata columns are object/datetime; confirmed by dtype inspection |

**Score:** 11/11 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `payment-backend/replay/__init__.py` | Package init | VERIFIED | Exists (1-line empty file) |
| `payment-backend/replay/feature_reconstruction.py` | Feature reconstruction batch script (min 150 lines) | VERIFIED | 457 lines; substantive implementation with 6 functions |
| `payment-backend/tests/unit/test_feature_reconstruction.py` | Unit tests (min 80 lines) | VERIFIED | 263 lines; 16 tests, all passing |
| `payment-backend/ml/train.py` | Updated docstring with `read_parquet` | VERIFIED | Contains `read_parquet` at line 18; code unchanged |
| `payment-backend/.gitignore` | Git-ignore for `data/feature_store/` | VERIFIED | Entry present at line 10 |
| `payment-backend/tests/integration/test_feature_reconstruction_integration.py` | Integration test against live PostgreSQL (min 40 lines) | VERIFIED | 253 lines; 4 tests, all passing |
| `payment-backend/data/feature_store/features_20260330.parquet` | Generated Parquet file | VERIFIED | Exists, 16 rows, correct schema |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `replay/feature_reconstruction.py` | `spark/feature_functions.py` | `from spark.feature_functions import` | WIRED | Import at line 85; functions used at lines 296-304 |
| `replay/feature_reconstruction.py` | `data/feature_store/features_YYYYMMDD.parquet` | `df.to_parquet(engine="pyarrow")` | WIRED | `write_parquet()` calls `df.to_parquet(output_path, engine="pyarrow", index=False)` at line 348; file produced at runtime |
| `ml/train.py` | `data/feature_store/features_YYYYMMDD.parquet` | comment showing `pd.read_parquet` path | WIRED | Line 18: `df = pd.read_parquet('data/feature_store/features_YYYYMMDD.parquet')` in docstring |

---

### Requirements Coverage

No requirement IDs were declared for Phase 10 (requirements field: null). Phase goal and must-haves from plan frontmatter used as the verification contract instead.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| None | — | — | — | No anti-patterns found |

Checked for: TODO/FIXME/PLACEHOLDER comments, `return null/[]/{}`, hardcoded empty data, print() statements, console.log-only handlers. All clear.

Notable: `print()` is absent; structlog is used throughout. `update_merchant_stats_and_zscore` (Welford) is absent. No hardcoded placeholder returns.

---

### Human Verification Required

None. All must-haves are programmatically verifiable and confirmed:

- Parquet file exists and schema was read back with pandas
- Unit tests (16/16) and integration tests (4/4) confirmed passing against live PostgreSQL
- Key imports and wiring confirmed via grep
- dtype verification confirmed via pandas inspection

---

### Summary

Phase 10 goal is fully achieved. The feature reconstruction pipeline:

1. Reads SETTLED transactions from PostgreSQL (`payment_state_log` INITIATED rows joined to `ledger_entries` DEBIT rows)
2. Computes all 8 ML features using batch SQL windows (velocity), pure Python functions (hour/weekend/log), batch z-scores (not Welford), Redis merchant risk (with 0.5 fallback), and constant 0 for device_switch_flag (documented in Feature Fidelity Notes)
3. Writes `data/feature_store/features_YYYYMMDD.parquet` with exactly 4 metadata + 8 float64 feature columns
4. The file is git-ignored to prevent committing generated data
5. `ml/train.py` docstring documents the retrain path with the Parquet file and labels-not-included caveat

All 20 tests (16 unit + 4 integration) pass. The Parquet file produced in the UAT session (`features_20260330.parquet`) has 16 rows and the exact schema `ml/train.py` expects.

---

_Verified: 2026-03-30_
_Verifier: Claude (gsd-verifier)_
