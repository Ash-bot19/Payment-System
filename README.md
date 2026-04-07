# Real-Time Payment Processing Pipeline

A production-grade payment processing backend built on an event-driven microservices architecture. Stripe webhook events flow through Kafka into a real-time fraud scoring engine, double-entry financial ledger, and nightly reconciliation — deployed on GCP.

---

## Architecture

```
Stripe Webhooks
      │
      ▼
┌─────────────────────┐
│  webhook-service    │  FastAPI — HMAC verification, Redis idempotency, rate limiting
│  (Cloud Run)        │
└──────────┬──────────┘
           │ payment.webhook.received
           ▼
┌─────────────────────┐
│ validation-consumer │  Schema + business-rule validation, payment state machine
│  (Kafka Consumer)   │  INITIATED → VALIDATED / FAILED → DLQ
└──────────┬──────────┘
           │ payment.transaction.validated
           ├──────────────────────────────────────────────────────┐
           ▼                                                      ▼
┌─────────────────────┐                          ┌───────────────────────────┐
│  spark-feature-     │  8 ML features           │   scoring-consumer        │
│  engine             │ ────────────────────────▶│   XGBoost risk scoring    │
│  (Spark Streaming)  │  Redis feat:{event_id}   │   VALIDATED → SCORING     │
└─────────────────────┘                          │   → AUTHORIZED / FLAGGED  │
                                                 └──────────────┬────────────┘
                                                                │ payment.ledger.entry
                                                                │ payment.alert.triggered
                                                                ▼
                                                 ┌───────────────────────────┐
                                                 │   ledger-consumer         │
                                                 │   Double-entry ledger     │
                                                 │   DEBIT + CREDIT rows     │
                                                 │   AUTHORIZED → SETTLED    │
                                                 └──────────────┬────────────┘
                                                                │
                                                 ┌──────────────▼────────────┐
                                                 │        PostgreSQL          │
                                                 │   payment_state_log        │
                                                 │   ledger_entries           │
                                                 │   manual_review_queue      │
                                                 │   reconciliation_          │
                                                 │   discrepancies            │
                                                 └──────┬──────┬─────┬───────┘
                                                        │      │     │
                                           ┌────────────┘      │     └──────────────┐
                                           ▼                   ▼                    ▼
                                ┌─────────────────┐ ┌──────────────────┐ ┌──────────────────┐
                                │  Airflow DAG    │ │  dbt transforms  │ │ Streamlit        │
                                │  nightly recon  │ │  10 models:      │ │ dashboard        │
                                │  vs Stripe API  │ │  staging →       │ │ (Cloud Run)      │
                                │  → BigQuery     │ │  facts → marts   │ │ 4 pages          │
                                └─────────────────┘ └──────────────────┘ └──────────────────┘
```

---

## Stack

| Layer | Technology |
|-------|-----------|
| Ingestion | FastAPI 0.115+, Stripe HMAC webhooks |
| Messaging | Apache Kafka 3.7+ — 7 topics, manual offset commit |
| Deduplication | Redis 7.2+ — `SET NX EX 86400` Lua atomic |
| Feature Engineering | Apache Spark Structured Streaming 3.5+ |
| ML Scoring | XGBoost 2.0+ served via FastAPI |
| Ledger | PostgreSQL 16+ — append-only, double-entry, DB triggers |
| Orchestration | Apache Airflow 2.9+ — nightly reconciliation DAG |
| Warehouse | BigQuery — reconciliation discrepancy export |
| Transformation | dbt 1.8+ — 10 models (staging → dimensions → facts → marts) |
| Dashboard | Streamlit 1.35+ — 4 observability pages |
| Metrics | Prometheus + Grafana |
| Infra | GCP: Cloud Run, Cloud SQL, Cloud Memorystore, Artifact Registry |
| CI/CD | GitHub Actions — build → test → staging (auto) → production (gate) |
| Language | Python 3.11 |

---

## Services

| Service | Port | Description |
|---------|------|-------------|
| `webhook-service` | 8000 | Receives Stripe webhooks, publishes to Kafka |
| `validation-consumer` | 8002 | Validates events, runs state machine |
| `ml-scoring-service` | 8001 | XGBoost risk scoring API |
| `scoring-consumer` | 8003 | Consumes validated events, calls ML service |
| `ledger-consumer` | 8004 | Writes double-entry ledger entries |
| `spark-feature-engine` | 4040 | Computes 8 ML features into Redis |
| `streamlit-dashboard` | 8501 | Observability dashboard |
| `airflow` | 8080 | Nightly reconciliation scheduler |

---

## Kafka Topics

```
payment.webhook.received       — raw inbound Stripe events
payment.transaction.validated  — schema-validated events
payment.transaction.scored     — ML-scored events (risk_score attached)
payment.ledger.entry           — double-entry bookkeeping triggers
payment.reconciliation.queue   — nightly batch feed for Airflow
payment.alert.triggered        — high-risk / flagged transactions
payment.dlq                    — dead letter queue (all failures)
```

---

## ML Risk Score

**Input features** (computed by Spark, stored in Redis):

`tx_velocity_1m` · `tx_velocity_5m` · `amount_zscore` · `merchant_risk_score` · `device_switch_flag` · `hour_of_day` · `weekend_flag` · `amount_cents_log`

**Output:** `{ risk_score: float[0,1], is_high_risk: bool (≥ 0.7), manual_review: bool (≥ 0.85) }`

