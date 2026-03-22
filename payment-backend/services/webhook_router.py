from datetime import datetime, timezone

import stripe
import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from models.webhook import (
    DuplicateEventResponse,
    WebhookAcceptedResponse,
    WebhookReceivedMessage,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def receive_webhook(
    request: Request,
    stripe_signature: str = Header(..., alias="stripe-signature"),
) -> JSONResponse:
    """Stripe webhook receiver.

    Flow:
      1. Verify HMAC signature (Stripe SDK)
      2. Check Redis idempotency — return 200 immediately on duplicate
      3. Publish to payment.webhook.received Kafka topic
      4. Set Redis idempotency key (Lua atomic SET NX EX 86400)
    """
    body = await request.body()
    webhook_secret: str = request.app.state.webhook_secret

    # ── 1. Verify Stripe signature ────────────────────────────────────────────
    try:
        event = stripe.Webhook.construct_event(
            payload=body,
            sig_header=stripe_signature,
            secret=webhook_secret,
        )
    except stripe.error.SignatureVerificationError as exc:
        logger.warning("stripe_signature_invalid", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe signature",
        )
    except Exception as exc:
        logger.error("stripe_event_parse_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed webhook payload",
        )

    stripe_event_id: str = event["id"]
    event_type: str = event["type"]
    redis_key = f"idempotency:{stripe_event_id}:{event_type}"

    log = logger.bind(stripe_event_id=stripe_event_id, event_type=event_type)

    # ── 2. Idempotency check ──────────────────────────────────────────────────
    redis = request.app.state.redis
    try:
        already_seen = await redis.get(redis_key)
    except Exception as exc:
        log.error("redis_get_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cache unavailable",
        )

    if already_seen:
        log.info("duplicate_event_skipped")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=DuplicateEventResponse().model_dump(),
        )

    # ── 3. Publish to Kafka ───────────────────────────────────────────────────
    message = WebhookReceivedMessage(
        stripe_event_id=stripe_event_id,
        event_type=event_type,
        payload=dict(event),
        received_at=datetime.now(timezone.utc),
    )
    producer = request.app.state.kafka_producer
    try:
        producer.publish(
            stripe_event_id=stripe_event_id,
            message=message.model_dump(),
        )
    except Exception as exc:
        log.error("kafka_publish_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue event",
        )

    # ── 4. Mark idempotency key (SET NX EX 86400, Lua atomic) ────────────────
    _LUA_SET_NX = """
    if redis.call('EXISTS', KEYS[1]) == 0 then
        return redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2])
    else
        return nil
    end
    """
    try:
        await redis.eval(_LUA_SET_NX, 1, redis_key, "1", 86400)
    except Exception as exc:
        # Non-fatal: event is already published; log and continue.
        log.warning("redis_idempotency_set_failed", error=str(exc))

    log.info("webhook_accepted")
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=WebhookAcceptedResponse(stripe_event_id=stripe_event_id).model_dump(),
    )
