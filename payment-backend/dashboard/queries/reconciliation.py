import pandas as pd
import streamlit as st

from dashboard.queries import get_connection


@st.cache_data(ttl=300)
def get_reconciliation_summary() -> pd.DataFrame:
    conn = get_connection()
    return pd.read_sql_query(
        "SELECT * FROM dbt_dev.reconciliation_summary ORDER BY run_date DESC",
        conn,
    )
