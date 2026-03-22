## Stack
Stripe Test Mode webhooks → FastAPI (0.115+)
Apache Kafka (3.7+) — 7 topics + DLQ
Redis (7.2+) — idempotency, rate limiting, online feature store
Apache Spark Structured Streaming (3.5+) — real-time feature engineering
XGBoost (2.0+) served via FastAPI — ML risk scoring
PostgreSQL (16+) — double-entry ledger, state machine
Apache Airflow (2.9+) — nightly reconciliation DAG
BigQuery (GCP) — data warehouse, offline feature store
dbt (1.8+) — transformation layer
Streamlit (1.35+) — observability dashboard
Prometheus + Grafana — metrics + alerting
GCP: Cloud Run, Cloud SQL, Cloud Memorystore, BigQuery
GitHub Actions — CI/CD
Python 3.11 | Docker + Docker Compose (local dev) | Windows 11 PowerShell

## Folder Structure
payment-backend/
  services/           # FastAPI apps: webhook receiver, ML scoring API
  kafka/              # producers, consumers, topic configs
  spark/              # feature engineering jobs
  models/             # Pydantic schemas, SQLAlchemy ORM models
  db/                 # Alembic migrations, seed scripts
  dbt/                # dbt models, sources, tests, profiles
  airflow/            # DAGs, custom operators
  ml/                 # XGBoost training, feature store utilities
  replay/             # feature replay engine
  infra/              # Docker Compose, GCP configs
  tests/              # unit/, integration/, e2e/
  scripts/            # utility scripts (PowerShell compatible)
  monitoring/         # Prometheus configs, Grafana dashboards
  .claude/            # GSD plugin files (committed to git)
  CLAUDE.md           # this file

## Kafka Topics (LOCKED — do not rename)

payment.webhook.received       — raw inbound Stripe events
payment.transaction.validated  — schema + business-rule validated events
payment.transaction.scored     — ML-scored events (risk_score attached)
payment.ledger.entry           — double-entry bookkeeping triggers
payment.reconciliation.queue   — nightly batch feed for Airflow
payment.alert.triggered        — high-risk / flagged transactions
payment.dlq                    — dead letter queue (all failures)

Topic config (local dev): 3 partitions, replication-factor=1
Consumer groups: use service name (e.g. validation-service, spark-feature-engine)
Manual offset commit ONLY — never auto-commit

## Idempotency Strategy (LOCKED)
Redis key: idempotency:{stripe_event_id}:{event_type}
Operation: SET NX EX 86400 (atomic, 24h TTL)
If key EXISTS: return 200 immediately, skip processing, log duplicate
If key MISSING: process → then SET key via Lua script (atomicity)
Rate limiting: INCR rate_limit:{merchant_id}:{minute_bucket} → 429 if > 100/min

## DLQ Contract (LOCKED)
Every DLQ message must include:
original_topic, original_offset, failure_reason (enum: SCHEMA_INVALID | IDEMPOTENCY_COLLISION | ML_TIMEOUT | LEDGER_WRITE_FAIL | UNKNOWN)
retry_count, first_failure_ts, payload (original message verbatim)
All DLQ consumers must be idempotent.

## Payment State Machine (LOCKED)
INITIATED → VALIDATED → SCORING → AUTHORIZED (risk < 0.7) → SETTLED
→ FLAGGED (risk >= 0.7) → MANUAL_REVIEW
Any state → FAILED → TERMINAL (unrecoverable error → DLQ)
Persisted in: PostgreSQL payment_state_log (append-only, no UPDATE/DELETE)

## Ledger Rules (LOCKED)
ledger_entries table: append-only (no UPDATE, no DELETE)
Every SETTLED transaction = exactly 2 entries (1 DEBIT + 1 CREDIT)
DB trigger enforces: SUM(amount_cents) per transaction_id = 0
amount_cents: BIGINT, always positive, stored in cents (USD only in MVP)

## ML Risk Score Contract (LOCKED)
Input features: [tx_velocity_1m, tx_velocity_5m, amount_zscore, merchant_risk_score, device_switch_flag, hour_of_day, weekend_flag, amount_cents_log]
Output: { risk_score: float[0,1], is_high_risk: bool (>= 0.7), manual_review: bool (>= 0.85) }
Fallback if Redis timeout > 20ms: use defaults + manual_review=true
SLA: p99 < 100ms local, < 50ms on GCP

