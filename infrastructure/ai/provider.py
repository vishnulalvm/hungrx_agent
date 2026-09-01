"""AI provider abstraction: the one seam every AI-backed node (currently
just Multimodal Translation) is allowed to call through. Concrete
providers (OpenAI today, any other model API later) implement this same
interface, so switching providers/models is a one-line change at the
call site — nothing about the node logic, prompts, or output schema has
to change.

Structural guarantees this interface exists to enforce:
  - `generate_structured` takes a Pydantic model *type*, not a free-form
    prompt-only call — a provider can literally only ever return an
    instance of the schema it was asked for, never arbitrary text.
  - There is no method on this interface for writing to the database, or
    for anything resembling "run this SQL" / "call this repository".
    AIProvider only ever turns source text into a typed Python object;
    what a caller does with that object (validate it, hand it to a
    human, discard it) is entirely outside this interface's power.
"""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class AIProviderResult(BaseModel, Generic[T]):
    """Wraps a provider's structured output with the metadata a caller
    needs to judge how much to trust it, without polluting the output
    schema itself (`T`) with provider/transport-level fields."""

    model_config = {"arbitrary_types_allowed": True}

    output: T
    model_name: str
    # Provider-reported confidence for the call as a whole, when the
    # provider/model exposes one (distinct from any per-field confidence
    # the output schema itself may carry, e.g. ExtractedDish.confidence).
    # None when no such signal is available — never fabricated.
    overall_confidence: float | None = None


class AIProviderError(Exception):
    """Raised for any failure to obtain valid structured output — a
    transport error, a refusal, or output that failed schema validation.
    Callers must treat this as "no usable output was produced", not
    attempt to salvage a partial/malformed response."""


class AIProvider(ABC, Generic[T]):
    @abstractmethod
    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_content: str,
        response_model: type[T],
    ) -> AIProviderResult[T]:
        """Sends `system_prompt` + `user_content` to the model and
        returns its response parsed strictly into `response_model`.

        `user_content` must be exactly the collected source material (and
        instructions about it) the caller intends to send — this
        interface takes no restaurant/database context implicitly, so a
        caller can't accidentally leak more than it means to.

        Raises AIProviderError if the provider fails to produce output
        that validates against `response_model` — never returns a
        best-effort/partial object.
        """
