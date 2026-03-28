-- fact_transactions: One row per transaction with its latest state and amount.
-- Grain: one row per transaction_id.
-- Joins dim_merchants to ensure only transactions with known merchants are included.

select
    t.transaction_id,
    t.merchant_id,
    t.current_state,
    t.previous_state,
    t.event_id,
    t.state_updated_at,
    t.amount_cents,
    t.currency,
    t.settled_at
from {{ ref('stg_transactions') }} t
inner join {{ ref('dim_merchants') }} dm
    on t.merchant_id = dm.merchant_id
