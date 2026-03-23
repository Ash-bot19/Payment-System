# Phase 3: State Machine + Rate Limiting + Downstream Publish — Context

**Gathered:** 2026-03-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Persist payment state transitions to PostgreSQL (`payment_state_log`), apply Redis rate limiting per merchant, write FAILED transitions for rejected or rate-limited events, and publish validated events to `payment.transaction.validated`. Phase 3 extends the validation consumer — it does not start a new service. Phase 3 ends when a valid event is published to `payment.transaction.validated` and state is VALIDATED. ML scoring is Phase 4+.

</domain>

<decisions>
## Implementation Decisions

### Service Architecture
- **D-01:** Single Docker container — no service split. Phase 3 extends the existing `validation-consumer` service. Two containers would add orchestration complexity (two Dockerfiles, inter-service networking) with zero benefit at this scale.
- **D-02:** Extract a `PaymentStateMachine` class — standalone, injectable, testable without Kafka. `ValidationConsumer` instantiates and calls into it. State machine logic is the most interview-worthy piece of Phase 3; a clean class lets you unit test it in isolation and explain it clearly.
- **D-03:** Processing order (locked):
  1. Read raw message from Kafka
  2. Extract `merchant_id` from raw payload
  3. Rate limit check (Redis) — if blocked → write INITIATED + FAILED to state log → commit offset → continue
  4. Write INITIATED to `payment_state_log`
  5. `validate_event(raw)` — if ValidationError → write FAILED → publish to DLQ → commit offset → continue
  6. Write VALIDATED to `payment_state_log`
  7. Publish `ValidatedPaymentEvent` to `payment.transaction.validated`
  8. Commit offset
- **D-04:** PostgreSQL session and `payment.transaction.validated` producer both initialized in `ValidationConsumer.__init__` alongside the existing Kafka consumer and DLQ producer. No separate bootstrap module — revisit if constructor exceeds ~6 dependencies.

### merchant_id Extraction
- **D-05:** Primary source: `data.object.metadata.merchant_id` from the raw Stripe payload. Fallback: `DEFAULT_MERCHANT_ID` environment variable. This is the pattern real payment systems use — Stripe metadata is how custom fields attach to charges.
- **D-06:** Rate limiter reads `merchant_id` from raw payload before `validate_event()` is called. Rate limiting is a pre-validation concern — a malformed event can still be rate-limited.
- **D-07:** Add `merchant_id: str` to `ValidatedPaymentEvent`. The model should be a complete representation of a validated payment; downstream consumers (Spark, ML) will want it.
- **D-08:** Rate limiting applies to `payment_intent.succeeded` only. "We rate limit on successful transaction volume per merchant" is a defensible, real-world design choice. Failed/canceled events do not count toward the bucket.

### PostgreSQL State Machine
- **D-09:** `payment_state_log` table created via Alembic migration. Alembic only — no init SQL scripts. Migration history is interview-worthy; a mounted SQL file is not.
- **D-10:** Consumer runs `alembic upgrade head` at startup before entering the poll loop. Self-healing: every restart is safe (no-op if already at head). The objection — "migrations shouldn't run in application code" — is a production concern, not a portfolio concern.
- **D-11:** DB-level trigger enforces append-only on `payment_state_log` — raises an exception on any UPDATE or DELETE. One extra migration, significant credibility. "The audit log is physically immutable at the database layer" is a strong interview sentence.
- **D-12:** `payment_state_log` schema (minimum): `id BIGSERIAL PK`, `transaction_id TEXT NOT NULL`, `from_state TEXT`, `to_state TEXT NOT NULL`, `event_id TEXT NOT NULL`, `merchant_id TEXT NOT NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`. Append-only: no UPDATE, no DELETE — enforced at DB layer (D-11).
- **D-13:** SQLAlchemy Core (not ORM) for state writes — two `INSERT` statements per valid event, straightforward and explicit. No ORM session complexity for append-only writes.

### Redis Rate Limiting
- **D-14:** Key format (LOCKED): `rate_limit:{merchant_id}:{minute_bucket}` where `minute_bucket = int(time.time() // 60)`.
- **D-15:** Operation (LOCKED): `INCR key` then `EXPIRE key 120` (2-minute TTL for safety margin). If result > 100 → rate limited.
- **D-16:** Rate limiter implemented as an injectable class (e.g. `MerchantRateLimiter`) — not logic buried in `_process_message`. Required for direct unit testing (see D-20).
- **D-17:** Rate-limited events write INITIATED → FAILED to `payment_state_log` and commit offset. They do NOT go to the DLQ — rate limiting is an expected operational condition, not a failure. No Kafka publish.

### Downstream Publish
- **D-18:** `payment.transaction.validated` producer mirrors `WebhookProducer` pattern — same `confluent-kafka` Producer, same `_delivery_report` callback, same `flush(timeout=5)`. Partition key: `stripe_event_id`.
- **D-19:** Publish happens after both state writes succeed and before offset commit. If publish fails → crash (Docker restarts, message replayed). Silent drops are not acceptable.

