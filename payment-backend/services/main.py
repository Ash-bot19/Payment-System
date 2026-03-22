import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from kafka.producers.webhook_producer import WebhookProducer
from services.webhook_router import router as webhook_router

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # ── Startup ───────────────────────────────────────────────────────────────
    redis_url = os.environ["REDIS_URL"]
    kafka_servers = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
    webhook_secret = os.environ["STRIPE_WEBHOOK_SECRET"]

    app.state.redis = aioredis.from_url(redis_url, decode_responses=True)
    app.state.kafka_producer = WebhookProducer(bootstrap_servers=kafka_servers)
    app.state.webhook_secret = webhook_secret

    logger.info("webhook_service_started", redis_url=redis_url, kafka=kafka_servers)

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    await app.state.redis.aclose()
    app.state.kafka_producer.close()
    logger.info("webhook_service_stopped")


app = FastAPI(
    title="Payment Webhook Service",
    version="0.1.0",
    description="Stripe webhook receiver — M1",
    lifespan=lifespan,
)

Instrumentator().instrument(app).expose(app)

app.include_router(webhook_router)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/")
async def root() -> JSONResponse:
    return JSONResponse({"service": "webhook-service", "version": "0.1.0"})
