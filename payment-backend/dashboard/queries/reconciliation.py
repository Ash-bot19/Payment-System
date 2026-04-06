import pandas as pd
import streamlit as st

from dashboard.queries import get_connection


@st.cache_data(ttl=300)
def get_reconciliation_summary() -> pd.DataFrame:
    conn = get_connection()
    return pd.read_sql_query(
        """
        SELECT
            run_date,
            discrepancy_type,
            COUNT(*)                       AS discrepancy_count,
            COUNT(DISTINCT transaction_id) AS affected_transactions,
            COUNT(DISTINCT merchant_id)    AS affected_merchants,
            SUM(COALESCE(diff_cents, 0))   AS total_diff_cents,
            MIN(created_at)                AS first_seen_at,
            MAX(created_at)                AS last_seen_at
        FROM reconciliation_discrepancies
        GROUP BY run_date, discrepancy_type
        ORDER BY run_date DESC, discrepancy_count DESC
        """,
        conn,
    )
