import os
import sys

import streamlit as st

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from dashboard.queries.reconciliation import get_reconciliation_summary

st.header("Reconciliation")

df = get_reconciliation_summary()

if df.empty:
    st.warning("No reconciliation data. Run seed script and dbt first.")
    st.stop()

# KPI cards
col1, col2, col3 = st.columns(3)
col1.metric("Total Discrepancies", int(df["discrepancy_count"].sum()))
col2.metric("Affected Transactions", int(df["affected_transactions"].sum()))
total_diff = df["total_diff_cents"].sum() / 100
col3.metric("Total Diff (USD)", f"${total_diff:,.2f}")

# Chart: discrepancies by type
st.subheader("Discrepancies by Type")
chart_df = df.groupby("discrepancy_type")["discrepancy_count"].sum()
st.bar_chart(chart_df)

# Table with type filter
st.subheader("Discrepancy Detail")
type_filter = st.selectbox(
    "Filter by type", ["All"] + sorted(df["discrepancy_type"].unique().tolist())
)
display_df = (
    df if type_filter == "All" else df[df["discrepancy_type"] == type_filter]
)
st.dataframe(display_df, use_container_width=True)
