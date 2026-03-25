"""Unit tests for spark.feature_functions — pure feature transform functions.

TDD RED: these tests are written before the implementation.
"""

import math
import pytest
from unittest.mock import MagicMock

from spark.feature_functions import (
    compute_hour_of_day,
    compute_weekend_flag,
    compute_amount_cents_log,
    update_merchant_stats_and_zscore,
)


class TestHourOfDay:
    def test_hour_mid_afternoon(self):
        assert compute_hour_of_day("2024-03-15T14:30:00") == 14

    def test_hour_midnight(self):
        assert compute_hour_of_day("2024-03-15T00:00:00") == 0

    def test_hour_end_of_day(self):
        assert compute_hour_of_day("2024-03-15T23:59:59") == 23


class TestWeekendFlag:
    def test_saturday_is_weekend(self):
        # 2024-03-16 is a Saturday
        assert compute_weekend_flag("2024-03-16T12:00:00") == 1

    def test_sunday_is_weekend(self):
        # 2024-03-17 is a Sunday
        assert compute_weekend_flag("2024-03-17T12:00:00") == 1

    def test_monday_is_weekday(self):
        # 2024-03-18 is a Monday
        assert compute_weekend_flag("2024-03-18T12:00:00") == 0

    def test_friday_is_weekday(self):
        # 2024-03-15 is a Friday
        assert compute_weekend_flag("2024-03-15T12:00:00") == 0


class TestAmountCentsLog:
    def test_zero_amount(self):
        result = compute_amount_cents_log(0)
        assert result == 0.0

    def test_ten_thousand_cents(self):
        result = compute_amount_cents_log(10000)
        assert result == pytest.approx(math.log1p(10000), rel=1e-6)

    def test_one_cent(self):
        result = compute_amount_cents_log(1)
        assert result == pytest.approx(math.log1p(1), rel=1e-6)


class TestWelfordZscore:
    def test_cold_start_returns_zero(self):
        """Empty merchant stats (no Redis key) should return 0.0."""
        mock_redis = MagicMock()
        mock_redis.hgetall.return_value = {}
        result = update_merchant_stats_and_zscore(mock_redis, "merch_cold", 5000)
        assert result == 0.0

    def test_single_point_returns_zero(self):
        """Count=1 means no variance computable — must return 0.0."""
        mock_redis = MagicMock()
        mock_redis.hgetall.return_value = {
            "count": "1",
            "mean": "100.0",
            "M2": "0.0",
        }
        result = update_merchant_stats_and_zscore(mock_redis, "merch_single", 200)
        assert result == 0.0

    def test_welford_sequential_zscore(self):
        """Feed amounts [100, 200, 300] sequentially; zscore of 300 should be ~1.0."""
        # Use a backing dict to simulate Redis state
        state: dict = {}

        mock_redis = MagicMock()

        def mock_hgetall(key):
            return state.copy()

        def mock_hset(key, mapping):
            state.clear()
            state.update({k: str(v) for k, v in mapping.items()})

        mock_redis.hgetall.side_effect = mock_hgetall
        mock_redis.hset.side_effect = mock_hset

        # Feed 100
        update_merchant_stats_and_zscore(mock_redis, "merch_seq", 100)
        # Feed 200
        update_merchant_stats_and_zscore(mock_redis, "merch_seq", 200)
        # Feed 300 — zscore should be approximately 1.0
        zscore = update_merchant_stats_and_zscore(mock_redis, "merch_seq", 300)
        assert zscore == pytest.approx(1.0, abs=0.1)

    def test_welford_updates_redis(self):
        """After calling update, merchant:stats:{merchant_id} hash must be written."""
        mock_redis = MagicMock()
        mock_redis.hgetall.return_value = {}

        update_merchant_stats_and_zscore(mock_redis, "merch_upd", 1000)

        mock_redis.hset.assert_called_once()
        call_kwargs = mock_redis.hset.call_args
        # Verify key format
        key_arg = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("name")
        assert "merchant:stats:merch_upd" in str(call_kwargs)
        # Verify mapping contains required fields
        mapping = call_kwargs[1].get("mapping") or (
            call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None
        )
        if mapping is None:
            # Try keyword args
            all_kwargs = call_kwargs[1]
            mapping = all_kwargs.get("mapping")
        assert mapping is not None
        assert "count" in mapping
        assert "mean" in mapping
        assert "M2" in mapping
