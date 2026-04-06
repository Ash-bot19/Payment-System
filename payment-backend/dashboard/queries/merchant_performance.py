import pandas as pd
import streamlit as st

from dashboard.queries import get_connection


@st.cache_data(ttl=300)
def get_merchant_performance() -> pd.DataFrame:
    conn = get_connection()
    return pd.read_sql_query(
        """
        WITH latest_state AS (
            SELECT DISTINCT ON (transaction_id)
                transaction_id, merchant_id, to_state AS current_state
            FROM payment_state_log
            ORDER BY transaction_id, created_at DESC
        ),
        ledger_balanced AS (
            SELECT
                transaction_id,
                MAX(CASE WHEN entry_type = 'DEBIT'  THEN amount_cents END) AS debit_amount_cents,
                MAX(CASE WHEN entry_type = 'CREDIT' THEN amount_cents END) AS credit_amount_cents
            FROM ledger_entries
            GROUP BY transaction_id
        )
        SELECT
            ls.merchant_id,
            COUNT(DISTINCT ls.transaction_id)                                          AS total_transactions,
            COUNT(DISTINCT CASE WHEN ls.current_state = 'SETTLED' THEN ls.transaction_id END) AS settled_transactions,
            COUNT(DISTINCT CASE WHEN ls.current_state = 'FLAGGED' THEN ls.transaction_id END) AS flagged_transactions,
            SUM(COALESCE(lb.debit_amount_cents, 0))                                    AS total_settled_amount_cents,
            AVG(COALESCE(lb.debit_amount_cents, 0))                                    AS avg_transaction_amount_cents,
            SUM(CASE WHEN lb.debit_amount_cents != lb.credit_amount_cents THEN 1 ELSE 0 END) AS imbalanced_transactions
        FROM latest_state ls
        LEFT JOIN ledger_balanced lb ON ls.transaction_id = lb.transaction_id
        GROUP BY ls.merchant_id
        ORDER BY total_settled_amount_cents DESC
        """,
        conn,
    )
