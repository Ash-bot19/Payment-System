import os
import sys

import streamlit as st

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from dashboard.queries.payment_volume import get_payment_volume

st.header("Payment Volume")

df = get_payment_volume()

if df.empty:
    st.warning("No payment volume data. Run seed script and dbt first.")
    st.stop()

# KPI cards
col1, col2, col3 = st.columns(3)
col1.metric("Total Settled", int(df["settled_count"].sum()))
total_usd = df["total_amount_cents"].sum() / 100
col2.metric("Total Volume (USD)", f"${total_usd:,.2f}")
col3.metric(
    "Avg per Transaction (USD)", f"${df['avg_amount_cents'].mean() / 100:,.2f}"
)

# Chart: settled volume over time
st.subheader("Hourly Settlement Volume")
chart_df = df.set_index("payment_hour")[["total_amount_cents"]].sort_index()
st.line_chart(chart_df)

# Table
st.subheader("Hourly Breakdown")
st.dataframe(df, use_container_width=True)
