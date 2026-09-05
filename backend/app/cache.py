import os

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

redis_client = redis.from_url(REDIS_URL, decode_responses=True)


def quote_key(symbol: str) -> str:
    return f"quote:{symbol}"


def enrichment_key(symbol: str) -> str:
    """Cache key for slower-changing data: day OHLC, 52-week range, analyst
    target. Refreshed far less often than the live price - see scheduler.py."""
    return f"enrichment:{symbol}"
