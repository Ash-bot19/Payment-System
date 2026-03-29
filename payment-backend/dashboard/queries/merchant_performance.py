import pandas as pd
import streamlit as st

from dashboard.queries import get_connection


@st.cache_data(ttl=300)
def get_merchant_performance() -> pd.DataFrame:
    conn = get_connection()
    return pd.read_sql_query(
        "SELECT * FROM dbt_dev.merchant_performance ORDER BY total_settled_amount_cents DESC",
        conn,
    )
