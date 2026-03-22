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

### Active

- [ ] Schema + business-rule validation layer consuming payment.webhook.received
- [ ] Payment state machine (INITIATED → VALIDATED → SCORING → AUTHORIZED → SETTLED / FLAGGED)
- [ ] Spark Structured Streaming feature engineering (velocity, z-score, merchant risk)
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
M1 shipped 376 Python LOC + 158 YAML LOC (534 total). All 5 unit tests pass.

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

---
*Last updated: 2026-03-22 after v1.0 milestone*
