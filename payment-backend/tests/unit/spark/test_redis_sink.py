"""Unit tests for spark.redis_sink — foreachBatch Redis write function.

TDD RED: these tests are written before the implementation.

Uses MagicMock for redis client and batch_df to avoid real Redis/Spark connections.
"""

import types
import pytest
from unittest.mock import MagicMock, patch, call


def make_fake_row(
    event_id="evt_001",
    merchant_id="merch_1",
    stripe_customer_id="cus_1",
    amount_cents=5000,
    received_at="2024-03-15T14:30:00",
    hour_of_day=14,
    weekend_flag=0,
    amount_cents_log=8.517,
    tx_velocity_1m=3,
    tx_velocity_5m=10,
):
    """Create a SimpleNamespace row mimicking a Spark Row object."""
    return types.SimpleNamespace(
        event_id=event_id,
        merchant_id=merchant_id,
        stripe_customer_id=stripe_customer_id,
        amount_cents=amount_cents,
        received_at=received_at,
        hour_of_day=hour_of_day,
        weekend_flag=weekend_flag,
        amount_cents_log=amount_cents_log,
        tx_velocity_1m=tx_velocity_1m,
        tx_velocity_5m=tx_velocity_5m,
    )


@pytest.fixture
def mock_redis_client():
    """MagicMock redis client with pipeline support."""
    mock_r = MagicMock()
    mock_pipe = MagicMock()
    mock_r.pipeline.return_value = mock_pipe
    return mock_r, mock_pipe


def make_batch_df(rows):
    """Create a MagicMock batch_df where .collect() returns the given rows."""
    batch_df = MagicMock()
    batch_df.collect.return_value = rows
    return batch_df


class TestFeatureHashWritten:
    def test_feature_hash_written(self, mock_redis_client):
        """pipeline.hset must be called with feat:evt_001 and all 8 feature keys."""
        mock_r, mock_pipe = mock_redis_client
        mock_r.hget.return_value = "0.8"
        mock_r.get.return_value = None

        with patch("spark.redis_sink.create_redis_client", return_value=mock_r), \
             patch("spark.redis_sink.update_merchant_stats_and_zscore", return_value=1.5):
            from spark.redis_sink import write_features_to_redis
            batch_df = make_batch_df([make_fake_row()])
            write_features_to_redis(batch_df, epoch_id=0)

        mock_pipe.hset.assert_called_once()
        call_args = mock_pipe.hset.call_args
        key_arg = call_args[0][0] if call_args[0] else call_args[1].get("name", call_args[1].get("key"))
        assert key_arg == "feat:evt_001"

        mapping = call_args[1].get("mapping") or (call_args[0][1] if len(call_args[0]) > 1 else None)
        assert mapping is not None
        expected_keys = {
            "tx_velocity_1m", "tx_velocity_5m", "amount_zscore",
            "merchant_risk_score", "device_switch_flag",
            "hour_of_day", "weekend_flag", "amount_cents_log",
        }
        assert expected_keys == set(mapping.keys())

    def test_feature_hash_ttl(self, mock_redis_client):
        """pipeline.expire must be called with feat:evt_001 and TTL 3600."""
        mock_r, mock_pipe = mock_redis_client
        mock_r.hget.return_value = None
        mock_r.get.return_value = None

        with patch("spark.redis_sink.create_redis_client", return_value=mock_r), \
             patch("spark.redis_sink.update_merchant_stats_and_zscore", return_value=0.0):
            from spark.redis_sink import write_features_to_redis
            batch_df = make_batch_df([make_fake_row()])
            write_features_to_redis(batch_df, epoch_id=1)

        mock_pipe.expire.assert_called_once_with("feat:evt_001", 3600)


