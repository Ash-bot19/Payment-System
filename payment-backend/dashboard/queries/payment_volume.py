import pandas as pd
import streamlit as st

from dashboard.queries import get_connection


@st.cache_data(ttl=300)
def get_payment_volume() -> pd.DataFrame:
    conn = get_connection()
    return pd.read_sql_query(
        """
        WITH settled AS (
            SELECT DISTINCT ON (transaction_id)
                transaction_id, to_state AS current_state
            FROM payment_state_log
            ORDER BY transaction_id, created_at DESC
        ),
        debit_amounts AS (
            SELECT transaction_id, amount_cents, created_at AS settled_at
            FROM ledger_entries WHERE entry_type = 'DEBIT'
        )
        SELECT
            DATE_TRUNC('hour', da.settled_at) AS payment_hour,
            COUNT(*)                          AS settled_count,
            SUM(da.amount_cents)              AS total_amount_cents,
            AVG(da.amount_cents)              AS avg_amount_cents,
            MIN(da.amount_cents)              AS min_amount_cents,
            MAX(da.amount_cents)              AS max_amount_cents
        FROM settled s
        INNER JOIN debit_amounts da ON s.transaction_id = da.transaction_id
        WHERE s.current_state = 'SETTLED'
          AND da.settled_at IS NOT NULL
        GROUP BY DATE_TRUNC('hour', da.settled_at)
        ORDER BY payment_hour DESC
        """,
        conn,
    )
