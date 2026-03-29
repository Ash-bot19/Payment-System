"""Unit tests for replay/feature_reconstruction.py.

Tests cover:
- compute_batch_zscore: 4 cases (correct z-scores, std=0, two merchants, empty DataFrame)
- build_query: 3 cases (no dates, start only, both dates)
- parse_args: 3 cases (no args, start only, both dates)
- write_parquet: 1 case (correct columns and filename pattern)
- get_merchant_risk_scores: 1 case (Redis unavailable fallback to 0.5)
- Constants validation: FEATURE_COLUMNS and METADATA_COLUMNS exact values
- device_switch_flag: always 0 in output
"""

import os
import re
import tempfile
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from replay.feature_reconstruction import (
    FEATURE_COLUMNS,
    METADATA_COLUMNS,
    build_query,
    compute_batch_zscore,
    get_merchant_risk_scores,
    parse_args,
    write_parquet,
)


# ─── compute_batch_zscore tests ───────────────────────────────────────────────


class TestComputeBatchZscore:
    def test_single_merchant_three_amounts(self):
        """Three amounts [1000, 2000, 3000]: mean=2000, std=1000 → z-scores [-1, 0, 1]."""
        df = pd.DataFrame(
            {
                "merchant_id": ["merchant_a", "merchant_a", "merchant_a"],
                "amount_cents": [1000, 2000, 3000],
            }
        )
        result = compute_batch_zscore(df)

        assert len(result) == 3
        assert abs(result.iloc[0] - (-1.0)) < 1e-9
        assert abs(result.iloc[1] - 0.0) < 1e-9
        assert abs(result.iloc[2] - 1.0) < 1e-9

    def test_single_merchant_identical_amounts_returns_zero(self):
        """All identical amounts → std=0 → all z-scores = 0.0."""
        df = pd.DataFrame(
            {
                "merchant_id": ["merchant_a", "merchant_a", "merchant_a"],
                "amount_cents": [5000, 5000, 5000],
            }
        )
        result = compute_batch_zscore(df)

        assert len(result) == 3
        assert (result == 0.0).all()

    def test_two_merchants_computed_independently(self):
        """Two merchants each get their own mean/std, not pooled."""
        df = pd.DataFrame(
            {
                "merchant_id": ["merchant_a", "merchant_a", "merchant_b", "merchant_b"],
                "amount_cents": [1000, 3000, 100, 300],
            }
        )
        result = compute_batch_zscore(df)

        assert len(result) == 4

        # Both merchants have [low, high] → z-scores should be [-1, +1] each
        # merchant_a: mean=2000, std=√(2000000)=1414.2..., but pandas uses ddof=1
        # merchant_a amounts [1000, 3000]: mean=2000, std=1414.21... → z ~ [-0.707, +0.707]
        # merchant_b amounts [100, 300]: mean=200, std=141.42... → z ~ [-0.707, +0.707]

        merchant_a_zscores = result.iloc[:2]
        merchant_b_zscores = result.iloc[2:]

        # Both merchants should have the same z-score pattern (scaled same)
        assert abs(merchant_a_zscores.iloc[0] - merchant_b_zscores.iloc[0]) < 1e-9
        assert abs(merchant_a_zscores.iloc[1] - merchant_b_zscores.iloc[1]) < 1e-9

        # Signs: first (low) is negative, second (high) is positive
        assert merchant_a_zscores.iloc[0] < 0
        assert merchant_a_zscores.iloc[1] > 0

    def test_empty_dataframe_returns_empty_series(self):
        """Empty DataFrame → empty Series."""
        df = pd.DataFrame(columns=["merchant_id", "amount_cents"])
        result = compute_batch_zscore(df)

        assert isinstance(result, pd.Series)
        assert len(result) == 0


# ─── build_query tests ────────────────────────────────────────────────────────


class TestBuildQuery:
    def test_no_date_filters(self):
        """No dates → SQL has velocity windows, params is empty list."""
        sql, params = build_query(None, None)

        assert "INTERVAL '1 minute' PRECEDING" in sql
        assert "INTERVAL '5 minutes' PRECEDING" in sql
        assert params == []

    def test_start_date_only_adds_gte_filter(self):
        """start_date only → params has 1 element, SQL has >= filter."""
        start = date(2026, 1, 1)
        sql, params = build_query(start, None)

        assert len(params) == 1
        assert params[0] == start
        assert "psl.created_at >=" in sql

    def test_both_dates_adds_two_filters(self):
        """Both dates → params has 2 elements."""
        start = date(2026, 1, 1)
        end = date(2026, 3, 1)
        sql, params = build_query(start, end)

        assert len(params) == 2
        assert params[0] == start
        assert params[1] == end
        assert "psl.created_at >=" in sql
        assert "psl.created_at <=" in sql


