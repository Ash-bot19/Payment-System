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
M6: Reconciliation + Airflow — DONE 2026-03-28 (GSD milestone: v1.5 — Phase 7)
  ✅ Phase 7: Reconciliation + Airflow — DONE 2026-03-28
      - 07-01: ReconciliationMessage Pydantic v2 model (10 D-11 fields, Literal enum, run_date: date) + ReconciliationProducer (backoff [1,2,4], publish_batch) + 20 unit tests
      - 07-02: nightly_reconciliation Airflow TaskFlow DAG (@daily, catchup=False) — detect_duplicates + fetch_stripe_window parallel → compare_and_publish (MISSING_INTERNALLY, AMOUNT_MISMATCH, DUPLICATE_LEDGER) + 19 unit tests
      - 07-03: Airflow Dockerfile (apache/airflow:2.9.3-python3.11) + docker-compose 3 services (airflow-init, airflow-webserver:8080, airflow-scheduler) + requirements-airflow.txt + integration + E2E tests (skip gracefully without live stack)
  ✅ 174 tests total — 167 unit + 5 integration + 2 E2E (all passing)
  ✅ Human UAT passed 2026-03-28: Airflow UI at :8080 shows nightly_reconciliation DAG + 3 tasks; 7/7 integration+E2E tests pass against live stack
  ✅ Bug fixed: test helpers used single-row transactions — balance trigger requires DEBIT+CREDIT in same tx; replaced _insert_ledger_row with _insert_ledger_pair
  ✅ Bug fixed: sqlalchemy>=2.0 in requirements-airflow.txt broke Airflow 2.9 ORM — removed, let Airflow manage its own SQLAlchemy version
M7: BigQuery + dbt — DONE 2026-03-28 (GSD milestone: v2.0 — Phase 8)
  ✅ Phase 8: BigQuery + dbt — DONE 2026-03-28
      - 08-01: Alembic migration 004 (reconciliation_discrepancies table, append-only trigger) + ReconciliationDiscrepancy ORM model + DAG extended with persist_discrepancies + export_to_bigquery (Phase 11 placeholder) + 27 unit tests
      - 08-02: dbt project scaffold (dbt_project.yml, profiles.yml dev+prod) + all 10 models (stg_transactions, stg_ledger_entries, dim_merchants, dim_users, fact_transactions, fact_ledger_balanced, reconciliation_summary, fraud_metrics, hourly_payment_volume, merchant_performance) + sources/schema YAML + assert_ledger_balanced singular test + imbalanced_transaction seed
      - 08-03: integration tests (persist_discrepancies vs live PostgreSQL, UUID isolation) + E2E tests (dbt compile/run/test via subprocess) + GCP env vars in .env.example + dbt-core/dbt-postgres in requirements.txt
  ✅ 175 unit tests + 28 dbt tests (reported separately) — all passing
  ✅ Human UAT passed 2026-03-28: migration 004 applied, dbt debug OK, dbt run 10/10 models, dbt test 28/28 pass (assert_ledger_balanced ✓), 5/5 integration + 5/5 E2E tests pass
  ✅ Bug fixed: nightly_reconciliation.py DATABASE_URL_SYNC defaults to @postgres (Docker hostname) — run integration tests from host with DATABASE_URL_SYNC=postgresql://payment:payment@localhost:5432/payment_db
M8: Dashboard + Monitoring — DONE 2026-03-29
  ✅ Phase 9: Dashboard + Monitoring — DONE 2026-03-29
      - 09-01: seed_demo_data.py (15 txns, 68 state rows, 16 ledger rows, 5 discrepancies) + prometheus.yml ml-scoring-service target + ml_service.py Instrumentator + Grafana provisioning (datasource YAML + 2 alert rules JSON)
      - 09-02: Streamlit dashboard (4 pages: Fraud Metrics, Payment Volume, Merchant Performance, Reconciliation) + queries module + Dockerfile (PYTHONPATH=/app) + docker-compose streamlit-dashboard service port 8501 + 6 unit tests
      - 09-03: 6 E2E tests (Streamlit health + page load, Prometheus targets, Grafana datasource + alert rules) — all passing against live stack
  ✅ 175 unit + 1 skipped (dashboard, needs streamlit in venv) + 6 E2E passing
  ✅ Human UAT passed 2026-03-29: Streamlit at :8501 shows 4 pages with data, Prometheus shows both targets UP, Grafana has WebhookErrorRate + MLScoringLatencyP99 alert rules
  ✅ Bugs fixed: Grafana mount path (../monitoring/grafana → ../monitoring/grafana/provisioning); datasource must be .yaml not .json; alert folder "General" conflicts with Grafana reserved folder → renamed to "Payment System"; st.Page paths relative to app.py dir not CWD; PYTHONPATH=/app needed for dashboard package imports
