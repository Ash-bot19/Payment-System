-- fraud_metrics: Daily fraud summary from fact_transactions.
-- Grain: one row per state (FLAGGED, AUTHORIZED, SETTLED) per day.

select
    date_trunc('day', state_updated_at)     as metric_date,
    current_state,
    count(*)                                as transaction_count,
    count(distinct merchant_id)             as unique_merchants,
    sum(coalesce(amount_cents, 0))          as total_amount_cents,
    avg(coalesce(amount_cents, 0))          as avg_amount_cents
from {{ ref('fact_transactions') }}
group by date_trunc('day', state_updated_at), current_state
order by metric_date desc, transaction_count desc
