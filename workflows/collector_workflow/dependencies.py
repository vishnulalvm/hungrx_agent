"""Wires the real, process-default dependencies `build_graph` needs
(storage, AI provider) for callers — like the admin review API — that
need a compiled graph but aren't themselves a crawl job with an
already-open StorageAdapter/AIProvider (see
workflows/collector_workflow/nodes/source_authority.py and friends for
where those are normally supplied by a collector-run caller directly).
Resuming a paused human_review interrupt never re-executes
source_authority/extraction/multimodal_translation (LangGraph resumes
exactly at the interrupted node), so these are only structurally
required by `build_graph`'s signature, not functionally exercised on a
resume — but they still have to be real, working instances, since
`build_graph` has no unsafe silent fallback for either (see graph.py's
docstring on why).
"""

from typing import TypeVar

from pydantic import BaseModel

from core.config.settings import Settings
from infrastructure.ai.openai_provider import OpenAIProvider
from infrastructure.ai.provider import AIProvider, AIProviderResult
from infrastructure.storage.base import StorageAdapter
from infrastructure.storage.local_storage import LocalStorageAdapter

T = TypeVar("T", bound=BaseModel)


def default_storage_adapter(settings: Settings) -> StorageAdapter:
    return LocalStorageAdapter(settings.storage_local_base_dir)


class _LazyOpenAIProvider(AIProvider):
    """Defers constructing (and therefore api-key-validating) the real
    OpenAIProvider until a call actually needs it. A resumed
    human_review interrupt never reaches multimodal_translation again
    (LangGraph resumes exactly at the interrupted node), so a caller
    that only ever resumes reviews — like ReviewService — should not be
    forced to have a working OpenAI key configured just to satisfy
    build_graph's required `ai_provider` parameter. If a run genuinely
    does reach multimodal_translation without a configured key, this
    still fails loudly at that point, same as OpenAIProvider always has
    — nothing here silently no-ops."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def generate_structured(
        self, *, system_prompt: str, user_content: str, response_model: type[T]
    ) -> AIProviderResult[T]:
        provider = OpenAIProvider(api_key=self._settings.openai_api_key, model=self._settings.openai_model)
        return await provider.generate_structured(
            system_prompt=system_prompt, user_content=user_content, response_model=response_model
        )


def default_ai_provider(settings: Settings) -> AIProvider:
    return _LazyOpenAIProvider(settings)
