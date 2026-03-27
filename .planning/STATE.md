---
gsd_state_version: 1.0
milestone: v1.4
milestone_name: Ledger + Reconciliation
status: unknown
stopped_at: Checkpoint 07-03 Task 3 — awaiting human verification of Airflow UI + unit tests
last_updated: "2026-03-27T18:57:17.982Z"
progress:
  total_phases: 2
  completed_phases: 2
  total_plans: 5
  completed_plans: 5
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-27 after v1.3 milestone complete)

**Core value:** Every payment event is reliably ingested, deduplicated, scored for fraud risk, and recorded in an auditable double-entry ledger with no data loss.
**Current focus:** Phase 07 — reconciliation-airflow

## Current Position

Phase: 07 (reconciliation-airflow) — EXECUTING
Plan: 3 of 3

## Performance Metrics

**Velocity:**

- Total plans completed: 1
- Sessions: 1

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Foundation + Ingestion | 1 | 1 session | - |
| Phase 02-kafka-consumer-validation-dlq P01 | pre-committed | 3 tasks | 4 files |
| Phase 02-kafka-consumer-validation-dlq P02 | 4 | 2 tasks | 4 files |
| Phase 03 P01 | 186 | 2 tasks | 9 files |
| Phase 03-state-machine-rate-limiting-downstream-publish P02 | 15 | 2 tasks | 5 files |
| Phase 03-state-machine-rate-limiting-downstream-publish P03 | 2 | 2 tasks | 2 files |
| Phase 04-spark-feature-engineering P01 | 13 | 2 tasks | 8 files |
| Phase 04 P02 | 4 | 2 tasks | 4 files |
| Phase 04 P03 | 184 | 2 tasks | 4 files |
| Phase 05-ml-risk-scoring P01 | 8 | 1 tasks | 8 files |
| Phase 05 P02 | 387 | 2 tasks | 8 files |
| Phase 05-ml-risk-scoring P03 | 45 | 2 tasks | 4 files |
| Phase 06-financial-ledger P01 | 7 | 2 tasks | 11 files |
| Phase 06-financial-ledger P02 | 5 | 2 tasks | 5 files |
| Phase 07-reconciliation-airflow P01 | 12 | 1 tasks | 4 files |
| Phase 07-reconciliation-airflow P02 | 7 | 1 tasks | 3 files |
| Phase 07 P03 | 5 | 2 tasks | 6 files |

## Accumulated Context

### Decisions

See PROJECT.md Key Decisions table for full log.

Decisions affecting Phase 2 and Phase 3:

