import os
import sys

import streamlit as st

# Ensure dashboard package is importable regardless of PYTHONPATH
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from dashboard.queries.fraud_metrics import get_fraud_metrics

st.header("Fraud Metrics")

df = get_fraud_metrics()

if df.empty:
    st.warning("No fraud metrics data. Run seed script and dbt first.")
    st.stop()

# KPI cards
col1, col2, col3 = st.columns(3)
col1.metric("Total Transactions", int(df["transaction_count"].sum()))
col2.metric("Unique Merchants", int(df["unique_merchants"].max()))
flagged = df[df["current_state"] == "FLAGGED"]["transaction_count"].sum()
col3.metric("Flagged Transactions", int(flagged))

# Chart: transaction count by date and state
st.subheader("Transactions by State Over Time")
chart_df = df.pivot_table(
    index="metric_date",
    columns="current_state",
    values="transaction_count",
    aggfunc="sum",
).fillna(0)
st.bar_chart(chart_df)

# Filterable table
st.subheader("Detail Table")
state_filter = st.selectbox(
    "Filter by state", ["All"] + sorted(df["current_state"].unique().tolist())
)
if state_filter != "All":
    df = df[df["current_state"] == state_filter]
st.dataframe(df, use_container_width=True)