### Integration Tests (QUAL-01)
- **D-20:** Tests assume `docker-compose up` services are already running — no testcontainers. Local dev portfolio project; zero startup overhead; same environment you develop against.
- **D-21:** Kafka isolation per test: share the broker, generate UUID-suffixed topic names per test (e.g. `payment.transaction.validated.{uuid4().hex[:8]}`). Zero cross-contamination, no restart overhead.
- **D-22:** Rate limit test: instantiate `MerchantRateLimiter` directly, point at real Redis, call 101 times with same merchant_id in same minute bucket, assert the 101st returns rejected. Forces injectable design, faster and more precise than routing 101 messages through Kafka.
- **D-23:** DB isolation: transaction rollback fixture per test function. `payment_state_log` is append-only so shared DB state is messy; transaction rollback is the correct mechanism and a standard `pytest` pattern.

</decisions>

<specifics>
## Specific Ideas

- `PaymentStateMachine` should have a single public method: `process(raw_payload, kafka_msg) -> ProcessingResult` where `ProcessingResult` carries enough info for `ValidationConsumer` to decide whether to publish downstream. Clean, testable, explainable.
- The append-only DB trigger is one extra Alembic migration but pays dividends in every interview. Two lines of PL/pgSQL — worth doing.
- `MerchantRateLimiter.is_rate_limited(merchant_id) -> bool` is the cleanest interface. Returns True = blocked, False = allow. No side effects beyond the Redis INCR.
- The `DEFAULT_MERCHANT_ID` env var fallback means local testing works without populating Stripe metadata. Set it to `"test_merchant"` in `.env.example`.
- Offset commit only after all writes + publish succeed. Any failure before commit = message replayed on restart = exactly-once semantics at the application layer.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Locked contracts (non-negotiable)
- `CLAUDE.md` §Payment State Machine — transitions: INITIATED → VALIDATED / FAILED; table is append-only
- `CLAUDE.md` §Kafka Topics — `payment.transaction.validated` is the locked downstream topic
- `CLAUDE.md` §Idempotency Strategy — Redis key format reference
- `CLAUDE.md` §Coding Rules — structlog only, Pydantic v2, manual offset commit, no hardcoded credentials

### Phase requirements
- `.planning/REQUIREMENTS.md` §SM-01, SM-02, SM-03, SM-04, RATELIMIT-01, QUAL-01

### Existing code (read before implementing)
- `payment-backend/kafka/consumers/validation_consumer.py` — Phase 3 extends this class; understand `_process_message` before modifying
- `payment-backend/kafka/consumers/validation_logic.py` — `validate_event()` is called inside the new processing order (D-03)
- `payment-backend/models/validation.py` — `ValidatedPaymentEvent` gets a new `merchant_id` field; `DLQMessage` unchanged
- `payment-backend/kafka/producers/webhook_producer.py` — mirror this pattern for the `payment.transaction.validated` producer
- `payment-backend/kafka/producers/dlq_producer.py` — mirror this pattern too; both producers follow the same structure

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `DLQProducer` (`kafka/producers/dlq_producer.py`): new `ValidatedEventProducer` should mirror this exactly — same `Producer` config, same `_delivery_report`, same `flush(timeout=5)`, same `close()`.
- `ValidationConsumer.__init__` already initializes `_consumer` + `_dlq_producer`. Phase 3 adds `_db_session`, `_validated_producer`, `_rate_limiter`, `_state_machine` — all in the same `__init__` block.
- `validate_event()` returns `ValidatedPaymentEvent` or raises `ValidationError` — Phase 3 adds `merchant_id` to `ValidatedPaymentEvent` but the function signature is unchanged.

### Integration Points
- Consumer writes to: `payment_state_log` (PostgreSQL, new)
- Consumer reads from: Redis for rate limiting (existing Redis service in docker-compose)
- Consumer publishes to: `payment.transaction.validated` (new producer, existing Kafka)
- Consumer still publishes failures to: `payment.dlq` (unchanged)
- Alembic migration target: `payment-backend/db/migrations/` (existing `migrations/` directory under `db/`)

### New Files Expected
- `payment-backend/models/state_machine.py` — `PaymentState` enum, `PaymentStateLogEntry` SQLAlchemy model
- `payment-backend/services/state_machine.py` — `PaymentStateMachine` class
- `payment-backend/services/rate_limiter.py` — `MerchantRateLimiter` class
- `payment-backend/kafka/producers/validated_event_producer.py` — mirrors DLQProducer
- `payment-backend/db/migrations/versions/001_create_payment_state_log.py` — Alembic migration + append-only trigger
- `payment-backend/tests/integration/test_phase3_integration.py` — QUAL-01 coverage

</code_context>

<deferred>
## Deferred Ideas

- Separate `alembic-init` container in Docker Compose — right pattern for production Kubernetes, deferred until GCP deploy (Phase 11)
- testcontainers-python for CI pipelines — correct approach for GitHub Actions, deferred to Phase 11 CI/CD setup
- Rate limiting for `payment_intent.payment_failed` and `payment_intent.canceled` — deferred; current design rate-limits succeeded only
- Connected account (`on_behalf_of`) merchant ID extraction — deferred; direct charges only in MVP

</deferred>

---

*Phase: 03-state-machine-rate-limiting-downstream-publish*
*Context gathered: 2026-03-23*