- **Kafka consumer group LOCKED** — must be `validation-service`; manual offset commit only (no auto-commit)
- **DLQ contract LOCKED** — failures must include: original_topic, original_offset, failure_reason (SCHEMA_INVALID | IDEMPOTENCY_COLLISION | ML_TIMEOUT | LEDGER_WRITE_FAIL | UNKNOWN), retry_count, first_failure_ts, payload verbatim
- **Redis rate limiting LOCKED** — key format `rate_limit:{merchant_id}:{minute_bucket}`, INCR → 429 if > 100/min
- **State machine LOCKED** — transitions: INITIATED → VALIDATED (valid), INITIATED → FAILED (invalid/rate-limited); table `payment_state_log` is append-only, no UPDATE/DELETE
- **Pydantic v2** — use `model_validator`, not `validator`; all schemas in `models/`
- **PostgreSQL Alembic** — always run migrations in a transaction
- [Phase 02]: validate_event raises ValidationError (not returns optional) — unambiguous failure path for DLQ routing
- [Phase 02]: Amount positivity enforced for payment_intent.succeeded only (D-12); canceled/failed events may have amount=0
- [Phase 02]: DLQProducer crash-on-exhaustion (re-raise KafkaException) preferred over silent drop — Docker restart replays message per D-17
- [Phase 02-kafka-consumer-validation-dlq]: Threaded http.server for /health endpoint — zero-dependency health check, avoids uvicorn for pure consumer service
- [Phase 02-kafka-consumer-validation-dlq]: DLQ publish before offset commit — silent drops impossible; DLQ failure triggers crash-and-restart via Docker
- [Phase 03-01]: SQLAlchemy Core insert() for PaymentStateMachine writes — explicit append-only semantics, no ORM session complexity
- [Phase 03-01]: DB-level PL/pgSQL trigger enforces append-only on payment_state_log — physically immutable audit log
- [Phase 03-01]: DATABASE_URL_SYNC (psycopg2) separate from DATABASE_URL (asyncpg) for Alembic vs FastAPI
- [Phase 03]: merchant_id added to ValidatedPaymentEvent so all downstream consumers receive merchant context without re-parsing raw Stripe payload
- [Phase 03]: Rate-limiting applied only to payment_intent.succeeded events — canceled/failed events have no revenue impact and should not be throttled
- [Phase 03]: Alembic migrations run at ValidationConsumer startup via _run_migrations() — consumer owns its schema, no separate migration job needed
- [Phase 03]: DB isolation via unique transaction_ids per test — append-only trigger blocks DELETE so UUID-keyed rows are isolated by value
- [Phase 03]: Kafka test isolation via AdminClient-created UUID-suffixed topics — avoids auto-create restriction and guarantees zero cross-test contamination
- [Phase 04-spark-feature-engineering]: Welford count<3 threshold: need 2 prior observations for reliable variance; cold start and single-point both return 0.0
- [Phase 04-spark-feature-engineering]: feature_functions.py uses pure Python types (not PySpark Columns) — runs inside foreachBatch on driver, not distributed executors
- [Phase 04-spark-feature-engineering]: foreachBatch Redis pipeline requires explicit pipe.execute() — omitting causes silent zero writes with no error
- [Phase 04]: PySpark column expressions (F.hour, F.dayofweek, F.log1p) used for streaming transforms — pure Python feature_functions.py used inside foreachBatch on driver only
- [Phase 04]: requires_java skipif guard added to JVM-dependent Spark tests — prevents ERROR on machines without Java, tests run in bitnami/spark Docker container
- [Phase 04]: bitnami/spark:3.5 as Dockerfile base with spark-submit --packages for Kafka JAR (runtime download avoids 100MB in image)
- [Phase 04]: E2E tests use static DataFrame (no live Kafka) for CI stability; validates write_features_to_redis directly against live Redis
- [Phase 04]: requires_java guard on all Spark E2E tests — skip on local dev, run inside bitnami/spark Docker container
- [Phase 05-ml-risk-scoring]: model.ubj committed to repo (D-A1) — not a Docker volume, not a registry; zero runtime dependency on training infra
- [Phase 05-ml-risk-scoring]: crash-and-exit (sys.exit(1)) for missing model at startup — distinguishes misconfiguration from runtime Redis-timeout fallback (D-A3)
- [Phase 05]: FastAPI lifespan over @app.on_event for ml_service model load — on_event deprecated in FastAPI 0.115+
- [Phase 05]: model.ubj lives at ml/models/model.ubj (not ml/model.ubj) — confirmed on filesystem during Phase 05-02 test run
- [Phase 05-ml-risk-scoring]: ML scoring pipeline containerized: scoring-consumer (8003) + ml-scoring-service (8001) via docker-compose with health checks; ML_MODEL_PATH=ml/models/model.ubj; health checks via Python urllib.request (no curl dep)
- [Phase 06-financial-ledger]: DEFERRABLE INITIALLY DEFERRED balance trigger in ledger_entries — allows DEBIT+CREDIT to be inserted in one transaction before SUM=0 is checked at commit
- [Phase 06-financial-ledger]: LedgerEntryProducer mirrors AlertProducer (retry+backoff+crash-on-exhaustion) for consistency across all Kafka producers
- [Phase 06-financial-ledger]: ManualReviewRepository uses SQLAlchemy Core insert() with engine.begin() for explicit append-only semantics
- [Phase 06-financial-ledger]: OperationalError (DB down) is re-raised (crash) not routed to DLQ — systemic failures use Docker restart + Kafka replay per D-08, D-10
- [Phase 06-financial-ledger]: Idempotency guard checks payment_state_log for existing SETTLED row — prevents duplicate DEBIT+CREDIT writes on message replay
- [Phase 07-01]: Use Literal type for discrepancy_type (not Python enum) — .model_dump() serializes to plain string, zero special-casing for Kafka JSON
- [Phase 07-01]: All 10 D-11 fields on every ReconciliationMessage with Optional=None for inapplicable fields — consistent shape for downstream consumers
- [Phase 07-01]: publish_batch() iterates sequentially and calls publish() per message — fail-fast on first KafkaException, no partial batch commits
- [Phase 07-02]: Module-level @task functions (not nested inside @dag) — makes tasks directly callable in unit tests without Airflow context
- [Phase 07-02]: _InDagContext flag in test stub: @task wrappers return MagicMock sentinel inside @dag body, run real logic when called directly in tests
- [Phase 07-02]: AMOUNT_MISMATCH sets stripe_amount_cents=None per D-11 — diff_cents = stripe - internal captures the discrepancy
- [Phase 07-03]: LocalExecutor (not CeleryExecutor) for local dev Airflow — single-node, no Redis/RabbitMQ broker overhead
- [Phase 07-03]: Shared PostgreSQL for Airflow metadata + app tables in local dev — reduces docker-compose service count, acceptable for dev
- [Phase 07-03]: DAGs baked into Airflow image via COPY (not volume mount) — deterministic image, no host path dependency

### Pending Todos

None.

### Blockers/Concerns

- Stripe test webhooks need Stripe CLI running locally to forward events (see CLAUDE.md Known Gotchas)
- PostgreSQL Alembic migrations must run in a transaction (CLAUDE.md)
- Pydantic v2: use `model_validator`, not `validator` (CLAUDE.md)
- Docker container name conflicts on re-run: always `docker rm -f` before `docker-compose up -d` if containers exist (CLAUDE.md)

## Session Continuity

Last session: 2026-03-27T18:57:01.025Z
Stopped at: Checkpoint 07-03 Task 3 — awaiting human verification of Airflow UI + unit tests
Resume file: None
