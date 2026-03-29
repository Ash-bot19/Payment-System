import pandas as pd
import streamlit as st

from dashboard.queries import get_connection


@st.cache_data(ttl=300)
def get_payment_volume() -> pd.DataFrame:
    conn = get_connection()
    return pd.read_sql_query(
        "SELECT * FROM dbt_dev.hourly_payment_volume ORDER BY payment_hour DESC",
        conn,
    )
