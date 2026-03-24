# Retrospective

## Milestone: v1.0 — Foundation + Ingestion

**Shipped:** 2026-03-22
**Phases:** 1 | **Plans:** 1

### What Was Built

- Full `payment-backend/` scaffold with 13 subdirectories
- Docker Compose: 8 services with health checks and dependency ordering
- All 7 Kafka topics provisioned via one-shot kafka-init container
- FastAPI webhook receiver: HMAC verify → Redis idempotency → Kafka publish
- Prometheus instrumentation on all routes from day one
- 5 unit tests (pytest-asyncio, mocked Redis/Kafka, ASGITransport)

### What Worked

- **Locking contracts early**: Kafka topic names, idempotency key format, DLQ contract, state machine transitions, ledger rules — all locked in CLAUDE.md before any code was written. This prevented scope creep and rework.
- **Full scaffold upfront**: Creating all 13 directories at M1 (even as placeholders) means contributors can immediately see the intended architecture without needing to read docs.
- **Prometheus from day 1**: Adding instrumentation at the start costs 3 lines; adding it retroactively to a running system requires touching every route handler.
- **Separate Pydantic models in `models/`**: Prevented inline schema drift across routes.

### What Was Inefficient

- `requirements.txt` was overwritten by a tool write — needed manual restoration. A verification step should be standard after any automated file write.
- Git was not initialized before the architecture review step — had to run `git init` mid-session. Should be step 1 of any new project session.
- No integration test layer yet — unit tests mock Redis/Kafka entirely, so a real end-to-end flow hasn't been validated in a running container.

### Patterns Established

- **Known Gotchas section in CLAUDE.md**: Every bug found gets documented immediately so it can't be rediscovered. This is the highest-ROI section of CLAUDE.md over time.
- **Session Protocol**: start with /status, open with context statement, /compact between sub-tasks, end with CLAUDE.md update + commit.
- **`cub zk-ready` for Confluent health checks**: `nc` is not available in any Confluent image.
- **Stripe SDK v9+**: Exception is `stripe.error.SignatureVerificationError` (singular), not `stripe.errors`.

### Key Lessons

1. Lock cross-service contracts (Kafka topics, Redis key formats, DLQ schema) before writing any consumer — changing them later requires coordinated resets across multiple services.
2. Redis must be healthy before FastAPI starts — enforce via `depends_on: condition: service_healthy`, not just `depends_on`.
3. The idempotency key ordering matters: check → publish → set (not set → publish → check). The current order tolerates at-least-once; the reversed order silently drops events on publish failure.
4. `KAFKA_AUTO_CREATE_TOPICS_ENABLE: false` is non-negotiable — auto-created topics get default configs that break partition ordering guarantees.
5. Always verify file contents after automated tool writes, especially `requirements.txt`.

### Cost Observations

- Sessions: 1
- Notable: Single-session M1 completion; test-first discipline (5 tests before marking done) added minimal overhead but caught the Stripe exception namespace bug before runtime.

---

## Milestone: v1.1 — Validation + State Machine

**Shipped:** 2026-03-24
**Phases:** 2 (Phase 2 + Phase 3) | **Plans:** 5 | **Tasks:** 11

### What Was Built

- Pydantic v2 validation models (`ValidatedPaymentEvent`, `DLQMessage`), schema + business-rule `validate_event()`, DLQProducer with 3-retry backoff, 11 unit tests
- Synchronous `ValidationConsumer` Kafka poll loop with manual offset commit, DLQ routing, threaded `/health` on port 8002, Docker Compose service
- Alembic migration: `payment_state_log` table with PL/pgSQL append-only triggers (UPDATE/DELETE blocked)
- `PaymentStateMachine`, `MerchantRateLimiter` (Redis INCR), `ValidatedEventProducer` — all independently injectable and testable
- `ValidationConsumer` wired in D-03 locked order: rate limit → INITIATED write → validate → VALIDATED/FAILED write → publish/DLQ → offset commit
- `merchant_id` propagated from raw event through to `ValidatedPaymentEvent` and all downstream consumers
- 8 integration tests covering state transitions, append-only enforcement, rate limit boundary (100/101), and Kafka E2E

### What Worked

- **Locking the D-03 processing order early**: Explicitly naming the operation sequence (rate limit → INITIATED → validate → transition write → publish → commit) prevented ordering bugs that are very hard to debug after the fact.
- **Injectable classes over singletons**: `PaymentStateMachine(db_engine)`, `MerchantRateLimiter(redis_client)` — passing dependencies as constructor args made integration tests trivial (no monkeypatching required).
- **DB-level trigger as belt-and-suspenders**: Application-layer INSERT-only semantics are good; PL/pgSQL trigger that raises on UPDATE/DELETE is physically immutable — even a rogue SQL client can't corrupt the audit log.
- **UUID-keyed test isolation without DELETE**: Since DELETE is blocked by the trigger, using UUID-keyed `transaction_id` values per test run achieves full isolation without needing teardown.
- **crash-on-exhaustion for DLQ**: Re-raising `KafkaException` after 3 retries lets Docker restart the consumer and replay the message — silent drops are impossible.

### What Was Inefficient

- **CLAUDE.md "Next" pointer left under wrong milestone**: After Phase 3, the `📋 Next` line was left under M2 instead of being moved to M3. Caught by user review. Should be part of the phase-close checklist.
- **CLAUDE.md not updated by execute-phase automation**: The GSD workflow updates ROADMAP.md/STATE.md automatically but not CLAUDE.md. Requires manual update after every phase — a gotcha to remember.

### Patterns Established

- **DATABASE_URL_SYNC vs DATABASE_URL**: Alembic needs a sync psycopg2 URL; FastAPI uses async asyncpg. Always define both in `.env`.
- **Consumer owns its migrations**: `_run_migrations()` at ValidationConsumer startup — no separate migration deployment step.
- **Rate limiting scoped to revenue events only**: `payment_intent.succeeded` events are rate-limited; canceled/failed events are not (no revenue impact).
- **AdminClient for Kafka test topics**: Auto-create is disabled in docker-compose; use `confluent_kafka.admin.AdminClient` to create UUID-suffixed test topics before each E2E test.

### Key Lessons

1. The append-only trigger is not just an application pattern — it must be enforced at the DB level. Application bugs, DBA mistakes, or tooling can bypass application logic.
2. `merchant_id` propagation belongs on the event model, not extracted per-consumer. Consumers should not need to re-parse the raw Stripe payload.
3. Integration tests that hit real infrastructure (PostgreSQL, Redis, Kafka) are worth the setup cost — they caught the `DATABASE_URL_SYNC` issue that mocked tests would never surface.
4. The D-03 processing order (rate limit before state write, state write before validation, offset commit last) is the correct sequence for exactly-once-effect semantics in a best-effort system.

### Cost Observations

- Sessions: 2
- Notable: All 3 Phase 3 plans executed as strict sequential waves (03-01 → 03-02 → 03-03) due to hard dependency chain; no parallelization was possible. Phase 2 had 2 sequential plans for the same reason.

---

## Cross-Milestone Trends

| Milestone | Phases | LOC Added | Sessions | Bugs Found |
|-----------|--------|-----------|----------|------------|
| v1.0 Foundation + Ingestion | 1 | ~534 | 1 | 5 |
| v1.1 Validation + State Machine | 2 | ~5,258 | 2 | 2 (CLAUDE.md pointer, DATABASE_URL_SYNC) |
