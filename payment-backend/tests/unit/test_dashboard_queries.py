"""Unit tests for dashboard query modules — Phase 09."""
import inspect
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

streamlit = pytest.importorskip("streamlit")


def _cleared(fn):
    """Clear st.cache_data between tests so each test gets a fresh call."""
    if hasattr(fn, "clear"):
        fn.clear()
    return fn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMPTY_DF = pd.DataFrame()


# ---------------------------------------------------------------------------
# fraud_metrics
# ---------------------------------------------------------------------------


def test_get_fraud_metrics_query_uses_dbt_schema():
    from dashboard.queries.fraud_metrics import get_fraud_metrics

    _cleared(get_fraud_metrics)
    mock_conn = MagicMock()
    with patch("dashboard.queries.get_connection", return_value=mock_conn), patch(
        "pandas.read_sql_query", return_value=_EMPTY_DF
    ) as mock_read:
        get_fraud_metrics()
        sql = mock_read.call_args[0][0]
        assert "dbt_dev.fraud_metrics" in sql


def test_get_fraud_metrics_returns_dataframe():
    from dashboard.queries.fraud_metrics import get_fraud_metrics

    _cleared(get_fraud_metrics)
    sample = pd.DataFrame({"metric_date": [], "current_state": [], "transaction_count": []})
    mock_conn = MagicMock()
    with patch("dashboard.queries.get_connection", return_value=mock_conn), patch(
        "pandas.read_sql_query", return_value=sample
    ):
        result = get_fraud_metrics()
    assert isinstance(result, pd.DataFrame)


# ---------------------------------------------------------------------------
# payment_volume
# ---------------------------------------------------------------------------


def test_get_payment_volume_query_uses_dbt_schema():
    from dashboard.queries.payment_volume import get_payment_volume

    _cleared(get_payment_volume)
    mock_conn = MagicMock()
    with patch("dashboard.queries.get_connection", return_value=mock_conn), patch(
        "pandas.read_sql_query", return_value=_EMPTY_DF
    ) as mock_read:
        get_payment_volume()
        sql = mock_read.call_args[0][0]
        assert "dbt_dev.hourly_payment_volume" in sql


# ---------------------------------------------------------------------------
# merchant_performance
# ---------------------------------------------------------------------------


def test_get_merchant_performance_query_uses_dbt_schema():
    from dashboard.queries.merchant_performance import get_merchant_performance

    _cleared(get_merchant_performance)
    mock_conn = MagicMock()
    with patch("dashboard.queries.get_connection", return_value=mock_conn), patch(
        "pandas.read_sql_query", return_value=_EMPTY_DF
    ) as mock_read:
        get_merchant_performance()
        sql = mock_read.call_args[0][0]
        assert "dbt_dev.merchant_performance" in sql


# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------


def test_get_reconciliation_summary_query_uses_dbt_schema():
    from dashboard.queries.reconciliation import get_reconciliation_summary

    _cleared(get_reconciliation_summary)
    mock_conn = MagicMock()
    with patch("dashboard.queries.get_connection", return_value=mock_conn), patch(
        "pandas.read_sql_query", return_value=_EMPTY_DF
    ) as mock_read:
        get_reconciliation_summary()
        sql = mock_read.call_args[0][0]
        assert "dbt_dev.reconciliation_summary" in sql


# ---------------------------------------------------------------------------
# cache_data decorator check
# ---------------------------------------------------------------------------


def test_all_queries_use_cache_data_decorator():
    from dashboard.queries.fraud_metrics import get_fraud_metrics
    from dashboard.queries.merchant_performance import get_merchant_performance
    from dashboard.queries.payment_volume import get_payment_volume
    from dashboard.queries.reconciliation import get_reconciliation_summary

    for fn in (
        get_fraud_metrics,
        get_payment_volume,
        get_merchant_performance,
        get_reconciliation_summary,
    ):
        # st.cache_data-wrapped functions expose a .clear() method
        assert hasattr(fn, "clear"), (
            f"{fn.__name__} missing @st.cache_data (no .clear attribute)"
        )
