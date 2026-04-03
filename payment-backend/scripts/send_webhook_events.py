"""Synthetic webhook event sender — Phase 11 E2E demo.

Sends N signed Stripe-format payment_intent.succeeded events to the
deployed webhook-service Cloud Run URL. Useful for Loom demo step 1
and for ad-hoc load testing the deployed pipeline.

Usage:
    cd payment-backend
    python scripts/send_webhook_events.py \\
        --url https://webhook-service-uuxwmvlyea-el.a.run.app \\
        --secret whsec_xxx \\
        --count 75 \\
        --delay-ms 200

The script uses stripe.WebhookSignature to sign each event so the
webhook-service HMAC verification passes identically to live Stripe events.
"""

import argparse
import hashlib
import hmac
import json
import random
import time
import uuid
from datetime import datetime, timezone

import requests
import structlog

logger = structlog.get_logger(__name__)

# 80% payment_intent.succeeded so most events flow through the full pipeline
# 10% payment_intent.payment_failed, 10% payment_intent.canceled for variety
EVENT_TYPES = ["payment_intent.succeeded"] * 8 + [
    "payment_intent.payment_failed",
    "payment_intent.canceled",
]

MERCHANTS = ["merchant_a", "merchant_b", "merchant_c"]


def build_event(event_type: str = "payment_intent.succeeded") -> dict:
    """Build a Stripe-format webhook event payload.

    Mirrors the fields read by validation-consumer downstream:
    - data.object.id        → used as transaction_id
    - data.object.amount    → amount_cents
    - data.object.metadata.merchant_id → merchant_id
    - data.object.metadata.device_id   → device fingerprint (future use)
    """
    pi_id = f"pi_{uuid.uuid4().hex[:24]}"
    amount = random.randint(500, 50_000)  # $5–$500 in cents
    merchant_id = random.choice(MERCHANTS)

    return {
        "id": f"evt_{uuid.uuid4().hex[:24]}",  # unique per event for idempotency key
        "object": "event",
        "api_version": "2024-04-10",
        "created": int(datetime.now(tz=timezone.utc).timestamp()),
        "type": event_type,
        "livemode": False,
        "data": {
            "object": {
                "id": pi_id,
                "object": "payment_intent",
                "amount": amount,
                "currency": "usd",
                "status": "succeeded" if event_type == "payment_intent.succeeded" else "canceled",
                "customer": f"cus_{uuid.uuid4().hex[:14]}",
                "metadata": {
                    "merchant_id": merchant_id,
                    "device_id": f"device_{random.randint(1, 100)}",
                },
                "created": int(datetime.now(tz=timezone.utc).timestamp()),
            }
        },
    }


def sign_payload(payload_bytes: bytes, secret: str) -> str:
    """Generate a valid Stripe-Signature header value.

    Stripe signature format: t={timestamp},v1={hmac_sha256}
    The signed string is: "{timestamp}.{raw_payload_body}"
    The secret must be the raw value (not base64) — whsec_ prefix is fine,
    stripe.Webhook.construct_event strips it internally.
    """
    timestamp = int(time.time())
    signed_string = f"{timestamp}.{payload_bytes.decode('utf-8')}"

    mac = hmac.new(
        secret.encode("utf-8"),
        signed_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return f"t={timestamp},v1={mac}"


def send_events(url: str, secret: str, count: int, delay_ms: int = 100) -> None:
    """POST `count` signed webhook events to the deployed webhook-service.

    Args:
        url:      Base URL of the webhook-service (e.g. https://webhook-service-xxx.run.app)
        secret:   STRIPE_WEBHOOK_SECRET — the same value stored in Secret Manager
        count:    Number of events to send
        delay_ms: Milliseconds to sleep between events (avoids rate limiting)
    """
    endpoint = url.rstrip("/") + "/webhook"
    success = 0
    duplicate = 0
    failed = 0

    log = logger.bind(endpoint=endpoint, total=count)
    log.info("send_starting")

    for i in range(count):
        event_type = random.choice(EVENT_TYPES)
        event = build_event(event_type)
        payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
        sig_header = sign_payload(payload, secret)

        try:
            resp = requests.post(
                endpoint,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Stripe-Signature": sig_header,
                },
                timeout=10,
            )

            if resp.status_code == 200:
                body = resp.json()
                # DuplicateEventResponse has status="duplicate"; WebhookAcceptedResponse has stripe_event_id
                if body.get("status") == "duplicate":
                    duplicate += 1
                    logger.debug("event_duplicate", index=i + 1, event_id=event["id"])
                else:
                    success += 1
                    logger.info(
                        "event_sent",
                        index=i + 1,
                        event_id=event["id"],
                        event_type=event_type,
                    )
            else:
                failed += 1
                logger.warning(
                    "event_rejected",
                    index=i + 1,
                    event_id=event["id"],
                    status=resp.status_code,
                    body=resp.text[:300],
                )

        except requests.exceptions.Timeout:
            failed += 1
            logger.error("event_timeout", index=i + 1, event_id=event["id"])
        except Exception as exc:
            failed += 1
            logger.error("event_error", index=i + 1, event_id=event["id"], error=str(exc))

        if delay_ms > 0 and i < count - 1:
            time.sleep(delay_ms / 1000)

    logger.info(
        "send_complete",
        total=count,
        success=success,
        duplicate=duplicate,
        failed=failed,
        success_rate=f"{success / count * 100:.1f}%",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send synthetic Stripe webhook events to the deployed webhook-service"
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Webhook-service base URL (e.g. https://webhook-service-xxx.run.app)",
    )
    parser.add_argument(
        "--secret",
        required=True,
        help="STRIPE_WEBHOOK_SECRET value (whsec_... or raw)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=50,
        help="Number of events to send (default: 50)",
    )
    parser.add_argument(
        "--delay-ms",
        type=int,
        default=100,
        help="Delay between events in milliseconds (default: 100)",
    )
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ]
    )

    send_events(
        url=args.url,
        secret=args.secret,
        count=args.count,
        delay_ms=args.delay_ms,
    )


if __name__ == "__main__":
    main()
