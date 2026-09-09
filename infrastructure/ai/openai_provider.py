"""OpenAI-backed AIProvider implementation. Uses the Chat Completions
`parse()` API with `response_format=<pydantic model>` (OpenAI's strict
`json_schema` structured-output mode) — the model is constrained at the
API level to only emit JSON matching the given schema, not merely asked
nicely via a prompt to do so. This is what makes "do not allow free-form
output" a real API-level guarantee rather than a convention.

Retries on 429 (RateLimitError) with backoff: a chunked extraction run
(infrastructure/ai/chunked_extraction.py) makes several calls per
restaurant in quick succession, all resending the same source material —
on a low-TPM-tier account this can trip OpenAI's per-minute token limit
well within a single restaurant's run. OpenAI's 429 response includes a
`retry-after` header naming exactly how long to wait; honoring it (with
a fallback and cap for when it's absent) turns a transient rate-limit
moment into a short pause instead of a hard failure that kills the whole
collector run. Only RateLimitError is retried — every other OpenAIError
(auth failure, bad request, refusal-adjacent transport errors) fails
immediately, since waiting and repeating an unchanged request wouldn't
fix those.
"""

import asyncio
import logging
from typing import TypeVar

from openai import AsyncOpenAI, OpenAIError, RateLimitError
from pydantic import BaseModel

from infrastructure.ai.provider import AIProvider, AIProviderError, AIProviderResult

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger("hungrx.infrastructure.ai.openai_provider")

# Retry budget for a rate-limited call: enough attempts to ride out a
# short burst (chunked_extraction's own concurrency cap plus this
# backoff together are what keep a single restaurant's run within a
# small TPM budget) without turning one already-slow crawl+AI run into
# an unbounded wait.
_MAX_RATE_LIMIT_RETRIES = 5
_FALLBACK_RETRY_AFTER_SECONDS = 5.0
_MAX_RETRY_AFTER_SECONDS = 60.0


def _retry_after_seconds(exc: RateLimitError) -> float:
    """OpenAI's 429 response names the exact wait time via the
    `retry-after` header (falling back to `retry-after-ms` when
    present); if neither is set, a fixed fallback is used instead of
    guessing from the error message. Always capped so a server-supplied
    value can't stall a run for an unreasonable amount of time."""
    headers = exc.response.headers
    retry_after_ms = headers.get("retry-after-ms")
    if retry_after_ms is not None:
        try:
            return min(float(retry_after_ms) / 1000.0, _MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
    retry_after = headers.get("retry-after")
    if retry_after is not None:
        try:
            return min(float(retry_after), _MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
    return _FALLBACK_RETRY_AFTER_SECONDS


class OpenAIProvider(AIProvider):
    def __init__(self, *, api_key: str, model: str) -> None:
        if not api_key:
            raise ValueError("OpenAIProvider requires a non-empty api_key")
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key)

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_content: str,
        response_model: type[T],
    ) -> AIProviderResult[T]:
        completion = await self._parse_with_rate_limit_retry(
            system_prompt=system_prompt, user_content=user_content, response_model=response_model
        )

        choice = completion.choices[0] if completion.choices else None
        if choice is None:
            raise AIProviderError("OpenAI returned no choices")

        if choice.message.refusal:
            raise AIProviderError(f"Model refused to respond: {choice.message.refusal}")

        parsed = choice.message.parsed
        if parsed is None:
            raise AIProviderError("OpenAI response did not include parsed structured output")

        return AIProviderResult(
            output=parsed,
            model_name=self._model,
            # Chat Completions doesn't expose a single scalar
            # "confidence" for the response as a whole (log-probs are a
            # different, per-token concept) — per-field confidence is
            # instead requested as part of response_model itself
            # (ExtractedDish.confidence, etc.), which is the honest place
            # for a model to report it.
            overall_confidence=None,
        )

    async def _parse_with_rate_limit_retry(
        self, *, system_prompt: str, user_content: str, response_model: type[T]
    ):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            try:
                return await self._client.chat.completions.parse(
                    model=self._model, messages=messages, response_format=response_model
                )
            except RateLimitError as exc:
                if attempt == _MAX_RATE_LIMIT_RETRIES:
                    raise AIProviderError(f"OpenAI request failed: {exc}") from exc
                wait_seconds = _retry_after_seconds(exc)
                logger.warning(
                    "OpenAI rate limit hit (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    _MAX_RATE_LIMIT_RETRIES,
                    wait_seconds,
                    exc,
                )
                await asyncio.sleep(wait_seconds)
            except OpenAIError as exc:
                raise AIProviderError(f"OpenAI request failed: {exc}") from exc

        raise AssertionError("unreachable: loop always returns or raises")