M9: Feature Replay Engine — DONE 2026-03-30 (Phase 10)
  ✅ replay/feature_reconstruction.py (457 lines) — reads payment_state_log + ledger_entries, reconstructs 8 ML features
  ✅ SQL window functions for tx_velocity_1m/5m; batch retrospective z-score per merchant; device_switch_flag=0 (documented)
  ✅ --start-date / --end-date CLI via argparse; structlog; psycopg2 matching seed_demo_data.py pattern
  ✅ Redis merchant:risk:{merchant_id} lookup with 0.5 fallback when Redis unavailable
  ✅ Outputs data/feature_store/features_YYYYMMDD.parquet (4 metadata + 8 feature cols, float64)
  ✅ ml/train.py docstring updated with Parquet retrain path (D-17); .gitignore excludes data/feature_store/
  ✅ 20 tests: 16 unit + 4 integration (all pass; integration skips gracefully without PostgreSQL)
  ✅ Feature Fidelity Notes docstring documents all approximations and device_switch_flag gap (portfolio signal)
M10: GCP Deploy + CI/CD — IN PROGRESS (Phase 11, session 2026-04-03)
  ✅ 11-01: GCP Infra provisioned — Artifact Registry, Cloud SQL (172.31.0.3), Memorystore (10.101.168.91), GCS bucket, BigQuery dataset, Secret Manager (7 secrets)
  ✅ 11-02: VM kafka-vm (e2-standard-2, asia-south1-a, 10.160.0.2) + docker-compose for all 7 services + Cloud Build configs for 7 Docker images
  ✅ 11-03: 3 Cloud Run services deployed
       webhook-service:    https://webhook-service-uuxwmvlyea-el.a.run.app
       ml-scoring-service: https://ml-scoring-service-uuxwmvlyea-el.a.run.app
       streamlit-dashboard: https://streamlit-dashboard-94891977471.asia-south1.run.app
       Workload Identity Federation configured (no SA key needed)
       GitHub Actions SA: github-actions-sa@project-2f9d2775-493e-4e59-9b8.iam.gserviceaccount.com
  ✅ 11-04: export_to_bigquery implemented (google-cloud-bigquery==3.25.0) — Cloud Run Job + Cloud Scheduler NOT YET deployed (needs GCP commands next session)
  ✅ 11-05: .github/workflows/ci-cd.yml written (WIF auth, 4 jobs: test→build→staging→production) — GitHub secrets NOT YET set
  ✅ 11-06: send_webhook_events.py written — ready to run against live URL

  ⏳ NEXT SESSION — manual GCP steps required (see "Next Session Checklist" below):
    1. Re-provision: start VM + Cloud SQL, recreate Memorystore (stopped to save billing)
    2. Run Alembic migrations via IAP tunnel
    3. Build + push Airflow image → deploy Cloud Run Job + Cloud Scheduler
    4. Set 5 GitHub secrets + create staging/production environments
    5. Run E2E: send_webhook_events.py → verify all 9 pipeline steps → teardown

  GCP Project: project-2f9d2775-493e-4e59-9b8 | Region: asia-south1
  AR repo: asia-south1-docker.pkg.dev/project-2f9d2775-493e-4e59-9b8/payment-system

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
webhook-service uses localhost:9092 from .env but needs kafka:29092 inside Docker — always add environment override in docker-compose.yml for any service that env_file loads .env
webhook-service image has no curl — use python urllib for healthcheck (same as other services)
Do NOT put sqlalchemy>=2.0 in requirements-airflow.txt — Airflow 2.9 bundles SQLAlchemy 1.4 internally; overriding it breaks Airflow's ORM models (MappedAnnotationError on TaskInstance)
If Airflow DB gets into a partial migration state (e.g. after sqlalchemy version conflict), run: docker run --rm --network infra_default -e ... infra-airflow-init bash -c "airflow db reset --yes && airflow db migrate && airflow users create ..."
nightly_reconciliation.py DATABASE_URL_SYNC defaults to @postgres (Docker hostname for Airflow containers) — when running integration tests from host machine, override: `DATABASE_URL_SYNC=postgresql://payment:payment@localhost:5432/payment_db pytest ...`
Alembic version mismatch after Airflow run: Airflow writes its own hash-based alembic_version; fix with `docker exec postgres psql -U payment -d payment_db -c "UPDATE alembic_version SET version_num = '003';"` then re-run upgrade
dbt must be installed in .venv via pip: `pip install dbt-core==1.8.9 dbt-postgres==1.8.2` — not in requirements.txt by default for Docker builds (only needed for local dev + tests)

