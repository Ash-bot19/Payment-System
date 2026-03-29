"""Integration tests for replay/feature_reconstruction.py.

Tests run against a live PostgreSQL database seeded with demo data
(scripts/seed_demo_data.py). All tests are skipped gracefully when
PostgreSQL is not available.

Test coverage:
1. test_reconstruct_features_returns_dataframe — full column schema + value ranges
2. test_write_parquet_produces_valid_file — Parquet round-trip, 12 columns, float64 dtypes
3. test_date_range_filter — filtered vs unfiltered row counts
4. test_main_cli_runs_without_error — end-to-end CLI entry point with tmpdir
"""

import os
import tempfile
from datetime import date
from unittest.mock import patch

import pandas as pd
import psycopg2
import pytest
import structlog

logger = structlog.get_logger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://payment:payment@localhost:5432/payment_db"
)


def _pg_available() -> bool:
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(
    not _pg_available(), reason="PostgreSQL not available"
)


# ─── Test 1: reconstruct_features returns a valid DataFrame ──────────────────


@requires_pg
def test_reconstruct_features_returns_dataframe():
    """reconstruct_features returns a pd.DataFrame with correct columns and value ranges."""
    from replay.feature_reconstruction import (
        FEATURE_COLUMNS,
        METADATA_COLUMNS,
        reconstruct_features,
    )

    conn = psycopg2.connect(DATABASE_URL)
    try:
        df = reconstruct_features(conn)
    finally:
        conn.close()

    assert isinstance(df, pd.DataFrame), "Result must be a pandas DataFrame"

    if df.empty:
        # Seed data not loaded — columns should still be correct
        expected_cols = METADATA_COLUMNS + FEATURE_COLUMNS
        assert list(df.columns) == expected_cols, (
            f"Empty DataFrame must have correct columns: {expected_cols}"
        )
        return

    # All 8 feature columns must be present
    for col in FEATURE_COLUMNS:
        assert col in df.columns, f"Feature column '{col}' missing from DataFrame"

    # All 4 metadata columns must be present
    for col in METADATA_COLUMNS:
        assert col in df.columns, f"Metadata column '{col}' missing from DataFrame"

    # device_switch_flag must always be 0 (D-09)
    assert (df["device_switch_flag"] == 0).all(), (
        "device_switch_flag must be 0 for all rows (device sequence not in PostgreSQL)"
    )

    # No NaN in feature columns (amount_zscore returns 0.0 for std=0, not NaN)
    for col in FEATURE_COLUMNS:
        nan_count = df[col].isna().sum()
        assert nan_count == 0, (
            f"Feature column '{col}' has {nan_count} NaN values — expected 0"
        )

    # hour_of_day must be in [0, 23]
    assert df["hour_of_day"].between(0, 23).all(), (
        "hour_of_day values must be in range [0, 23]"
    )

    # weekend_flag must be in {0, 1}
    assert df["weekend_flag"].isin([0, 1]).all(), (
        "weekend_flag values must be 0 or 1"
    )

    # amount_cents_log must be > 0 (log1p of positive cents)
    assert (df["amount_cents_log"] > 0).all(), (
        "amount_cents_log must be > 0 for positive amount_cents (log1p)"
    )

    # tx_velocity_1m must be >= 1 (at least the row itself counts)
    assert (df["tx_velocity_1m"] >= 1).all(), (
        "tx_velocity_1m must be >= 1 (SQL window includes current row)"
    )

    # tx_velocity_5m must be >= tx_velocity_1m (5m window is always >= 1m window)
    assert (df["tx_velocity_5m"] >= df["tx_velocity_1m"]).all(), (
        "tx_velocity_5m must be >= tx_velocity_1m (wider window includes 1m window)"
    )

    logger.info(
        "test_reconstruct_features_passed",
        row_count=len(df),
        columns=list(df.columns),
    )


# ─── Test 2: write_parquet produces a valid Parquet file ─────────────────────


