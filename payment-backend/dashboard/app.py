import streamlit as st

st.set_page_config(page_title="Payment System Dashboard", layout="wide")

pg = st.navigation(
    {
        "Analytics": [
            st.Page("pages/1_Fraud_Metrics.py", title="Fraud Metrics"),
            st.Page("pages/2_Payment_Volume.py", title="Payment Volume"),
            st.Page("pages/3_Merchant_Performance.py", title="Merchant Performance"),
        ],
        "Operations": [
            st.Page("pages/4_Reconciliation.py", title="Reconciliation"),
        ],
    }
)
pg.run()
