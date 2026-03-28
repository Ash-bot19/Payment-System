-- stg_transactions: Latest state per transaction from payment_state_log,
-- joined to ledger_entries DEBIT row for amount_cents.
--
-- DISTINCT ON (transaction_id) keeps the row with the latest created_at
-- per transaction. Non-SETTLED transactions will have amount_cents = NULL
-- (LEFT JOIN — no DEBIT row exists until SETTLED).
--
-- NOTE: DISTINCT ON is PostgreSQL-specific syntax. For Phase 11 (BigQuery
-- prod profile), replace with:
--   ROW_NUMBER() OVER (PARTITION BY transaction_id ORDER BY created_at DESC) = 1
-- wrapped in a CTE with WHERE rn = 1.

with latest_state as (
    select distinct on (transaction_id)
        transaction_id,
        merchant_id,
        to_state        as current_state,
        from_state      as previous_state,
        event_id,
        created_at      as state_updated_at
    from {{ source('payment_db', 'payment_state_log') }}
    order by transaction_id, created_at desc
),

debit_amounts as (
    select
        transaction_id,
        amount_cents,
        currency,
        created_at as settled_at
    from {{ source('payment_db', 'ledger_entries') }}
    where entry_type = 'DEBIT'
)

select
    ls.transaction_id,
    ls.merchant_id,
    ls.current_state,
    ls.previous_state,
    ls.event_id,
    ls.state_updated_at,
    da.amount_cents,
    da.currency,
    da.settled_at
from latest_state ls
left join debit_amounts da
    on ls.transaction_id = da.transaction_id
