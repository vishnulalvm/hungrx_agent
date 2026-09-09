"""Unit tests for OpenAIProvider — mocks the underlying openai SDK client
(AsyncOpenAI.chat.completions.parse) so these tests never make a real
network call. Covers: strict structured-output request shape, refusal
handling, error wrapping into AIProviderError, and 429 retry/backoff."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIConnectionError, RateLimitError
from pydantic import BaseModel

from infrastructure.ai.openai_provider import OpenAIProvider
from infrastructure.ai.provider import AIProviderError


class _SampleOutput(BaseModel):
    value: str


def _make_provider() -> OpenAIProvider:
    return OpenAIProvider(api_key="test-key", model="gpt-4o-test")


def _fake_completion(*, parsed=None, refusal=None):
    message = SimpleNamespace(parsed=parsed, refusal=refusal)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


class TestGeneratesStructuredOutput:
    async def test_returns_parsed_output_and_model_name(self) -> None:
        provider = _make_provider()
        provider._client.chat.completions.parse = AsyncMock(
            return_value=_fake_completion(parsed=_SampleOutput(value="hello"))
        )

        result = await provider.generate_structured(
            system_prompt="system", user_content="user", response_model=_SampleOutput
        )

        assert result.output == _SampleOutput(value="hello")
        assert result.model_name == "gpt-4o-test"

    async def test_calls_parse_with_response_format_equal_to_response_model(self) -> None:
        provider = _make_provider()
        mock_parse = AsyncMock(return_value=_fake_completion(parsed=_SampleOutput(value="x")))
        provider._client.chat.completions.parse = mock_parse

        await provider.generate_structured(
            system_prompt="system", user_content="user", response_model=_SampleOutput
        )

        _, kwargs = mock_parse.call_args
        assert kwargs["response_format"] is _SampleOutput

    async def test_sends_system_and_user_messages_only(self) -> None:
        provider = _make_provider()
        mock_parse = AsyncMock(return_value=_fake_completion(parsed=_SampleOutput(value="x")))
        provider._client.chat.completions.parse = mock_parse

        await provider.generate_structured(
            system_prompt="be careful", user_content="raw material", response_model=_SampleOutput
        )

        _, kwargs = mock_parse.call_args
        assert kwargs["messages"] == [
            {"role": "system", "content": "be careful"},
            {"role": "user", "content": "raw material"},
        ]


class TestRejectsFreeFormOutput:
    async def test_refusal_raises_ai_provider_error(self) -> None:
        provider = _make_provider()
        provider._client.chat.completions.parse = AsyncMock(
            return_value=_fake_completion(parsed=None, refusal="cannot comply")
        )

        with pytest.raises(AIProviderError, match="refused"):
            await provider.generate_structured(
                system_prompt="system", user_content="user", response_model=_SampleOutput
            )

    async def test_missing_parsed_output_raises_ai_provider_error(self) -> None:
        provider = _make_provider()
        provider._client.chat.completions.parse = AsyncMock(
            return_value=_fake_completion(parsed=None, refusal=None)
        )

        with pytest.raises(AIProviderError, match="did not include parsed"):
            await provider.generate_structured(
                system_prompt="system", user_content="user", response_model=_SampleOutput
            )

    async def test_no_choices_raises_ai_provider_error(self) -> None:
        provider = _make_provider()
        provider._client.chat.completions.parse = AsyncMock(return_value=SimpleNamespace(choices=[]))

        with pytest.raises(AIProviderError, match="no choices"):
            await provider.generate_structured(
                system_prompt="system", user_content="user", response_model=_SampleOutput
            )


class TestWrapsTransportErrors:
    async def test_openai_error_is_wrapped_in_ai_provider_error(self) -> None:
        provider = _make_provider()
        provider._client.chat.completions.parse = AsyncMock(
            side_effect=APIConnectionError(request=SimpleNamespace())
        )

        with pytest.raises(AIProviderError, match="OpenAI request failed"):
            await provider.generate_structured(
                system_prompt="system", user_content="user", response_model=_SampleOutput
            )


def _rate_limit_error(*, retry_after: str | None = None) -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    response = httpx.Response(429, request=request, headers=headers, json={"error": {"message": "rate limited"}})
    return RateLimitError("rate limited", response=response, body=None)


class TestRateLimitRetry:
    async def test_retries_after_rate_limit_and_eventually_succeeds(self, monkeypatch) -> None:
        provider = _make_provider()
        sleeps: list[float] = []
        monkeypatch.setattr(
            "infrastructure.ai.openai_provider.asyncio.sleep",
            AsyncMock(side_effect=lambda s: sleeps.append(s)),
        )
        mock_parse = AsyncMock(
            side_effect=[
                _rate_limit_error(retry_after="2"),
                _fake_completion(parsed=_SampleOutput(value="ok")),
            ]
        )
        provider._client.chat.completions.parse = mock_parse

        result = await provider.generate_structured(
            system_prompt="system", user_content="user", response_model=_SampleOutput
        )

        assert result.output == _SampleOutput(value="ok")
        assert mock_parse.call_count == 2
        assert sleeps == [2.0]

    async def test_gives_up_after_max_retries_and_raises_ai_provider_error(self, monkeypatch) -> None:
        provider = _make_provider()
        monkeypatch.setattr(
            "infrastructure.ai.openai_provider.asyncio.sleep", AsyncMock(return_value=None)
        )
        provider._client.chat.completions.parse = AsyncMock(side_effect=_rate_limit_error())

        with pytest.raises(AIProviderError, match="OpenAI request failed"):
            await provider.generate_structured(
                system_prompt="system", user_content="user", response_model=_SampleOutput
            )

    async def test_uses_fallback_wait_when_retry_after_header_missing(self, monkeypatch) -> None:
        provider = _make_provider()
        sleeps: list[float] = []
        monkeypatch.setattr(
            "infrastructure.ai.openai_provider.asyncio.sleep",
            AsyncMock(side_effect=lambda s: sleeps.append(s)),
        )
        provider._client.chat.completions.parse = AsyncMock(
            side_effect=[_rate_limit_error(), _fake_completion(parsed=_SampleOutput(value="ok"))]
        )

        await provider.generate_structured(
            system_prompt="system", user_content="user", response_model=_SampleOutput
        )

        assert sleeps == [5.0]

    async def test_a_non_rate_limit_error_is_not_retried(self, monkeypatch) -> None:
        provider = _make_provider()
        sleep_mock = AsyncMock()
        monkeypatch.setattr("infrastructure.ai.openai_provider.asyncio.sleep", sleep_mock)
        mock_parse = AsyncMock(side_effect=APIConnectionError(request=SimpleNamespace()))
        provider._client.chat.completions.parse = mock_parse

        with pytest.raises(AIProviderError, match="OpenAI request failed"):
            await provider.generate_structured(
                system_prompt="system", user_content="user", response_model=_SampleOutput
            )

        assert mock_parse.call_count == 1
        sleep_mock.assert_not_called()


class TestConstruction:
    def test_requires_non_empty_api_key(self) -> None:
        with pytest.raises(ValueError):
            OpenAIProvider(api_key="", model="gpt-4o-test")
