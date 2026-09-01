"""Shared synchronous Redis connection for RQ.

RQ itself is sync (jobs run inside a worker's `Connection`/`Worker`
machinery, not asyncio), so this is deliberately the `redis.Redis` sync
client, not an async one. Every module in this package (queues, the
dedup lock, job-status helpers) shares one connection via this
lru_cache'd getter — same one-process-wide-singleton shape as
`database/session.py`'s `get_engine`.
"""

from functools import lru_cache

import redis

from core.config.settings import get_settings


@lru_cache
def get_redis_connection() -> redis.Redis:
    settings = get_settings()
    return redis.Redis.from_url(settings.redis_url)
