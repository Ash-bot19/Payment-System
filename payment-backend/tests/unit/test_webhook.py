import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe
from httpx import AsyncClient, ASGITransport

from services.main import app


def _make_stripe_event(event_id: str = "evt_test_123", event_type: str = "payment_intent.created") -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "object": "event",
        "data": {"object": {}},
        "created": int(time.time()),
        "livemode": False,
    }


def _signed_payload(secret: str, payload: dict) -> tuple[bytes, str]:
    """Return (raw_body, stripe-signature header) for a fake signed event."""
    body = json.dumps(payload).encode()
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.{body.decode()}"
    import hmac, hashlib
    sig = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    header = f"t={timestamp},v1={sig}"
    return body, header


@pytest.fixture()
def mock_app_state():
    """Attach mock Redis and Kafka to app.state before each test."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)          # not a duplicate
    mock_redis.eval = AsyncMock(return_value=1)            # SET NX succeeded

    mock_producer = MagicMock()
    mock_producer.publish = MagicMock()

    app.state.redis = mock_redis
    app.state.kafka_producer = mock_producer
    app.state.webhook_secret = "whsec_test_secret"
    return mock_redis, mock_producer


@pytest.mark.asyncio
async def test_valid_webhook_accepted(mock_app_state) -> None:
    mock_redis, mock_producer = mock_app_state
    secret = "whsec_test_secret"
    event = _make_stripe_event()

    with patch("stripe.Webhook.construct_event", return_value=event):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/webhook",
                content=json.dumps(event).encode(),
                headers={"stripe-signature": "t=1,v1=fake"},
            )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    mock_producer.publish.assert_called_once()


@pytest.mark.asyncio
async def test_duplicate_event_returns_200_and_skips_kafka(mock_app_state) -> None:
    mock_redis, mock_producer = mock_app_state
    mock_redis.get = AsyncMock(return_value="1")           # simulate already seen
    event = _make_stripe_event()

    with patch("stripe.Webhook.construct_event", return_value=event):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/webhook",
                content=json.dumps(event).encode(),
                headers={"stripe-signature": "t=1,v1=fake"},
            )

    assert response.status_code == 200
    assert response.json()["status"] == "duplicate"
    mock_producer.publish.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_signature_returns_400(mock_app_state) -> None:
    with patch(
        "stripe.Webhook.construct_event",
        side_effect=stripe.error.SignatureVerificationError("bad sig", "header"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/webhook",
                content=b"{}",
                headers={"stripe-signature": "t=1,v1=bad"},
            )

    assert response.status_code == 400