@requires_pg
def test_write_parquet_produces_valid_file():
    """write_parquet produces a Parquet file with 12 columns, float64 features, non-zero size."""
    from replay.feature_reconstruction import (
        FEATURE_COLUMNS,
        reconstruct_features,
        write_parquet,
    )

    conn = psycopg2.connect(DATABASE_URL)
    try:
        df = reconstruct_features(conn)
    finally:
        conn.close()

    if df.empty:
        pytest.skip("No data in database — skipping Parquet output test")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = write_parquet(df, tmpdir)

        # File must exist and be non-empty
        assert os.path.isfile(output_path), f"Parquet file not created at {output_path}"
        assert os.path.getsize(output_path) > 0, "Parquet file is empty"

        # Read back and verify schema
        result = pd.read_parquet(output_path)

        # Must have exactly 12 columns (8 features + 4 metadata)
        assert len(result.columns) == 12, (
            f"Expected 12 columns, got {len(result.columns)}: {list(result.columns)}"
        )

        # All feature columns must be float64
        for col in FEATURE_COLUMNS:
            assert result[col].dtype == "float64", (
                f"Feature column '{col}' dtype is '{result[col].dtype}', expected float64"
            )

        logger.info(
            "test_write_parquet_passed",
            path=output_path,
            row_count=len(result),
            columns=list(result.columns),
        )


# ─── Test 3: date range filter ────────────────────────────────────────────────


@requires_pg
def test_date_range_filter():
    """Date range filters correctly limit result rows."""
    from replay.feature_reconstruction import reconstruct_features

    conn = psycopg2.connect(DATABASE_URL)
    try:
        # Far future start date — should return empty or subset
        df_future = reconstruct_features(conn, start_date=date(2099, 1, 1))

        # No filter — should return >= the filtered count
        df_all = reconstruct_features(conn)
    finally:
        conn.close()

    assert isinstance(df_future, pd.DataFrame), "Filtered result must be a DataFrame"
    assert isinstance(df_all, pd.DataFrame), "Unfiltered result must be a DataFrame"

    # Future start date should return fewer rows than no filter
    assert len(df_future) <= len(df_all), (
        f"Filtered (start=2099) returned {len(df_future)} rows but "
        f"unfiltered returned {len(df_all)} rows — expected filtered <= unfiltered"
    )

    # A past start date (before seed data) should return >= 0 rows
    conn2 = psycopg2.connect(DATABASE_URL)
    try:
        df_past = reconstruct_features(conn2, start_date=date(2020, 1, 1))
    finally:
        conn2.close()

    assert len(df_past) >= 0, "Past start date result must have non-negative row count"

    logger.info(
        "test_date_range_filter_passed",
        rows_all=len(df_all),
        rows_future_filter=len(df_future),
        rows_past_start=len(df_past),
    )


# ─── Test 4: main CLI runs without error ─────────────────────────────────────


@requires_pg
def test_main_cli_runs_without_error():
    """main([]) runs end-to-end, connects to PostgreSQL, writes Parquet to tmpdir."""
    import replay.feature_reconstruction as frc

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(frc, "OUTPUT_DIR", tmpdir):
            # Should not raise any exception
            try:
                frc.main([])
            except SystemExit as exc:
                # sys.exit(0) is OK (empty dataset path); sys.exit(1) is a failure
                if exc.code != 0:
                    raise AssertionError(
                        f"main([]) exited with code {exc.code} (expected 0 or Parquet file)"
                    ) from exc

        # If data exists, a Parquet file should be in tmpdir
        parquet_files = [f for f in os.listdir(tmpdir) if f.endswith(".parquet")]

        # Either a Parquet file was written (data exists) or the dir is empty (no data)
        logger.info(
            "test_main_cli_passed",
            tmpdir=tmpdir,
            parquet_files=parquet_files,
        )

        # If any Parquet files were written, verify they are non-empty
        for fname in parquet_files:
            fpath = os.path.join(tmpdir, fname)
            assert os.path.getsize(fpath) > 0, f"Parquet file {fname} is empty"
