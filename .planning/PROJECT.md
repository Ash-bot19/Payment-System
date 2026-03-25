# Payment System

## What This Is

A production-grade real-time payment processing pipeline built on Stripe webhooks, Apache Kafka, Redis, Spark, XGBoost, and PostgreSQL. It receives inbound Stripe events, validates and ML-scores them for fraud risk, maintains a double-entry financial ledger, and feeds nightly reconciliation — deployed on GCP.

## Core Value

Every payment event is reliably ingested, deduplicated, scored for fraud risk, and recorded in an auditable double-entry ledger with no data loss.

## Requirements

### Validated

- ✓ Stripe webhook receiver with HMAC signature verification — v1.0
- ✓ Redis idempotency (SET NX EX 86400, Lua atomic) preventing duplicate processing — v1.0
- ✓ All 7 Kafka topics provisioned with locked names and 3 partitions each — v1.0
- ✓ FastAPI service containerised with Docker Compose (Kafka, Redis, PostgreSQL, Prometheus, Grafana) — v1.0
- ✓ Prometheus metrics instrumentation on all HTTP routes from day one — v1.0
- ✓ Structured logging via structlog bound to event context — v1.0
- ✓ 5 unit tests covering happy path, duplicate, and invalid-signature cases — v1.0
- ✓ Schema + business-rule validation layer consuming payment.webhook.received — v1.1
- ✓ Payment state machine (INITIATED → VALIDATED/FAILED) persisted in append-only PostgreSQL payment_state_log — v1.1
- ✓ Redis rate limiting (100 req/min per merchant via INCR rate_limit:{merchant_id}:{minute_bucket}) — v1.1
- ✓ DLQ routing with full locked contract (SCHEMA_INVALID, original_topic/offset, retry_count, first_failure_ts, payload) — v1.1
- ✓ Downstream publish to payment.transaction.validated with merchant_id propagation — v1.1
- ✓ 8 integration tests covering state transitions, append-only enforcement, rate limit boundary, and Kafka E2E — v1.1
- ✓ Spark Structured Streaming pipeline: 3 concurrent queries (velocity_1m, velocity_5m, enriched features), all 8 ML features written to Redis feat:{event_id} hash — v1.2
- ✓ Welford online z-score algorithm for amount_zscore computation (cold-start safe, requires ≥3 observations) — v1.2
- ✓ Spark Dockerfile (python:3.11-slim + openjdk-21 + pyspark pip) + docker-compose service with named checkpoint volume — v1.2
- ✓ 37 tests: 23 unit (feature_functions + redis_sink) + 11 integration + 3 E2E (Redis) — v1.2
- ✓ Human UAT: all 8 feat:{event_id} Redis fields verified live against running docker-compose stack — v1.2

### Active

- [ ] XGBoost ML risk scoring service (p99 < 100ms, fallback on Redis timeout)
- [ ] Double-entry financial ledger (append-only, DB trigger enforces balanced entries)
- [ ] Apache Airflow nightly reconciliation DAG
- [ ] BigQuery + dbt transformation layer
- [ ] Streamlit observability dashboard
- [ ] GCP deployment (Cloud Run, Cloud SQL, Cloud Memorystore)
- [ ] GitHub Actions CI/CD pipeline

### Out of Scope

- Multi-currency support — USD only in MVP, currency conversion adds ledger complexity
- Live mode Stripe events — test mode only until GCP deploy is verified
- Mobile/frontend UI — pipeline is backend-only; Streamlit is ops dashboard, not customer-facing
- GraphQL API — REST FastAPI is sufficient for internal service communication

## Context

Python 3.11, Docker + Docker Compose for local dev, Windows 11 PowerShell environment.
Stack: FastAPI 0.115+ · Kafka 3.7+ · Redis 7.2+ · Spark 3.5+ · XGBoost 2.0+ · PostgreSQL 16+ · Airflow 2.9+ · BigQuery · dbt 1.8+ · Streamlit 1.35+ · Prometheus + Grafana · GCP.
v1.0 shipped 376 Python LOC + 158 YAML LOC (534 total). All 5 unit tests pass.
v1.1 shipped 40 files changed, 5,258 insertions — 2 phases, 5 plans, 11 tasks. 8 integration tests + 11 unit tests all passing (pending human UAT on live Docker stack).
v1.2 shipped Phase 04 complete — Spark feature engineering pipeline. 3 plans, 6 tasks, 37 tests (23 unit + 11 integration + 3 E2E). All 8 ML features computed and written to Redis. Human UAT passed 2026-03-25.