## dbt Models (In Build Order)
Staging: stg_transactions, stg_ledger_entries
Dimensions: dim_merchants, dim_users
Facts: fact_transactions, fact_ledger_balanced
Marts: reconciliation_summary, fraud_metrics, hourly_payment_volume, merchant_performance
Custom test: assert_ledger_balanced (debit = credit per transaction_id)

## Coding Rules
Never use print() — use structlog for all logging
All external calls wrapped in try/except with explicit error types
All Pydantic models in models/ — no inline schemas in routes
All Kafka consumers use manual offset commit
Tests required before marking any sub-task done
No hardcoded credentials — python-dotenv for all secrets
PowerShell compatible scripts only (Windows 11 dev environment)
Spark checkpoint dir = GCS bucket, never /tmp

## Naming Conventions
Python: snake_case functions/vars, PascalCase classes
DB tables: snake_case, plural (payment_transactions, ledger_entries)
Redis keys: prefix:identifier (idempotency:xxx, rate_limit:xxx, feat:xxx)
Kafka consumer groups: service-name (e.g. validation-service)
dbt models: stg_ (staging), dim_ (dimension), fact_ (fact), no prefix (mart)
Environment vars: SCREAMING_SNAKE_CASE

## Environment Setup (Local)
# Copy and fill in
cp .env.example .env
# Start local services
docker-compose up -d   # Kafka, Redis, PostgreSQL, Spark, Prometheus, Grafana
# FastAPI dev server
uvicorn services.main:app --reload --port 8000
# ML scoring service
uvicorn services.ml_service:app --reload --port 8001
# Airflow
airflow standalone   # after: airflow db migrate

## Current Build Status
UPDATE THIS AFTER EVERY SESSION. Format: [Status] Description — Date

M1: Foundation + Ingestion — DONE 2026-03-22
  ✅ Folder scaffold (payment-backend/ full structure)
  ✅ Docker Compose: Zookeeper, Kafka, Redis, PostgreSQL, Prometheus, Grafana, webhook-service
  ✅ All 7 Kafka topics created via kafka-init container
  ✅ FastAPI webhook service with lifespan, Prometheus instrumentation
  ✅ POST /webhook: Stripe HMAC verification, Redis idempotency (Lua SET NX EX 86400), Kafka publish
  ✅ 5 unit tests passing (.venv, pytest-asyncio)
M2: Validation Layer + State Machine — TODO
M3: Spark Feature Engineering — TODO
M4: ML Risk Scoring Service — TODO
M5: Financial Ledger — TODO
M6: Reconciliation + Airflow — TODO
M7: BigQuery + dbt — TODO
M8: Dashboard + Monitoring — TODO
M9: Feature Replay Engine — TODO
M10: GCP Deploy + CI/CD — TODO

## Known Gotchas
ADD TO THIS AS YOU DISCOVER ISSUES. This is the most valuable section over time.

Redis must be healthy before FastAPI starts — add health check in Docker Compose
Kafka consumer group names must be unique per service instance in Docker
Spark checkpoint dir must be on GCS (persistent), not /tmp (lost on restart)
Stripe test webhooks need the Stripe CLI running locally to forward events
Pydantic v2 has breaking changes from v1 — use model_validator, not validator
PostgreSQL Alembic: always run migrations in a transaction
Zookeeper health check: use `cub zk-ready localhost:2181 30` — nc (netcat) is not available in the Confluent image
Stripe SDK v9+: exception is stripe.error.SignatureVerificationError (singular), not stripe.errors (plural)
Docker container name conflicts on re-run: always `docker rm -f <names>` before `docker-compose up -d` if containers exist from a previous failed run
requirements.txt got overwritten by a linter with docker-compose content — always verify after any tool writes to it
Repo must be git-initialized before architecture review step (git diff) — run `git init && git add . && git commit` at end of first session

## Session Protocol
Start: run /status in Claude Code to check budget
Open with: "Working on M[X] — today's sub-task: [specific task]. See CLAUDE.md."
Use /compact before switching sub-tasks
End: update Current Build Status + Known Gotchas + commit CLAUDE.md