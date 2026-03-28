-- dim_users: Stub user dimension.
-- Distinct event_ids from payment_state_log as a proxy for user activity.
-- Phase 9 will expand with user attributes when the dashboard requires them.

select distinct
    event_id
from {{ source('payment_db', 'payment_state_log') }}
where event_id is not null
