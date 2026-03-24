"""Redis-backed rate limiter for payment events — Phase 03.

Implements per-merchant rate limiting using the LOCKED key format:
  rate_limit:{merchant_id}:{minute_bucket}

Operation: INCR key → EXPIRE key 120 if count == 1 → reject if count > 100.
"""

import time

import structlog
from redis import Redis

logger = structlog.get_logger(__name__)


class MerchantRateLimiter:
    """Rate limiter that blocks merchants exceeding 100 requests per minute.

    Injectable via constructor — takes a Redis client instance so tests can
    point it at a real or mock Redis without changing class internals.

    Key format (LOCKED): rate_limit:{merchant_id}:{minute_bucket}
    minute_bucket = int(time.time() // 60)

    Returns True = blocked (caller should reject), False = allow.
    """

    RATE_LIMIT = 100  # max events per minute bucket
    KEY_TTL = 120     # 2-minute TTL for safety margin

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    def is_rate_limited(self, merchant_id: str) -> bool:
        """Check whether merchant has exceeded the per-minute rate limit.

        Increments the bucket counter and sets TTL on first call for this
        bucket. Subsequent calls within the same minute bucket reuse the
        existing key.

        Args:
            merchant_id: The merchant identifier to rate-limit against.

        Returns:
            True if the merchant is rate-limited (count > 100), else False.
        """
        minute_bucket = int(time.time() // 60)
        key = f"rate_limit:{merchant_id}:{minute_bucket}"
        count = self._redis.incr(key)
        if count == 1:
            self._redis.expire(key, self.KEY_TTL)
        is_limited = count > self.RATE_LIMIT
        if is_limited:
            logger.warning(
                "merchant_rate_limited",
                merchant_id=merchant_id,
                minute_bucket=minute_bucket,
                count=count,
            )
        return is_limited
