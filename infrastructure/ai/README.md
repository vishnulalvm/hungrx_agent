# infrastructure/ai/

The one seam every AI-backed collector node is allowed to call through.
Nothing outside this module talks to OpenAI (or any model API) directly
— that's what makes swapping models/providers later a one-line change
instead of a rewrite.

## Files

- `provider.py` — `AIProvider(ABC)`: `async def generate_structured(*,
  system_prompt, user_content, response_model: type[T]) ->
  AIProviderResult[T]`. Takes a Pydantic model *type*, not a free-form
  prompt — the interface itself makes "the model can only return this
  exact shape" structural, not a convention callers have to remember.
  `AIProviderResult` wraps the parsed output with `model_name` and an
  optional `overall_confidence` without polluting the output schema with
  transport-level fields. `AIProviderError` is raised for any failure to
  produce valid output (transport error, refusal, failed validation) —
  callers must treat that as "no usable output," never attempt to
  salvage a partial response.
- `openai_provider.py` — `OpenAIProvider(api_key, model)`: the only
  concrete implementation today. Uses `chat.completions.parse(...,
  response_format=<pydantic model>)` — OpenAI's strict `json_schema`
  structured-output mode, enforced at the API level. Raises
  `AIProviderError` on a transport error, a refusal
  (`message.refusal`), or missing parsed output.

## Why no default/null provider

Unlike `infrastructure/source_authority`'s `NullEntityResolutionProvider`
(safe: always resolves to "not found"), there's no meaningful "null" AI
provider — any stand-in would either do nothing useful or risk looking
like real output. Callers (`workflows/collector_workflow/graph.py`) must
supply a real `AIProvider` explicitly; there's no silent default.

## Adding a new provider

Implement `AIProvider` in a new file here (e.g. `anthropic_provider.py`)
and pass an instance into whichever node needs it
(`build_multimodal_translation_node(session, storage, ai_provider)`) — no
other code changes. Keep the same guarantee `OpenAIProvider` has: the
provider must enforce structured output at the API/SDK level, not rely
on prompt instructions alone, and must raise `AIProviderError` rather
than return best-effort/partial output.

## What this module does NOT do

- No database access of any kind — `AIProvider` implementations take
  text in, return a typed Pydantic object out. Nothing here can write to
  any table.
- No restaurant-identity or database context is added implicitly —
  callers decide exactly what goes into `user_content`. See
  `workflows/collector_workflow/nodes/multimodal_translation.py` for how
  the Multimodal Translation node deliberately sends only collected
  source material (crawled HTML), never the restaurant's name/location.
