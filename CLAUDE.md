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
M2: Validation Layer + State Machine — DONE 2026-03-24 (GSD milestone v1.1 archived)
  ✅ Phase 2: Kafka Consumer + Validation + DLQ — DONE 2026-03-23
      - 02-01: Pydantic models (ValidatedPaymentEvent, DLQMessage), validation logic, DLQ producer, 9 unit tests
      - 02-02: Kafka consumer poll loop, /health on port 8002, Dockerfile, docker-compose service
  ✅ Phase 3: State Machine + Rate Limiting + Downstream Publish — DONE 2026-03-24
      - 03-01: Alembic setup + payment_state_log migration (append-only DB trigger), PaymentStateMachine, MerchantRateLimiter, ValidatedEventProducer
      - 03-02: ValidationConsumer wired with D-03 locked order; merchant_id added to ValidatedPaymentEvent; Docker Compose → PostgreSQL
      - 03-03: 8 integration tests (QUAL-01): happy path, schema failure, rate-limit block, append-only enforcement, Kafka E2E
  ✅ Human UAT passed 2026-03-25: 8/8 integration tests passed, Alembic migration 001 applied, no_update_state_log + no_delete_state_log triggers verified in pg_trigger
M3: Spark Feature Engineering — DONE 2026-03-25 (GSD milestone: v1.2 — Phase 4)
  ✅ Phase 4: Spark Feature Engineering — DONE 2026-03-25
      - 04-01: feature_functions.py (Welford zscore count<3, pure transforms) + redis_sink.py (foreachBatch, all 8 ML features, TTL 3600s) + 23 unit tests
      - 04-02: feature_engineering.py (SparkSession, env validator, Kafka readStream, velocity windows → Redis, 3 concurrent writeStream queries) + 11 integration tests
      - 04-03: spark/Dockerfile (python:3.11-slim + openjdk-21 + pyspark pip, Kafka JAR), docker-compose spark-feature-engine (port 4040, named spark_checkpoints volume) + 3 E2E tests
  ✅ 37 tests total — 23 unit + 11 integration + 3 E2E (all pass, JVM-dependent skip gracefully)
  ✅ Verification: 18/18 must-haves, all FEAT-01–FEAT-12 requirements covered
  ✅ Human UAT passed 2026-03-25: docker-compose build+run OK, all 8 feat:* Redis keys verified live (hour_of_day=10, amount_cents_log=8.517, merchant_risk_score=0.5 default)
M4: ML Risk Scoring Service — DONE 2026-03-26 (GSD milestone: v1.3 — Phase 5)
  ✅ Phase 5: ML Risk Scoring — DONE 2026-03-26
      - 05-01: XGBoostScorer + FeatureVector/RiskScore/ScoredPaymentEvent models + train.py + model.ubj (38KB) + 13 unit tests
      - 05-02: ScoringConsumer (Redis 3×50ms retry, fallback, state writes, Kafka publish) + AlertProducer + ScoredEventProducer + FastAPI POST /score + 34 unit tests
      - 05-03: Dockerfile.scoring-consumer (port 8003) + Dockerfile.ml-service (port 8001) + docker-compose.yml updated + 4 E2E tests
  ✅ 47 tests total — 13 unit (05-01) + 34 unit (05-02) + 4 E2E (05-03, skip if services not running)
  ✅ Human UAT passed 2026-03-26: both containers healthy, POST /score returned risk_score=0.00336, scoring-consumer logs xgboost_model_loaded, all unit tests 100% pass
M5: Financial Ledger — DONE 2026-03-27 (GSD milestone: v1.4 — Phase 6)
  ✅ Phase 6: Financial Ledger — DONE 2026-03-27
      - 06-01: LedgerEntry + ManualReviewQueueEntry ORM models + Alembic migrations 002 (manual_review_queue, append-only) + 003 (ledger_entries, append-only + DEFERRABLE balance trigger) + LedgerEntryProducer + ManualReviewRepository + ScoringConsumer wired (AUTHORIZED → ledger, FLAGGED+manual_review → queue) + 43 unit tests
      - 06-02: LedgerConsumer (reads payment.ledger.entry, writes 1 DEBIT + 1 CREDIT in single DB tx, AUTHORIZED→SETTLED state transition, DLQ on constraint violations, crash-on-OperationalError) + Dockerfile.ledger-consumer (port 8004) + docker-compose.yml updated + 12 unit + 4 E2E tests
  ✅ 128 unit tests total (all passing)
  ✅ Human UAT passed 2026-03-27: migrations 002+003 applied, all 3 DB triggers verified, balanced insert succeeded, single DEBIT correctly rejected, container healthy, 6 ledger rows written, SETTLED state transitions confirmed
  ✅ Bug fixed: LedgerConsumer missing enable.auto.offset.store=false — store_offsets() requires it (commit a875500)
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
bitnami/spark:3.5 removed from Docker Hub — use python:3.11-slim + openjdk-21-jre-headless + pyspark via pip instead
openjdk-17-jre-headless removed from Debian trixie — use openjdk-21-jre-headless (PySpark 3.5 is Java 21 compatible)
Spark foreachBatch: always guard Redis hash values against None before pipe.hset() — Spark columns null-propagate silently
UAT test messages must match Spark schema field names exactly: stripe_customer_id (not customer_id), received_at (not created_at)
Repo must be git-initialized before architecture review step (git diff) — run `git init && git add . && git commit` at end of first session
WSL2 memory limit not set by default — full stack (11 containers including Spark + Kafka JVMs) needs ~6GB RAM; set memory=8GB in C:\Users\ASUS\.wslconfig before first docker-compose up or Docker Engine will hang
Always start infra containers first (zookeeper kafka redis postgres), then build app services — avoids OOM spikes from parallel image pulls + builds
Kafka consumer using store_offsets() requires enable.auto.offset.store=False in Consumer config — missing it causes KafkaError{code=_INVALID_ARG} at every offset commit
Alembic must be run from payment-backend/ root with PYTHONPATH set: `PYTHONPATH="..." alembic -c db/alembic.ini upgrade head` — running from db/ dir fails with ModuleNotFoundError on models/
Ledger balance trigger uses accounting formula: SUM(CREDIT amounts) - SUM(DEBIT amounts) = 0 (both amounts_cents are always positive per CLAUDE.md — do NOT use negative values for CREDIT rows)

## Session Protocol
Start: run /status in Claude Code to check budget
Open with: "Working on M[X] — today's sub-task: [specific task]. See CLAUDE.md."
Use /compact before switching sub-tasks
End: update Current Build Status + Known Gotchas + commit CLAUDE.md