Grafana datasource provisioning requires .yaml extension — .json files are silently ignored for datasources (alerting provisioning accepts .json)
Grafana alert rules: do NOT use "folder": "General" — it's Grafana's reserved root folder and causes a startup crash; use a unique name like "Payment System"
Grafana provisioning mount: must be ../monitoring/grafana/provisioning:/etc/grafana/provisioning (not ../monitoring/grafana) — one extra level
Streamlit st.Page() paths are relative to app.py's directory, not the CWD where streamlit run is invoked
Streamlit Docker: set ENV PYTHONPATH=/app so page scripts can import the dashboard package — Streamlit adds the app.py dir to sys.path, not WORKDIR
Grafana MCP server: URL must have no trailing space — "http://localhost:3000 " (with space) causes a panic on startup
structlog.stdlib.add_logger_name processor requires a stdlib-backed logger — incompatible with structlog's default PrintLogger; remove it from processors list when using structlog.configure() without wrapper_class=stdlib

## Next Session Checklist (M10 E2E — Phase 11-06)
Run these commands in order. All require `gcloud` auth.

### Step 0 — Re-provision stopped services
```bash
# Start VM (was stopped to save billing)
gcloud compute instances start kafka-vm --zone=asia-south1-a --project=project-2f9d2775-493e-4e59-9b8

# Start Cloud SQL (was stopped to save billing)
gcloud sql instances patch payment-db --activation-policy=ALWAYS --project=project-2f9d2775-493e-4e59-9b8

# Recreate Memorystore Redis (was deleted — takes ~5 min)
gcloud redis instances create payment-redis \
  --size=1 --region=asia-south1 --redis-version=redis_7_0 \
  --network=default --tier=basic \
  --project=project-2f9d2775-493e-4e59-9b8
```
Then update Secret Manager with the new Redis host:
```bash
REDIS_HOST=$(gcloud redis instances describe payment-redis --region=asia-south1 --project=project-2f9d2775-493e-4e59-9b8 --format="value(host)")
echo -n "redis://$REDIS_HOST:6379" | gcloud secrets versions add REDIS_URL --data-file=- --project=project-2f9d2775-493e-4e59-9b8
echo -n "$REDIS_HOST" | gcloud secrets versions add REDIS_HOST --data-file=- --project=project-2f9d2775-493e-4e59-9b8
```

### Step 1 — Alembic migrations (via IAP tunnel through kafka-vm)
```bash
gcloud compute ssh ubuntu@kafka-vm --zone=asia-south1-a --project=project-2f9d2775-493e-4e59-9b8 --tunnel-through-iap --quiet \
  --command='docker exec validation-consumer bash -c "PYTHONPATH=/app DATABASE_URL_SYNC=postgresql://payment:payment@172.31.0.3:5432/payment_db alembic -c db/alembic.ini upgrade head"'
```

