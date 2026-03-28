-- reconciliation_summary: Aggregated discrepancy counts from nightly reconciliation.
-- Source: reconciliation_discrepancies table (populated by persist_discrepancies Airflow task).
-- Grain: one row per run_date + discrepancy_type.

select
    run_date,
    discrepancy_type,
    count(*)                            as discrepancy_count,
    count(distinct transaction_id)      as affected_transactions,
    count(distinct merchant_id)         as affected_merchants,
    sum(coalesce(diff_cents, 0))        as total_diff_cents,
    min(created_at)                     as first_seen_at,
    max(created_at)                     as last_seen_at
from {{ source('payment_db', 'reconciliation_discrepancies') }}
group by run_date, discrepancy_type
order by run_date desc, discrepancy_count desc
