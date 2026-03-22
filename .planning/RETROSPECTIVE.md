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

## Cross-Milestone Trends

| Milestone | Phases | LOC Added | Sessions | Bugs Found |
|-----------|--------|-----------|----------|------------|
| v1.0 Foundation + Ingestion | 1 | ~534 | 1 | 5 |
