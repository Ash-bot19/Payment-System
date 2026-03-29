import pandas as pd
import streamlit as st

from dashboard.queries import get_connection


@st.cache_data(ttl=300)
def get_fraud_metrics() -> pd.DataFrame:
    conn = get_connection()
    return pd.read_sql_query(
        "SELECT * FROM dbt_dev.fraud_metrics ORDER BY metric_date DESC",
        conn,
    )