### Step 2 — Deploy Airflow Cloud Run Job + Cloud Scheduler
```bash
cd payment-backend
docker build -f airflow/Dockerfile -t asia-south1-docker.pkg.dev/project-2f9d2775-493e-4e59-9b8/payment-system/airflow-dag-runner:latest .
docker push asia-south1-docker.pkg.dev/project-2f9d2775-493e-4e59-9b8/payment-system/airflow-dag-runner:latest

gcloud run jobs create nightly-reconciliation \
  --image=asia-south1-docker.pkg.dev/project-2f9d2775-493e-4e59-9b8/payment-system/airflow-dag-runner:latest \
  --region=asia-south1 --project=project-2f9d2775-493e-4e59-9b8 \
  --vpc-connector=payment-connector --vpc-egress=private-ranges-only \
  --set-secrets="DATABASE_URL_SYNC=DATABASE_URL_SYNC:latest,STRIPE_API_KEY=STRIPE_API_KEY:latest" \
  --set-env-vars="GCP_PROJECT_ID=project-2f9d2775-493e-4e59-9b8,BIGQUERY_DATASET=payment_analytics,KAFKA_BOOTSTRAP_SERVERS=placeholder,PYTHONPATH=/opt/airflow,GOOGLE_CLOUD_PROJECT=project-2f9d2775-493e-4e59-9b8" \
  --command="python" \
  --args="-c,from airflow.dags.nightly_reconciliation import detect_duplicates, fetch_stripe_window, compare_and_publish, persist_discrepancies, export_to_bigquery; import datetime; ds=datetime.date.today().isoformat(); duplicates=detect_duplicates(ds); intents=fetch_stripe_window(ds); msgs=compare_and_publish(intents, ds); persist_discrepancies(msgs, ds); export_to_bigquery(ds)" \
  --memory=1Gi --cpu=1 --max-retries=1 --task-timeout=600s

# Cloud Scheduler SA
gcloud iam service-accounts create scheduler-sa --display-name="Cloud Scheduler SA" --project=project-2f9d2775-493e-4e59-9b8
gcloud projects add-iam-policy-binding project-2f9d2775-493e-4e59-9b8 \
  --member="serviceAccount:scheduler-sa@project-2f9d2775-493e-4e59-9b8.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

gcloud scheduler jobs create http nightly-reconciliation-trigger \
  --project=project-2f9d2775-493e-4e59-9b8 --location=asia-south1 \
  --schedule="0 1 * * *" --time-zone="UTC" \
  --uri="https://asia-south1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/project-2f9d2775-493e-4e59-9b8/jobs/nightly-reconciliation:run" \
  --http-method=POST \
  --oauth-service-account-email="scheduler-sa@project-2f9d2775-493e-4e59-9b8.iam.gserviceaccount.com"
```

### Step 3 — Set GitHub secrets (browser)
Go to: https://github.com/Ash-bot19/Payment-System/settings/secrets/actions
Set these 5 secrets:
- AR_REPO              = asia-south1-docker.pkg.dev/project-2f9d2775-493e-4e59-9b8/payment-system
- VM_INTERNAL_IP       = 10.160.0.2
- GCP_PROJECT_ID       = project-2f9d2775-493e-4e59-9b8
- STRIPE_WEBHOOK_SECRET = (from your .env file)
- DATABASE_URL         = postgresql+asyncpg://payment:payment@localhost:5432/payment_db

Create two Environments at: https://github.com/Ash-bot19/Payment-System/settings/environments
- staging    — no approval required
- production — require reviewer (yourself)

### Step 4 — E2E demo (send events + verify pipeline)
```bash
WEBHOOK_URL="https://webhook-service-uuxwmvlyea-el.a.run.app"
STRIPE_SECRET=$(gcloud secrets versions access latest --secret=STRIPE_WEBHOOK_SECRET --project=project-2f9d2775-493e-4e59-9b8)
cd payment-backend
python scripts/send_webhook_events.py --url "$WEBHOOK_URL" --secret "$STRIPE_SECRET" --count 75 --delay-ms 200
```
Then verify each of the 9 pipeline steps from 11-06-PLAN.md.

### Step 5 — Final teardown (after E2E)
```bash
gcloud compute instances delete kafka-vm --zone=asia-south1-a --project=project-2f9d2775-493e-4e59-9b8 --quiet
gcloud sql instances delete payment-db --project=project-2f9d2775-493e-4e59-9b8 --quiet
gcloud redis instances delete payment-redis --region=asia-south1 --project=project-2f9d2775-493e-4e59-9b8 --quiet
gcloud compute firewall-rules delete allow-kafka-internal --project=project-2f9d2775-493e-4e59-9b8 --quiet
```
Keep: Cloud Run, BigQuery, GCS, Artifact Registry, Secret Manager, Cloud Scheduler, WIF — all near-zero cost.

## Session Protocol
Start: run /status in Claude Code to check budget
Open with: "Working on M[X] — today's sub-task: [specific task]. See CLAUDE.md."
Use /compact before switching sub-tasks
End: update Current Build Status + Known Gotchas + commit CLAUDE.md