import os

import psycopg2
import streamlit as st
import structlog

logger = structlog.get_logger(__name__)


@st.cache_resource
def get_connection():
    """Shared psycopg2 connection. @st.cache_resource because psycopg2 is not picklable."""
    url = os.environ.get(
        "DATABASE_URL", "postgresql://payment:payment@localhost:5432/payment_db"
    )
    conn = psycopg2.connect(url)
    conn.autocommit = True  # dashboard is read-only
    logger.info("dashboard_db_connected", url=url.split("@")[1])  # log host only, not creds
    return conn
