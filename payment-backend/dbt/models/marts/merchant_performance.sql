-- merchant_performance: Per-merchant settlement and balance metrics.
-- Joins fact_transactions and fact_ledger_balanced for full picture.
-- Grain: one row per merchant_id.

select
    ft.merchant_id,
    count(distinct ft.transaction_id)                   as total_transactions,
    count(distinct case
        when ft.current_state = 'SETTLED'
        then ft.transaction_id end)                     as settled_transactions,
    count(distinct case
        when ft.current_state = 'FLAGGED'
        then ft.transaction_id end)                     as flagged_transactions,
    sum(coalesce(ft.amount_cents, 0))                   as total_settled_amount_cents,
    avg(coalesce(ft.amount_cents, 0))                   as avg_transaction_amount_cents,
    sum(case
        when flb.debit_amount_cents != flb.credit_amount_cents
        then 1 else 0 end)                              as imbalanced_transactions
from {{ ref('fact_transactions') }} ft
left join {{ ref('fact_ledger_balanced') }} flb
    on ft.transaction_id = flb.transaction_id
group by ft.merchant_id
order by total_settled_amount_cents desc
