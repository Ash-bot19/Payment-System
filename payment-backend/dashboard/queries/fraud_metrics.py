import pandas as pd
import streamlit as st

from dashboard.queries import get_connection


@st.cache_data(ttl=300)
def get_fraud_metrics() -> pd.DataFrame:
    conn = get_connection()
    return pd.read_sql_query(
        """
        WITH latest_state AS (
            SELECT DISTINCT ON (transaction_id)
                transaction_id, merchant_id,
                to_state AS current_state,
                created_at AS state_updated_at
            FROM payment_state_log
            ORDER BY transaction_id, created_at DESC
        ),
        debit_amounts AS (
            SELECT transaction_id, amount_cents
            FROM ledger_entries WHERE entry_type = 'DEBIT'
        )
        SELECT
            DATE_TRUNC('day', ls.state_updated_at) AS metric_date,
            ls.current_state,
            COUNT(*)                               AS transaction_count,
            COUNT(DISTINCT ls.merchant_id)         AS unique_merchants,
            SUM(COALESCE(da.amount_cents, 0))      AS total_amount_cents,
            AVG(COALESCE(da.amount_cents, 0))      AS avg_amount_cents
        FROM latest_state ls
        LEFT JOIN debit_amounts da ON ls.transaction_id = da.transaction_id
        GROUP BY DATE_TRUNC('day', ls.state_updated_at), ls.current_state
        ORDER BY metric_date DESC, transaction_count DESC
        """,
        conn,
    )