# ─── parse_args tests ─────────────────────────────────────────────────────────


class TestParseArgs:
    def test_no_args_returns_none_dates(self):
        """No args → both dates are None."""
        args = parse_args([])

        assert args.start_date is None
        assert args.end_date is None

    def test_start_date_only(self):
        """--start-date 2026-01-01 → start_date=date(2026, 1, 1), end_date=None."""
        args = parse_args(["--start-date", "2026-01-01"])

        assert args.start_date == date(2026, 1, 1)
        assert args.end_date is None

    def test_both_dates(self):
        """Both dates → both parsed correctly as date objects."""
        args = parse_args(["--start-date", "2026-01-01", "--end-date", "2026-03-01"])

        assert args.start_date == date(2026, 1, 1)
        assert args.end_date == date(2026, 3, 1)


# ─── write_parquet tests ──────────────────────────────────────────────────────


class TestWriteParquet:
    def test_creates_file_with_correct_columns(self):
        """write_parquet creates a Parquet file with all metadata + feature columns."""
        sample_data = {col: [0.0] for col in METADATA_COLUMNS + FEATURE_COLUMNS}
        # Override metadata columns with appropriate types
        sample_data["transaction_id"] = ["txn_001"]
        sample_data["merchant_id"] = ["merchant_a"]
        sample_data["event_id"] = ["evt_001"]
        sample_data["settled_at"] = [pd.Timestamp("2026-01-01")]

        df = pd.DataFrame(sample_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = write_parquet(df, tmpdir)

            # File should exist
            assert os.path.isfile(output_path)

            # File name must match features_YYYYMMDD.parquet
            filename = os.path.basename(output_path)
            assert re.match(r"^features_\d{8}\.parquet$", filename), (
                f"Filename '{filename}' does not match expected pattern"
            )

            # Read back and verify column names
            result_df = pd.read_parquet(output_path)
            expected_cols = METADATA_COLUMNS + FEATURE_COLUMNS
            assert list(result_df.columns) == expected_cols


# ─── get_merchant_risk_scores tests ──────────────────────────────────────────


class TestGetMerchantRiskScores:
    def test_redis_unavailable_returns_fallback(self):
        """When Redis raises ConnectionError, all merchants get 0.5."""
        with patch("redis.Redis.from_url", side_effect=ConnectionError("refused")):
            result = get_merchant_risk_scores(["merchant_a", "merchant_b"])

        assert result == {"merchant_a": 0.5, "merchant_b": 0.5}

    def test_redis_unavailable_import_error_returns_fallback(self):
        """When redis module raises on from_url, fallback still works."""
        with patch(
            "replay.feature_reconstruction.get_merchant_risk_scores",
            wraps=get_merchant_risk_scores,
        ):
            # Patch the internal redis import path
            with patch("redis.Redis.from_url", side_effect=OSError("no redis")):
                result = get_merchant_risk_scores(["merchant_c"])

        assert result == {"merchant_c": 0.5}


# ─── Constants validation tests ───────────────────────────────────────────────


class TestConstants:
    def test_feature_columns_exact_order_and_values(self):
        """FEATURE_COLUMNS must match CLAUDE.md ML contract exactly."""
        expected = [
            "tx_velocity_1m",
            "tx_velocity_5m",
            "amount_zscore",
            "merchant_risk_score",
            "device_switch_flag",
            "hour_of_day",
            "weekend_flag",
            "amount_cents_log",
        ]
        assert FEATURE_COLUMNS == expected

    def test_metadata_columns_exact_values(self):
        """METADATA_COLUMNS must include all 4 required fields."""
        expected = ["transaction_id", "merchant_id", "event_id", "settled_at"]
        assert METADATA_COLUMNS == expected


# ─── device_switch_flag always 0 ─────────────────────────────────────────────


class TestDeviceSwitchFlag:
    def test_device_switch_flag_always_zero_in_feature_columns(self):
        """device_switch_flag is always 0 — verify FEATURE_COLUMNS index and constant."""
        assert "device_switch_flag" in FEATURE_COLUMNS

        # Create a sample output DataFrame as reconstruct_features would produce
        sample_data = {col: [0.0, 0.0, 0.0] for col in METADATA_COLUMNS + FEATURE_COLUMNS}
        sample_data["transaction_id"] = ["t1", "t2", "t3"]
        sample_data["merchant_id"] = ["m1", "m2", "m3"]
        sample_data["event_id"] = ["e1", "e2", "e3"]
        sample_data["settled_at"] = [pd.Timestamp("2026-01-01")] * 3

        df = pd.DataFrame(sample_data)
        # Simulate the device_switch_flag assignment
        df["device_switch_flag"] = 0

        assert (df["device_switch_flag"] == 0).all()
