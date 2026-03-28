-- hourly_payment_volume: Settled payment volume by hour.
-- Only includes SETTLED transactions (amount_cents is non-null).
-- Grain: one row per hour.

select
    date_trunc('hour', settled_at)          as payment_hour,
    count(*)                                as settled_count,
    sum(amount_cents)                       as total_amount_cents,
    avg(amount_cents)                       as avg_amount_cents,
    min(amount_cents)                       as min_amount_cents,
    max(amount_cents)                       as max_amount_cents
from {{ ref('fact_transactions') }}
where current_state = 'SETTLED'
  and settled_at is not null
group by date_trunc('hour', settled_at)
order by payment_hour desc