Last updated: 2026-03-25

## Constraints

- **Tech Stack**: Fully locked (see CLAUDE.md) — no substitutions without explicit decision
- **Kafka Topics**: Names are LOCKED — renaming requires coordinated consumer group reset across all services
- **Idempotency Strategy**: LOCKED — Redis key format, Lua script, 24h TTL non-negotiable
- **DLQ Contract**: LOCKED — all DLQ messages must include original_topic, original_offset, failure_reason enum, retry_count, first_failure_ts, payload
- **Ledger Rules**: LOCKED — append-only, 2 entries per SETTLED tx, DB trigger enforces SUM=0
- **ML Contract**: LOCKED — 8 input features, float[0,1] output, p99 < 100ms SLA
- **State Machine**: LOCKED — transitions defined, append-only payment_state_log
- **Platform**: Windows 11 PowerShell — all scripts must be PowerShell-compatible
- **Logging**: structlog only, never print()
- **Credentials**: python-dotenv only, never hardcoded

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| `confluent-kafka` over `aiokafka` | C librdkafka binding, battle-tested, better partition management | ✓ Good |
| Kafka key = `stripe_event_id` | Hash to same partition → ordering guarantee per payment | ✓ Good |
| Set Redis idempotency key AFTER Kafka publish | Crash-between-publish-and-set = at-least-once (recoverable); set-before-publish = silent drop (unrecoverable) | ✓ Good |
| `KAFKA_AUTO_CREATE_TOPICS_ENABLE: false` | Prevents auto-created topics with wrong partition/replication config | ✓ Good |
| Prometheus instrumented from M1 | Zero retroactive cost; every future milestone gets metrics free | ✓ Good |
| `cub zk-ready` for Zookeeper health check | `nc` (netcat) not available in Confluent images | ✓ Good |
| FastAPI lifespan over `@app.on_event` | `on_event` deprecated in FastAPI 0.115+ | ✓ Good |
| Pydantic v2 model contracts in `models/` | No inline schemas in routes; v2 `model_validator` not `validator` | ✓ Good |
| SQLAlchemy Core `insert()` for state machine writes | Explicit append-only semantics; no ORM session complexity | ✓ Good |
| DB-level PL/pgSQL trigger on payment_state_log | Physically immutable audit log even if application-layer guard fails | ✓ Good |
| DATABASE_URL_SYNC (psycopg2) separate from DATABASE_URL (asyncpg) | Alembic needs sync driver; FastAPI uses async driver | ✓ Good |
| merchant_id on ValidatedPaymentEvent | All downstream consumers receive merchant context without re-parsing raw Stripe payload | ✓ Good |
| Alembic migrations run at ValidationConsumer startup | Consumer owns its schema; no separate migration job or deployment step | ✓ Good |
| Rate limiting applied to payment_intent.succeeded only | Canceled/failed events have no revenue impact and should not be throttled | ✓ Good |
| UUID-keyed test rows for integration test isolation | append-only trigger blocks DELETE, so UUID-keyed rows are isolated by value | ✓ Good |
| pip-installed PySpark vs bitnami/spark base image | bitnami/spark:3.5 removed from Docker Hub during UAT; pip install bundles Spark binaries cleanly | ✓ Good |
| 3 separate writeStream queries vs one joined query | Windowed aggregations require `update` output mode, incompatible with `append` needed for per-event features | ✓ Good |
| Welford online algorithm for amount_zscore | BigQuery offline features don't exist yet; Welford gives online approximation with no external dependency | ✓ Good |
| foreachBatch + redis-py pipeline vs mapGroupsWithState | foreachBatch runs on driver with full redis-py access; mapGroupsWithState requires Spark-serializable state — unnecessary complexity | ✓ Good |
| ENV PYTHONPATH=/app in Dockerfile | spark-submit doesn't add CWD to sys.path; Dockerfile-level fix cleaner than patching application code | ✓ Good |

## Current Milestone: v1.3 ML Risk Scoring

**Goal:** Score transactions with XGBoost (p99 < 100ms SLA) by reading the 8 ML features from Redis, running inference, and publishing scored events downstream.

**Target features:**
- FastAPI ML scoring service consuming `payment.transaction.validated`
- Read `feat:{event_id}` + `velocity:1m/5m:` keys from Redis to assemble feature vector
- XGBoost inference (p99 < 100ms, Redis fallback → manual_review=true on timeout)
- Publish to `payment.transaction.scored` and `payment.alert.triggered`

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-25 after v1.2 milestone complete*
