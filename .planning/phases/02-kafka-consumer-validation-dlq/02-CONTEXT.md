# Phase 2: Kafka Consumer + Validation + DLQ - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Stand up the `validation-service` Kafka consumer, validate every inbound event against schema and business rules, and route every failure to the DLQ with the full locked contract. Phase 2 ends after DLQ routing — downstream publish to `payment.transaction.validated` and state machine writes are Phase 3.

</domain>

<decisions>
## Implementation Decisions

### Consumer Runtime Architecture
- **D-01:** Separate Docker container — not a FastAPI background task. Added as a new service in `docker-compose.yml`.
- **D-02:** Synchronous confluent-kafka poll loop (no asyncio). A dedicated process is the natural fit for confluent-kafka's C binding; no uvicorn needed — entrypoint is `python -m kafka.consumers.validation_consumer`.
- **D-03:** Expose a small `/health` or `/metrics` port from the container for Prometheus scraping. Kafka consumer group lag is the primary liveness signal.
- **D-04:** `auto.offset.reset=earliest` as first-run fallback; committed offset used on restart.
- **D-05:** `restart: unless-stopped` in Docker Compose. Crash-loop visibility via Prometheus consumer group lag alerts.

### Supported Stripe Event Types
- **D-06:** Payment intents only — no charges or refunds in MVP.
- **D-07:** Allowlist is exactly three types: `payment_intent.succeeded`, `payment_intent.payment_failed`, `payment_intent.canceled`.
- **D-08:** Allowlist is a hardcoded named constant (e.g. `SUPPORTED_EVENT_TYPES`) in the validation module — no env/config indirection.
- **D-09:** Unsupported event types → `SCHEMA_INVALID` failure reason in DLQ. A TODO comment is added for a future `UNSUPPORTED_EVENT_TYPE` enum value (Phase 3 concern).

### Amount Field Extraction
- **D-10:** Amount extracted from `data.object.amount` (Stripe cents, integer).
- **D-11:** Missing amount field → fail hard → DLQ. No fallback to zero. Check: `if "amount" not in data["object"]: raise ValidationError("MISSING_AMOUNT_FIELD")`.
- **D-12:** `amount > 0` enforced **only** for `payment_intent.succeeded`. For `payment_intent.payment_failed` and `payment_intent.canceled`: validate that the field exists and is an integer, but do not enforce positivity.
- **D-13:** Negative amount → same DLQ path as zero (`SCHEMA_INVALID`), but a distinct `logger.warning("negative_amount_received", ...)` log line before raising.
- **D-14:** A new `ValidatedPaymentEvent` Pydantic model is created in `models/` with fields: `event_id: str`, `event_type: str`, `amount_cents: int`, `currency: str`, `stripe_customer_id: str`, `received_at: datetime`. All downstream consumers use this model — no raw dict key lookups.

### Offset Commit Semantics
- **D-15:** Commit sequence for **failed** events: receive → validate → fails → write to DLQ → DLQ write succeeds → commit offset. Do not commit if DLQ write fails.
- **D-16:** Commit sequence for **valid** events (Phase 2): receive → validate → passes → commit offset. Log `validation_passed_awaiting_downstream` with `event_id` and `phase: 2`.
- **D-17:** DLQ write retry policy: 3 retries with exponential backoff (1s, 2s, 4s). After all retries exhausted → `logger.critical(...)` → raise exception → consumer crashes → Docker restarts → same message replayed. No message is ever silently dropped.
- **D-18:** Per-message offset tracking: `consumer.store_offsets(message)` after each message, then a single `consumer.commit()` at the end of each poll cycle. Do not call `consumer.commit()` per-message (unnecessary broker round-trips).

</decisions>

<specifics>
## Specific Ideas

- DLQ write failure should be **loud** — crash the consumer rather than swallow the error. Crashing is the honest signal; Prometheus picks up the restart.
- The `ValidatedPaymentEvent` model is the single validation point for the entire pipeline — "one validation point, one shape, type-checked automatically."
- Batch offset optimization (`store_offsets` + single `commit()` per poll) is intentional and matches confluent-kafka's design. Revisit for batch commits when processing thousands/sec.
- Allowlist constant should be clearly named (e.g. `SUPPORTED_EVENT_TYPES: frozenset[str]`) so it's obvious it's the authoritative list.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Locked contracts (non-negotiable)
- `payment-backend/CLAUDE.md` §Kafka Topics — locked topic names, partition count, consumer group naming convention
- `payment-backend/CLAUDE.md` §Idempotency Strategy — Redis key format (referenced for context; idempotency already handled in Phase 1)
- `payment-backend/CLAUDE.md` §DLQ Contract — all 6 required DLQ fields, `failure_reason` enum values
- `payment-backend/CLAUDE.md` §Payment State Machine — state transitions (context for Phase 3; Phase 2 ends before DB writes)
- `payment-backend/CLAUDE.md` §Coding Rules — structlog only, Pydantic v2 `model_validator`, manual offset commit, no hardcoded credentials

### Phase requirements
- `.planning/REQUIREMENTS.md` §CONSUMER-01, CONSUMER-02, VALID-01, VALID-02, VALID-03, QUAL-02 — the six requirements this phase must satisfy

### Existing code (read before implementing)
- `payment-backend/kafka/producers/webhook_producer.py` — establishes the confluent-kafka producer pattern; consumer should mirror its error handling and structlog usage
- `payment-backend/models/webhook.py` — defines `WebhookReceivedMessage` (the shape of messages on `payment.webhook.received`); consumer deserializes into this model
- `payment-backend/services/main.py` — FastAPI lifespan pattern; new consumer service should follow the same startup/shutdown discipline

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `WebhookProducer` in `kafka/producers/webhook_producer.py`: DLQ producer can mirror this class — same `confluent-kafka` Producer, same `_delivery_report` callback, same `flush(timeout=5)` pattern.
- `WebhookReceivedMessage` in `models/webhook.py`: The consumer deserializes Kafka messages into this model. Phase 2 adds `ValidatedPaymentEvent` as the output model.
- structlog bound-logger pattern (`logger.bind(stripe_event_id=..., event_type=...)`) established in `webhook_router.py` — use the same pattern in the consumer for consistent log correlation.

### Established Patterns
- **Error handling**: All external calls wrapped in `try/except` with explicit error types (see `webhook_router.py`). Consumer must follow this.
- **Pydantic v2**: `model_validator` not `validator`. All models in `models/` directory, not inline.
- **confluent-kafka sync**: Producer uses synchronous `produce()` + `flush()`. Consumer should use synchronous `poll()` loop — no asyncio.
- **No `print()`**: structlog everywhere.

### Integration Points
- Consumer reads from: `payment.webhook.received` (topic locked)
- Consumer writes failures to: `payment.dlq` (topic locked)
- New Docker service connects to same Kafka broker and Redis as webhook service (same Docker network, same env vars)
- Phase 3 will wire the consumer's valid-event output to `payment.transaction.validated` — Phase 2 ends at offset commit after validation passes

</code_context>

<deferred>
## Deferred Ideas

- `UNSUPPORTED_EVENT_TYPE` as a distinct DLQ `failure_reason` enum value — flagged as Phase 3 TODO
- Batch offset commit optimization — revisit when processing thousands of messages/sec (current per-poll `store_offsets` + `commit()` is intentional for Phase 2)
- Prometheus consumer lag alerting rules — Phase 9 (Dashboard + Monitoring), but the metrics port exposure in D-03 lays the groundwork

</deferred>

---

*Phase: 02-kafka-consumer-validation-dlq*
*Context gathered: 2026-03-23*
