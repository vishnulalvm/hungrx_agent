"""OpenAI-backed AIProvider implementation. Uses the Chat Completions
`parse()` API with `response_format=<pydantic model>` (OpenAI's strict
`json_schema` structured-output mode) — the model is constrained at the
API level to only emit JSON matching the given schema, not merely asked
nicely via a prompt to do so. This is what makes "do not allow free-form
output" a real API-level guarantee rather than a convention.
"""

from typing import TypeVar

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel

from infrastructure.ai.provider import AIProvider, AIProviderError, AIProviderResult

T = TypeVar("T", bound=BaseModel)


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
        try:
            completion = await self._client.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                response_format=response_model,
            )
        except OpenAIError as exc:
            raise AIProviderError(f"OpenAI request failed: {exc}") from exc

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
