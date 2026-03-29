---
phase: 9
name: Dashboard + Monitoring
status: context-complete
created: 2026-03-28
---

# Phase 9 Context: Dashboard + Monitoring

## Phase Goal

Streamlit observability dashboard connected to PostgreSQL dbt mart tables; Prometheus scraping ml_service; Grafana alert rules provisioned as JSON. BigQuery connection deferred to Phase 11.

> **ROADMAP.md goal updated:** "Streamlit observability dashboard wired to PostgreSQL dbt marts (dev); BigQuery connection is Phase 11. Grafana alerts for SLA breaches provisioned as infrastructure-as-code JSON."

---

## Decisions

### A. Data Source — PostgreSQL now, BigQuery Phase 11

**Decision:** Streamlit connects to PostgreSQL (`postgres:5432` inside compose network, `localhost:5432` from host). No data source abstraction layer.

**Rationale:** BigQuery isn't live until Phase 11. The dbt dev profile already targets PostgreSQL. Zero new infra needed.

**Implementation:**
- Env var `DATABASE_URL` in `.env` / docker-compose for Streamlit (same hostname pattern as all other services: `postgresql://payment:payment@postgres:5432/payment_db`)
- Phase 11 swaps `DATABASE_URL` to BigQuery connection string — one-line change
- No `DATA_SOURCE=postgres|bigquery` toggle — that's overengineering for a single-use switch

**Hostname confirmed:** Docker Compose service name is `postgres` — resolves inside compose network without extra config. All existing services already use `postgres:5432`.

---

### B. Dashboard Scope — 4 pages, one per mart

**Decision:** 4 Streamlit pages, one per dbt mart model. Each page = KPI metric cards + one chart + one filterable table. No drill-downs, no cross-mart joins in Streamlit.

**Pages:**
1. `Fraud Metrics` → queries `fraud_metrics` mart (daily counts by state)
2. `Payment Volume` → queries `hourly_payment_volume` mart (settled volume by hour)
3. `Merchant Performance` → queries `merchant_performance` mart (per-merchant settlement summary)
4. `Reconciliation` → queries `reconciliation_summary` mart (discrepancy counts by type/date)

**Query module:** All SQL lives in `dashboard/queries/` — not inline in page files. One file per mart (e.g. `queries/fraud_metrics.py`).

**Demo seed data — CRITICAL:**

The dbt mart chain requires specific app tables populated. Dependency trace:
- `fraud_metrics`, `hourly_payment_volume`, `merchant_performance` → `fact_transactions` → `stg_transactions` INNER JOIN `dim_merchants`
- `dim_merchants` = `SELECT DISTINCT merchant_id FROM ledger_entries` — **no ledger rows = no merchants = empty fact tables**
- `reconciliation_summary` → `reconciliation_discrepancies` directly

**Seed mechanism:** `scripts/seed_demo_data.py` — Python script inserting directly into live app tables. dbt seeds cannot populate existing app tables.

**Tables to seed:**
| Table | Rows needed | Constraints |
|---|---|---|
| `payment_state_log` | ~20 rows — mix of SETTLED, FLAGGED, AUTHORIZED states; multiple merchants | Append-only trigger: INSERT only, no UPDATE/DELETE |
| `ledger_entries` | DEBIT + CREDIT pairs for each SETTLED transaction; same merchant_id as state_log rows | Balance trigger (DEFERRABLE): DEBIT+CREDIT must be in the same DB transaction |
| `reconciliation_discrepancies` | ~5 rows — mix of MISSING_INTERNALLY, AMOUNT_MISMATCH, DUPLICATE_LEDGER types | Append-only trigger |

**Seed data shape (targets):**
- 3 merchants: `merchant_a`, `merchant_b`, `merchant_c`
- 8 SETTLED transactions (with matching DEBIT+CREDIT ledger pairs)
- 4 FLAGGED transactions (state log only — no ledger entries)
- 3 AUTHORIZED transactions (state log only)
- 5 reconciliation discrepancies (2 MISSING_INTERNALLY, 2 AMOUNT_MISMATCH, 1 DUPLICATE_LEDGER)
- Timestamps spread over last 7 days so time-series charts have shape

---

### C. Prometheus Coverage + Grafana Alert Rules

**Decision:** Add `ml_service` to `prometheus.yml`. Provision 2 Grafana alert rules as JSON. Skip Kafka consumer services (no HTTP server, no scrape endpoint).

**Prometheus changes:**
- `webhook-service:8000` — already registered, keep as-is
- `ml-scoring-service:8001` — add to `prometheus.yml` (already exposes `/metrics` via `prometheus_client.make_asgi_app`)
- Kafka consumers (validation, scoring, ledger) — skip; they have no HTTP server; adding custom metrics is out of scope for this phase

