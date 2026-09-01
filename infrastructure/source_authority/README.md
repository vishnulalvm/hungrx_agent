# infrastructure/source_authority/

Turns "here's a restaurant's name/city/phone" into "here's their verified
official website" — or refuses to guess. This is the module that backs
`apps/api/app/services/source_authority_service.py`, which is in turn
what Collector Agent 1 (`workflows/collector_workflow/nodes/source_authority.py`)
calls.

## Design: external lookup is behind an interface

`provider.py` — `EntityResolutionProvider(ABC)`: `async def
resolve(query: EntityResolutionQuery) -> list[EntityCandidate]`. This is
the only integration point for an external "find this business's
website" API (e.g. a places/search API). Nothing else in the codebase
should call an external resolution API directly.

`null_provider.py` — `NullEntityResolutionProvider`: always returns `[]`
(i.e. always resolves to `NOT_FOUND`). This is the safe default used
anywhere a real provider hasn't been wired up yet (e.g.
`build_graph()`'s default) — it fails closed rather than fabricating
results.

To add a real provider (e.g. Google Places), implement
`EntityResolutionProvider` in a new file here and pass an instance into
`SourceAuthorityService`/`build_source_authority_node` — no other code
needs to change.

## Validation pipeline (in order)

1. `url_normalizer.py` — `normalize_url(raw_url) -> str`: lowercases
   host, strips default ports, drops fragments, strips tracking params
   (`utm_*`, `gclid`, `fbclid`). Idempotent. **Does not** strip `www.` —
   that's deliberate; `www.example.com` and `example.com` are treated as
   the same *domain* for comparison purposes (see `domain_validator.py`)
   but the normalized URL preserves whichever host form the candidate
   actually used.
2. `aggregator_blocklist.py` — `KNOWN_AGGREGATOR_DOMAINS` (yelp,
   tripadvisor, doordash, ubereats, grubhub, facebook, instagram, etc.),
   `is_known_aggregator(domain)`: checks the domain and all of its parent
   domains, so `order.doordash.com` is correctly caught via
   `doordash.com`.
3. `domain_validator.py` — `validate_official_domain(raw_url) ->
   tuple[str, DomainLockConfig]`, raises `DomainRejectedError` for
   aggregators/invalid domains. Reuses the same host-matching logic as
   `infrastructure/crawler/domain_lock.py`.

## Confidence tiers

The actual orchestration lives in
`apps/api/app/services/source_authority_service.py`
(`SourceAuthorityService.resolve_official_website`), not in this
package, but the thresholds matter for anyone calling it:

- confidence ≥ **0.75** → `ResolutionStatus.VERIFIED`, a `Source` row is
  persisted with `is_verified_domain=True`.
- 0.5–0.75 → `ResolutionStatus.NEEDS_REVIEW`, **nothing is persisted**.
- No candidates survive aggregator/domain rejection →
  `ResolutionStatus.REJECTED` (with the rejected URLs listed).
- Provider returned nothing at all → `ResolutionStatus.NOT_FOUND`.

Only `VERIFIED` should ever be treated as a trustworthy URL by callers —
this is what backs the "never hallucinate URLs" guarantee in the
collector workflow.