class TestDeviceSwitchFlag:
    def test_device_switch_flag_new_customer(self, mock_redis_client):
        """New customer (no prior device_seen key) → device_switch_flag=0."""
        mock_r, mock_pipe = mock_redis_client
        mock_r.hget.return_value = None
        mock_r.get.return_value = None  # no prior device seen

        with patch("spark.redis_sink.create_redis_client", return_value=mock_r), \
             patch("spark.redis_sink.update_merchant_stats_and_zscore", return_value=0.0):
            from spark.redis_sink import write_features_to_redis
            batch_df = make_batch_df([make_fake_row(stripe_customer_id="cus_1", merchant_id="merch_1")])
            write_features_to_redis(batch_df, epoch_id=2)

        # Verify device_seen set is called
        mock_pipe.set.assert_called_once_with("device_seen:cus_1", "merch_1", ex=300)

        # Verify device_switch_flag=0 in the hset mapping
        hset_call = mock_pipe.hset.call_args
        mapping = hset_call[1].get("mapping") or (hset_call[0][1] if len(hset_call[0]) > 1 else None)
        assert mapping["device_switch_flag"] == 0

    def test_device_switch_flag_same_merchant(self, mock_redis_client):
        """Customer seen at same merchant → device_switch_flag=0."""
        mock_r, mock_pipe = mock_redis_client
        mock_r.hget.return_value = None
        mock_r.get.return_value = "merch_1"  # same merchant

        with patch("spark.redis_sink.create_redis_client", return_value=mock_r), \
             patch("spark.redis_sink.update_merchant_stats_and_zscore", return_value=0.0):
            from spark.redis_sink import write_features_to_redis
            batch_df = make_batch_df([make_fake_row(stripe_customer_id="cus_1", merchant_id="merch_1")])
            write_features_to_redis(batch_df, epoch_id=3)

        hset_call = mock_pipe.hset.call_args
        mapping = hset_call[1].get("mapping") or (hset_call[0][1] if len(hset_call[0]) > 1 else None)
        assert mapping["device_switch_flag"] == 0

    def test_device_switch_flag_different_merchant(self, mock_redis_client):
        """Customer seen at different merchant → device_switch_flag=1."""
        mock_r, mock_pipe = mock_redis_client
        mock_r.hget.return_value = None
        mock_r.get.return_value = "merch_old"  # different merchant

        with patch("spark.redis_sink.create_redis_client", return_value=mock_r), \
             patch("spark.redis_sink.update_merchant_stats_and_zscore", return_value=0.0):
            from spark.redis_sink import write_features_to_redis
            batch_df = make_batch_df([make_fake_row(stripe_customer_id="cus_1", merchant_id="merch_1")])
            write_features_to_redis(batch_df, epoch_id=4)

        hset_call = mock_pipe.hset.call_args
        mapping = hset_call[1].get("mapping") or (hset_call[0][1] if len(hset_call[0]) > 1 else None)
        assert mapping["device_switch_flag"] == 1


class TestMerchantRiskScore:
    def test_merchant_risk_score_exists(self, mock_redis_client):
        """merchant:risk:{merchant_id} score=0.8 → merchant_risk_score=0.8 in mapping."""
        mock_r, mock_pipe = mock_redis_client
        mock_r.hget.return_value = "0.8"
        mock_r.get.return_value = None

        with patch("spark.redis_sink.create_redis_client", return_value=mock_r), \
             patch("spark.redis_sink.update_merchant_stats_and_zscore", return_value=0.0):
            from spark.redis_sink import write_features_to_redis
            batch_df = make_batch_df([make_fake_row(merchant_id="merch_1")])
            write_features_to_redis(batch_df, epoch_id=5)

        hset_call = mock_pipe.hset.call_args
        mapping = hset_call[1].get("mapping") or (hset_call[0][1] if len(hset_call[0]) > 1 else None)
        assert mapping["merchant_risk_score"] == 0.8

    def test_merchant_risk_score_missing(self, mock_redis_client):
        """No merchant:risk key → merchant_risk_score=0.5 (default)."""
        mock_r, mock_pipe = mock_redis_client
        mock_r.hget.return_value = None
        mock_r.get.return_value = None

        with patch("spark.redis_sink.create_redis_client", return_value=mock_r), \
             patch("spark.redis_sink.update_merchant_stats_and_zscore", return_value=0.0):
            from spark.redis_sink import write_features_to_redis
            batch_df = make_batch_df([make_fake_row(merchant_id="merch_1")])
            write_features_to_redis(batch_df, epoch_id=6)

        hset_call = mock_pipe.hset.call_args
        mapping = hset_call[1].get("mapping") or (hset_call[0][1] if len(hset_call[0]) > 1 else None)
        assert mapping["merchant_risk_score"] == 0.5


class TestPipelineExecution:
    def test_pipeline_execute_called(self, mock_redis_client):
        """pipe.execute() must be called exactly once per batch."""
        mock_r, mock_pipe = mock_redis_client
        mock_r.hget.return_value = None
        mock_r.get.return_value = None

        with patch("spark.redis_sink.create_redis_client", return_value=mock_r), \
             patch("spark.redis_sink.update_merchant_stats_and_zscore", return_value=0.0):
            from spark.redis_sink import write_features_to_redis
            batch_df = make_batch_df([make_fake_row(), make_fake_row(event_id="evt_002")])
            write_features_to_redis(batch_df, epoch_id=7)

        mock_pipe.execute.assert_called_once()

    def test_empty_batch_skips(self, mock_redis_client):
        """Empty batch_df.collect() → no pipeline created, no Redis calls."""
        mock_r, mock_pipe = mock_redis_client

        with patch("spark.redis_sink.create_redis_client", return_value=mock_r), \
             patch("spark.redis_sink.update_merchant_stats_and_zscore", return_value=0.0):
            from spark.redis_sink import write_features_to_redis
            batch_df = make_batch_df([])
            write_features_to_redis(batch_df, epoch_id=8)

        mock_r.pipeline.assert_not_called()
