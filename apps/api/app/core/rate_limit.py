"""Redis-backed fixed-window rate limiting for unauthenticated,
token-issuing endpoints (login, refresh) — the endpoints a brute-force
or credential-stuffing attempt actually hits, since every other mutating
route already requires a valid access token first.

Deliberately a minimal hand-rolled limiter (INCR + EXPIRE on a
per-window Redis key) rather than a new dependency: Redis is already a
hard dependency of this stack (RQ), and a fixed-window counter is enough
for this specific goal — slow the attacker down, not implement
general-purpose API rate limiting.

Limits both by client IP and by the attempted email/identifier
independently, so neither "many emails from one IP" nor "one email
tried from many IPs" alone escapes throttling.
"""

from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, Request

from apps.api.app.dependencies.settings import SettingsDep
from core.config.exceptions import RateLimitedError

_LOGIN_WINDOW_SECONDS = 60
_LOGIN_MAX_ATTEMPTS_PER_IP = 10
_LOGIN_MAX_ATTEMPTS_PER_IDENTIFIER = 5

_REFRESH_WINDOW_SECONDS = 60
_REFRESH_MAX_ATTEMPTS_PER_IP = 20


async def _increment_and_check(*, redis_url: str, key: str, window_seconds: int, max_attempts: int) -> None:
    # Deliberately not a cached/module-level singleton client: a
    # redis.asyncio.Redis instance's connection pool binds to whichever
    # asyncio event loop is running when it first opens a connection, so
    # caching one across requests/event-loop lifetimes (e.g. across
    # pytest-asyncio's per-test loops) risks a "different event loop"
    # failure. redis-py's connection pooling happens at the URL/kwargs
    # level regardless of Python object identity, so building a fresh
    # client per call is cheap and correct — this dependency runs once
    # per login/refresh request, not in a hot loop.
    client = aioredis.from_url(redis_url)
    try:
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window_seconds)
        if count > max_attempts:
            raise RateLimitedError("Too many attempts — please wait before trying again.")
    finally:
        await client.aclose()


def _client_ip(request: Request) -> str:
    # No trusted reverse-proxy header parsing here on purpose — trusting
    # a client-supplied X-Forwarded-For without a known, configured proxy
    # chain lets an attacker simply spoof a fresh IP on every request and
    # bypass this limiter entirely. request.client.host is what the ASGI
    # server itself observed the connection came from.
    return request.client.host if request.client else "unknown"


async def rate_limit_login(request: Request, settings: SettingsDep) -> None:
    if settings.environment == "test":
        # The test suite's ASGI transport has every request share one
        # "client IP," so real throttling here would rate-limit the test
        # suite itself rather than a real attacker (see tests/conftest.py,
        # which sets ENVIRONMENT=test before any app import). Every other
        # environment, including local dev, keeps real throttling.
        return

    ip = _client_ip(request)
    await _increment_and_check(
        redis_url=settings.redis_url,
        key=f"hungrx:ratelimit:login:ip:{ip}",
        window_seconds=_LOGIN_WINDOW_SECONDS,
        max_attempts=_LOGIN_MAX_ATTEMPTS_PER_IP,
    )

    # Per-identifier limiting reads the email straight off the raw
    # request body without consuming/validating it, so this dependency
    # can run before FastAPI parses the LoginRequest body — the request
    # body stream itself is cached by Starlette, so a later
    # `payload: LoginRequest` parameter still reads the same bytes.
    try:
        body = await request.json()
        email = str(body.get("email", "")).strip().lower()
    except Exception:
        email = ""

    if email:
        await _increment_and_check(
            redis_url=settings.redis_url,
            key=f"hungrx:ratelimit:login:email:{email}",
            window_seconds=_LOGIN_WINDOW_SECONDS,
            max_attempts=_LOGIN_MAX_ATTEMPTS_PER_IDENTIFIER,
        )


async def rate_limit_refresh(request: Request, settings: SettingsDep) -> None:
    if settings.environment == "test":
        return

    ip = _client_ip(request)
    await _increment_and_check(
        redis_url=settings.redis_url,
        key=f"hungrx:ratelimit:refresh:ip:{ip}",
        window_seconds=_REFRESH_WINDOW_SECONDS,
        max_attempts=_REFRESH_MAX_ATTEMPTS_PER_IP,
    )


RateLimitLoginDep = Annotated[None, Depends(rate_limit_login)]
RateLimitRefreshDep = Annotated[None, Depends(rate_limit_refresh)]
