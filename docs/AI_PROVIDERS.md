# Understanding the AI provider (and how to switch it later)

This project uses an AI model (currently **OpenAI**) for exactly one
job: reading a crawled restaurant page and turning it into structured
menu/nutrition data. This doc explains how that's wired up today, and
what's involved if you ever want to switch to a different AI company
(Anthropic/Claude, Google/Gemini, etc.).

**Nothing needs to change today to keep using OpenAI.** This is
background/reference material for when that need actually comes up.

---

## 1. Where the AI is actually used

Only one place in the whole project calls an AI model:
`workflows/collector_workflow/nodes/multimodal_translation.py` — the
step that takes a crawled page and asks the model to structure it into
a `Restaurant`/menu/dish shape.

No other part of the app calls an AI model directly. Crawling, domain
verification, validation, human review, and publishing are all plain
code with no AI involved.

---

## 2. Why switching providers is designed to be easy (in theory)

The project was deliberately built so that *nothing* — not the prompt
logic, not the output shape, not the review/validation steps downstream
— needs to know or care which AI company is answering. That's enforced
by one file:

**`infrastructure/ai/provider.py`** defines a contract called
`AIProvider`. It says, in effect: *"Give me a system prompt and some
page content, and give me back this exact data shape — nothing else."*
Every AI-backed step in the app talks to that contract, never to
OpenAI (or Anthropic, or Gemini) directly.

**`infrastructure/ai/openai_provider.py`** is the one class that
actually fulfills that contract today, using OpenAI's API. It's the
*only* file in the project that imports OpenAI's SDK.

Because of that separation, switching providers is meant to mean:
"write one new file implementing the same contract, then point the app
at it" — not "go rewrite the AI logic everywhere it's used."

---

## 3. Where that one provider gets plugged in

There is exactly one place in the code where `OpenAIProvider` gets
created: `workflows/collector_workflow/dependencies.py`, in a function
called `default_ai_provider()`. It reads `OPENAI_API_KEY` and
`OPENAI_MODEL` from your `.env` file and builds the provider from them.

That's the only spot that would need to change to use a different
company's model.

---

## 4. What's NOT built yet

Today, there is:
- ✅ A clean interface (`AIProvider`) that doesn't lock the rest of the
  app to any one AI company.
- ✅ One real implementation: `OpenAIProvider`.
- ❌ **No Anthropic (Claude) implementation.**
- ❌ **No Gemini implementation.**
- ❌ **No switch/setting to pick between providers** — there's no
  `AI_PROVIDER=openai` (or similar) value in `.env` yet. The app only
  knows how to talk to OpenAI right now.

So today, switching to Anthropic or Gemini is **not** a config change —
it requires writing a small amount of new code first. The rest of this
doc explains what that would involve, for when you're ready to do it
(or ask for it to be built).

---

## 5. What switching to Anthropic or Gemini would actually take

If/when this gets built, the pattern would be:

1. **Add the SDK.** Neither Anthropic's nor Google's Python SDK is
   installed in this project yet — only OpenAI's is
   (see `pyproject.toml`). Whichever one is needed gets added there.

2. **Write a new provider class**, e.g. `infrastructure/ai/
   anthropic_provider.py` or `infrastructure/ai/gemini_provider.py`,
   implementing the same `AIProvider` contract as
   `openai_provider.py` does. Both Anthropic and Google's APIs support
   forcing the model to return data in a specific shape (Anthropic via
   "tool use," Gemini via a "response schema" option) — so the same
   guarantee this project relies on (the model can *only* return the
   expected shape, never free-form text) is possible with either, it
   just gets implemented slightly differently per company.

3. **Add a setting to choose between them.** `core/config/settings.py`
   would get a new field (e.g. `ai_provider: Literal["openai",
   "anthropic", "gemini"]`), and `.env` would get a matching
   `AI_PROVIDER=openai` line plus that provider's own API key
   (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`).

4. **Update the one plug-in point.** `default_ai_provider()` in
   `workflows/collector_workflow/dependencies.py` would check the new
   setting and build whichever provider class was selected.

Once all four of those exist, switching providers really would become
a one-line `.env` change (`AI_PROVIDER=anthropic`, plus that provider's
API key) with no other code needing to change — which is the whole
point of having the `AIProvider` contract in the first place.

---

## 6. Quick reference

| File | What it is |
|---|---|
| `infrastructure/ai/provider.py` | The contract every AI provider must follow. Never changes when adding a new provider. |
| `infrastructure/ai/openai_provider.py` | The only real provider implemented today. |
| `workflows/collector_workflow/dependencies.py` | The one place a provider gets constructed and handed to the app. |
| `workflows/collector_workflow/nodes/multimodal_translation.py` | The only place in the app that actually calls the AI. |
| `.env` → `OPENAI_API_KEY` / `OPENAI_MODEL` | Today's provider settings. |

---

When you're ready to actually add Anthropic or Gemini support, just
ask — the groundwork above is exactly what that work would follow.
