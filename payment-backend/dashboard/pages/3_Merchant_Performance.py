import streamlit as st

from dashboard.queries.merchant_performance import get_merchant_performance

st.header("Merchant Performance")

df = get_merchant_performance()

if df.empty:
    st.warning("No merchant data. Run seed script and dbt first.")
    st.stop()

# KPI cards
col1, col2, col3 = st.columns(3)
col1.metric("Total Merchants", len(df))
col2.metric(
    "Total Settled (USD)", f"${df['total_settled_amount_cents'].sum() / 100:,.2f}"
)
col3.metric("Total Flagged", int(df["flagged_transactions"].sum()))

# Chart: settled amount per merchant
st.subheader("Settled Amount by Merchant")
chart_df = df.set_index("merchant_id")[["total_settled_amount_cents"]]
st.bar_chart(chart_df)

# Table with merchant filter
st.subheader("Merchant Detail")
merchant_filter = st.selectbox(
    "Filter by merchant", ["All"] + sorted(df["merchant_id"].tolist())
)
display_df = df if merchant_filter == "All" else df[df["merchant_id"] == merchant_filter]
st.dataframe(display_df, use_container_width=True)
