---
phase: 09-dashboard-monitoring
plan: 01
status: completed
completed_at: 2026-03-29
---

# Plan 09-01 Summary: Observability Infrastructure

## What was built

### Seed script
- `scripts/seed_demo_data.py` — inserts 15 transactions (8 SETTLED, 4 FLAGGED, 3 AUTHORIZED) across 3 merchants into payment_state_log; 16 ledger rows (DEBIT+CREDIT per SETTLED tx in single DB transaction); 5 reconciliation discrepancies. Idempotent via event_id checks. Uses structlog, no print().

### Prometheus
- `monitoring/prometheus/prometheus.yml` — added `ml-scoring-service:8001` scrape job alongside existing webhook-service

### ml_service.py
- Added `from prometheus_fastapi_instrumentator import Instrumentator` import
- Added `Instrumentator().instrument(app)` after /metrics mount — exposes `http_request_duration_seconds` histogram for Grafana p99 alert

### Grafana provisioning (infrastructure-as-code)
- `monitoring/grafana/provisioning/datasources/prometheus.json` — Prometheus datasource with `uid: "prometheus"`, `url: http://prometheus:9090`, isDefault=true
- `monitoring/grafana/provisioning/alerting/payment_alerts.json` — 2 alert rules:
  - `WebhookErrorRate` — 5xx error rate > 1% over 5m (datasourceUid: "prometheus")
  - `MLScoringLatencyP99` — p99 latency > 200ms over 5m (2× SLA)

### Integration test
- `tests/integration/test_seed_demo_data.py` — 4 tests: row count validation, ledger balance constraint, idempotency, dbt mart population (skips if dbt not available)

## Verification
- All Python files parse without syntax errors
- prometheus.yml contains ml-scoring-service:8001
- ml_service.py contains Instrumentator
- datasource JSON has uid="prometheus"
- alert JSON has both WebhookErrorRate and MLScoringLatencyP99 rules
- seed script uses autocommit=False, structlog, no print(), has DEBIT+CREDIT