**prometheus.yml result:**
```yaml
scrape_configs:
  - job_name: "webhook-service"
    static_configs:
      - targets: ["webhook-service:8000"]
    metrics_path: /metrics

  - job_name: "ml-scoring-service"
    static_configs:
      - targets: ["ml-scoring-service:8001"]
    metrics_path: /metrics
```

**Grafana alert rules — provisioned as JSON (infrastructure-as-code):**

Two alert rules in `monitoring/grafana/provisioning/alerting/payment_alerts.json`:

| Alert | Condition | Threshold | Rationale |
|---|---|---|---|
| `WebhookErrorRate` | `rate(http_requests_total{job="webhook-service",status=~"5.."}[5m]) / rate(http_requests_total{job="webhook-service"}[5m])` | > 0.01 (1%) | Stripe events failing at ingest |
| `MLScoringLatencyP99` | `histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{job="ml-scoring-service"}[5m]))` | > 0.2 (200ms) | SLA from CLAUDE.md: p99 < 100ms; 200ms is 2× SLA = alert threshold |

**Why provisioned JSON:** Version-controlled, visible in code review, reproduced on `docker compose up` without manual Grafana UI steps — high signal for portfolio.

**Grafana datasource provisioning:** `monitoring/grafana/provisioning/datasources/prometheus.json` — Prometheus datasource wired to `prometheus:9090` inside compose network.

---

### D. Streamlit Deployment

**Decision:** Single service in existing `docker-compose.yml`, port 8501, no auth, `--server.runOnSave true` (dev mode).

```yaml
streamlit-dashboard:
  build:
    context: ..
    dockerfile: dashboard/Dockerfile
  ports:
    - "8501:8501"
  environment:
    - DATABASE_URL=postgresql://payment:payment@postgres:5432/payment_db
  depends_on:
    postgres:
      condition: service_healthy
  command: streamlit run dashboard/app.py --server.port 8501 --server.address 0.0.0.0 --server.runOnSave true
```

One `docker compose up` = full stack including dashboard. No auth — local portfolio demo only.

---

## Code Context

### Existing assets reused

| Asset | Location | How reused |
|---|---|---|
| dbt mart models (4) | `payment-backend/dbt/models/marts/` | Streamlit queries these materialized tables directly |
| Prometheus config | `payment-backend/monitoring/prometheus/prometheus.yml` | Extended with ml-scoring-service target |
| Grafana data volume | `docker-compose.yml`: `grafana_data` | Provisioning files mounted into Grafana container |
| `prometheus_fastapi_instrumentator` | `requirements.txt` line 30 | Already present — no new dependency for webhook-service |
| `prometheus_client` in ml_service | `services/ml_service.py` line 21 | Already exposes /metrics — just needs prometheus.yml registration |
| PostgreSQL hostname | `postgres:5432` (compose network) | Streamlit uses same pattern as all other services |

### New files needed

```
payment-backend/
  dashboard/
    app.py                        # Streamlit multi-page entry point
    pages/
      1_Fraud_Metrics.py
      2_Payment_Volume.py
      3_Merchant_Performance.py
      4_Reconciliation.py
    queries/
      __init__.py
      fraud_metrics.py
      payment_volume.py
      merchant_performance.py
      reconciliation.py
    Dockerfile
  scripts/
    seed_demo_data.py             # Inserts into payment_state_log + ledger_entries + reconciliation_discrepancies
  monitoring/
    grafana/
      provisioning/
        datasources/prometheus.json
        alerting/payment_alerts.json
  tests/
    unit/
      test_dashboard_queries.py   # Query module unit tests (mock DB)
    e2e/
      test_dashboard_e2e.py       # Streamlit accessible at :8501
```

### Locked contracts respected

- `payment_state_log` — append-only (INSERT only in seed script, no UPDATE/DELETE)
- `ledger_entries` — append-only + DEFERRABLE balance trigger (seed script must insert DEBIT+CREDIT in same `BEGIN/COMMIT` block)
- No structlog removed — Streamlit uses structlog for any server-side logging
- No credentials hardcoded — `DATABASE_URL` via env var only

---

## Deferred / Out of Scope for Phase 9

- BigQuery connection → Phase 11
- Auth on Streamlit dashboard → out of scope (ops-only, local)
- Kafka consumer lag alerts → requires Kafka exporter or JMX — out of scope
- Scraping validation/scoring/ledger consumers → no HTTP server; custom metrics are Phase 10+ concern
- Drill-down or cross-mart joins in Streamlit → overengineering
- Expanding `dim_merchants` with name/tier attributes → the model comment mentions Phase 9 but this adds no dashboard value without a merchants source table
