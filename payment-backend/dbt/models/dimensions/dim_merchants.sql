-- dim_merchants: Stub merchant dimension.
-- Distinct merchant_ids observed in ledger_entries.
-- Phase 9 will expand with merchant name, tier, and other attributes
-- when the Streamlit dashboard requires them.

select distinct
    merchant_id
from {{ source('payment_db', 'ledger_entries') }}
where merchant_id is not null
