"""Shared pytest fixtures for Phase 3 integration tests.

Provides:
- db_engine (session scope): SQLAlchemy engine + Alembic migrations
- db_connection (function scope): per-test connection for direct queries
- redis_client (session scope): Redis connection with ping check
- rate_limiter (function scope): MerchantRateLimiter pointing at real Redis
- clean_rate_limit_keys (function scope): cleanup Redis rate_limit:test_* keys
"""

import os

import pytest
from redis import Redis
from sqlalchemy import create_engine, text


@pytest.fixture(scope="session")
def db_engine():
    """Create SQLAlchemy engine and run Alembic migrations to head.

    Session-scoped so migrations run once per test session.
    Yields engine; disposes on teardown.
    """
    url = os.getenv(
        "DATABASE_URL_SYNC",
        "postgresql://payment:payment@localhost:5432/payment_db",
    )
    engine = create_engine(url)

    # Run Alembic migrations to ensure schema exists
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(
        os.path.join(os.path.dirname(__file__), "..", "..", "db", "alembic.ini")
    )
    alembic_cfg.set_main_option(
        "script_location",
        os.path.join(os.path.dirname(__file__), "..", "..", "db", "migrations"),
    )
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(alembic_cfg, "head")

    yield engine
    engine.dispose()


@pytest.fixture
def db_connection(db_engine):
    """Per-test SQLAlchemy connection for querying state log rows.

    Each test generates unique transaction_ids so no isolation teardown
    is needed — test rows are isolated by key, not by transaction.
    Yields connection; closes on teardown.
    """
    connection = db_engine.connect()
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def redis_client():
    """Redis client pointing at localhost:6379.

    Session-scoped — one connection shared across all integration tests.
    Pings on startup to fail fast if Redis is not running.
    Yields client; closes on teardown.
    """
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    client = Redis.from_url(url, decode_responses=True)
    client.ping()  # fail fast if Redis is not running
    yield client
    client.close()


@pytest.fixture
def rate_limiter(redis_client):
    """MerchantRateLimiter fixture pointing at real Redis.

    Function-scoped so each test gets a fresh instance (no state carried over
    from previous test's in-memory state — Redis keys are the truth).
    """
    from services.rate_limiter import MerchantRateLimiter

    return MerchantRateLimiter(redis_client)


@pytest.fixture
def clean_rate_limit_keys(redis_client):
    """Delete rate_limit:test_* keys before and after each rate limit test.

    Use this fixture in tests that drive rate limiting to prevent key
    accumulation across test runs in the same minute bucket.
    """
    keys = redis_client.keys("rate_limit:test_*")
    if keys:
        redis_client.delete(*keys)
    yield
    keys = redis_client.keys("rate_limit:test_*")
    if keys:
        redis_client.delete(*keys)