**SLA:** p99 < 100ms. On Redis timeout: fallback to `manual_review=True`.

---

## Payment State Machine

```
INITIATED → VALIDATED → SCORING → AUTHORIZED (risk < 0.7) → SETTLED
                                → FLAGGED    (risk ≥ 0.7) → MANUAL_REVIEW
Any state → FAILED → TERMINAL (unrecoverable → DLQ)
```

Persisted in `payment_state_log` — append-only, no UPDATE/DELETE enforced by DB trigger.

---

## Ledger Rules

- `ledger_entries` is append-only — UPDATE and DELETE blocked by PostgreSQL trigger
- Every SETTLED transaction produces exactly 2 rows: 1 DEBIT + 1 CREDIT
- DEFERRABLE trigger enforces `SUM(amount_cents) = 0` per `transaction_id` at commit time

---

## Local Setup

**Prerequisites:** Docker Desktop, Python 3.11

> Windows users: set `memory=8GB` in `~/.wslconfig` before starting Docker (Kafka + Spark JVMs need ~6GB RAM).

```bash
# 1. Clone and configure
git clone https://github.com/Ash-bot19/Payment-System.git
cd "Payment System/payment-backend"
cp .env.example .env
# Edit .env — fill in STRIPE_WEBHOOK_SECRET and other values

# 2. Start infrastructure first (avoids OOM spikes from parallel builds)
docker-compose up -d zookeeper kafka redis postgres prometheus grafana

# 3. Run Alembic migrations
PYTHONPATH=. alembic -c db/alembic.ini upgrade head

# 4. Start application services
docker-compose up -d webhook-service validation-consumer scoring-consumer ledger-consumer spark-feature-engine

# 5. Start dashboard
docker-compose up -d streamlit-dashboard

# 6. Seed demo data
python scripts/seed_demo_data.py
```

**Service URLs (local):**

| Service | URL |
|---------|-----|
| Webhook API | http://localhost:8000/docs |
| ML scoring API | http://localhost:8001/docs |
| Streamlit dashboard | http://localhost:8501 |
| Airflow | http://localhost:8080 |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090 |

---

## Tests

```bash
cd payment-backend

# Unit tests
pytest tests/unit/ -x --timeout=60 -q

# Integration tests (requires running Docker stack)
pytest tests/integration/ -x -q

# E2E tests (requires full stack)
pytest tests/e2e/ -x -q

# dbt tests
dbt test --profiles-dir dbt
```

---

## GCP Deployment

Three Cloud Run services, Cloud SQL (PostgreSQL), Cloud Memorystore (Redis), Kafka on a Compute Engine VM, BigQuery for the reconciliation warehouse, and Cloud Scheduler for nightly DAG execution.

**Live URLs:**

| Service | URL |
|---------|-----|
| Webhook service | https://webhook-service-uuxwmvlyea-el.a.run.app |
| ML scoring service | https://ml-scoring-service-94891977471.asia-south1.run.app |
| Streamlit dashboard | https://streamlit-dashboard-94891977471.asia-south1.run.app |

**CI/CD:** GitHub Actions — push to `master` triggers: unit tests → Docker build + push to Artifact Registry → auto-deploy staging → manual approval gate for production.

---

## Project Structure

```
payment-backend/
├── services/          # FastAPI apps: webhook receiver, ML scoring API
├── kafka/             # Producers, consumers, topic configs
├── spark/             # Spark Structured Streaming feature engineering
├── models/            # Pydantic schemas, SQLAlchemy ORM models
├── db/                # Alembic migrations
├── dbt/               # dbt models, sources, tests (10 models)
├── airflow/           # Nightly reconciliation DAG
├── ml/                # XGBoost training, feature store utilities
├── replay/            # Feature replay engine — offline Parquet output
├── dashboard/         # Streamlit observability dashboard
├── infra/             # Dockerfiles, docker-compose, GCP configs
├── monitoring/        # Prometheus config, Grafana provisioning
├── scripts/           # Utility scripts (seed data, webhook event sender)
└── tests/             # unit/, integration/, e2e/
```

---

## Key Design Decisions

**Kafka:** Manual offset commit only (`enable.auto.commit=False` + `enable.auto.offset.store=False`). Offsets committed only after successful downstream write — prevents silent message loss on crash.

**Idempotency:** Redis `SET NX EX 86400` (Lua atomic). Key exists → 200 + skip. Key missing → process → set key. 24h TTL covers Stripe's retry window.

**DLQ contract:** Every dead-letter message includes `original_topic`, `original_offset`, `failure_reason` (enum), `retry_count`, `first_failure_ts`, `payload`. All DLQ consumers are idempotent.

**Ledger:** PostgreSQL triggers physically block UPDATE/DELETE on both `ledger_entries` and `payment_state_log`. The balance check uses `DEFERRABLE INITIALLY DEFERRED` so DEBIT + CREDIT can be inserted in the same transaction before the `SUM=0` constraint fires at commit.

**ML fallback:** Spark writes features ~10s after event arrival; the scoring consumer always checks Redis within ~150ms, so features will not be present. Falling back to `manual_review=True` is the correct designed behavior — not a bug.

**Model storage:** `ml/models/model.ubj` (38KB XGBoost binary) is committed to the repo. Zero runtime dependency on training infrastructure; regenerable at any time from `ml/train.py`.
