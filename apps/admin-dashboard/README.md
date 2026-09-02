# Admin Dashboard

Next.js 15 (App Router) + TypeScript admin dashboard for the restaurant
data automation platform, backed by `apps/api/`'s FastAPI JSON API.

## Architecture: the browser never holds the bearer token

This app's own server (Route Handlers + `middleware.ts`) sits between
the browser and FastAPI — the browser never calls FastAPI directly and
never sees a raw access/refresh JWT.

```
Browser  <->  This Next.js app  <->  FastAPI (apps/api/)
              (httpOnly cookies)      (Bearer JWT)
```

- `src/app/api/auth/login/route.ts` — proxies `POST /auth/login`, sets
  `hungrx_access_token`/`hungrx_refresh_token` as httpOnly cookies
  (never returned in the JSON body).
- `src/app/api/auth/logout/route.ts`, `.../refresh/route.ts`,
  `.../me/route.ts` — same pattern for the rest of the auth surface.
  `/me` is what `useAuth()` (`src/lib/auth/auth-context.tsx`) calls to
  learn who's signed in.
- `src/app/api/proxy/[...path]/route.ts` — generic authenticated
  pass-through: every other client-side data hook calls a relative
  `/api/proxy/<fastapi-path>` URL. Pure forwarding, no business logic.
- `src/lib/auth/authenticated-fetch.ts` — the shared "attach the
  access-token cookie as `Authorization: Bearer`, and on a 401 refresh
  once + retry once" logic both `/me` and `/proxy` use. Refresh tokens
  are single-use/rotated on the backend
  (`apps/api/app/services/auth_service.py`), so a successful refresh
  always re-sets both cookies with the new pair.
- `middleware.ts` — redirects to `/login?next=<path>` before any
  protected page renders if neither auth cookie is present. Only checks
  cookie *presence*, not validity — an expired-but-present access token
  still reaches the page, where the 401 refresh-and-retry above (and,
  failing that, the React Query `QueryCache`/`MutationCache` `onError`
  redirect in `src/components/providers.tsx`) takes over.

`src/lib/api/backend-url.ts` is the only place that knows FastAPI's
address (`API_INTERNAL_BASE_URL`, defaults to the docker-compose service
name `http://api:8000`) — server-only, never bundled for the browser.
`NEXT_PUBLIC_API_BASE_URL` is unused by this app's own code for exactly
that reason (nothing here calls FastAPI directly from client-side JS).

## Data layer

- `src/lib/api/types.ts` — hand-written TypeScript mirrors of the
  `core/schemas/*` Pydantic response shapes this dashboard actually
  consumes. Comment on each type names its source schema file.
- `src/lib/api/client.ts` — `apiFetch`/`apiGet`/`apiPost`: the one
  function every hook uses to call `/api/proxy/*`, parsing FastAPI's
  `{error: {code, message, field}}` envelope into a typed `ApiError`
  (`src/lib/api/errors.ts`) on any non-2xx response.
- `src/hooks/` — one file per resource, each a thin React Query
  wrapper around `apiGet`/`apiPost` — no business logic, only
  fetch/cache-key/invalidation wiring:
  - `use-restaurants.ts`, `use-agent-runs.ts`, `use-audit-log.ts` — plain
    queries.
  - `use-ingestion.ts` — `useTriggerIngestion()`, a mutation against
    `POST /admin/ingestion/trigger` (enqueues
    `apps/worker/app/jobs/restaurant_ingestion.py`'s RQ job — see
    `apps/worker/README.md`).
  - `use-reviews.ts` — `usePendingReviews`/`useReviewDetail` plus
    `useApproveReview`/`useRejectReview`/`useEditThenApproveReview`.
  - `use-agent-runs.ts` polls (`refetchInterval`) while any run on the
    current page is `pending`/`running`, and stops once nothing is
    active — no manual refresh needed to watch a run finish.

### Optimistic UI — where it's actually safe

Approve/reject/edit-approve resume a paused LangGraph run and, for
approve/edit-approve, write to production tables server-side
(`workflows/*/nodes/publish.py`) — not safe to reflect before the server
confirms (a failed enqueue with an optimistic "approved" state would
misrepresent whether anything actually happened). `use-reviews.ts`'s
`removeFromPendingList` only patches the query cache in `onSuccess` —
dropping the now-decided row from the pending list without waiting for
a full refetch — which is safe because it only ever reflects a decision
the server already confirmed. Same reasoning for
`use-ingestion.ts`: no optimistic "queued" row before the trigger
endpoint actually confirms the RQ job was enqueued.

## Auth / protected routes

- `src/lib/auth/auth-context.tsx` — `AuthProvider`/`useAuth()`:
  `user`, `isLoading`, `login(email, password)`, `logout()`. Wired into
  `src/components/providers.tsx` alongside the React Query
  `QueryClientProvider`.
- `src/app/login/page.tsx` — the only public page; `middleware.ts`
  excludes `/login` and `/api/*` from the redirect gate.
- `src/app/(app)/layout.tsx` + `src/components/app-shell.tsx` — every
  other page lives under the `(app)` route group and gets the nav
  sidebar/sign-out button automatically.

## Pages wired to real data

`(app)/dashboard`, `(app)/restaurants` (+ `[id]` detail),
`(app)/ingestion`, `(app)/review-queue` (+ `[id]` detail with
approve/reject/edit-JSON-then-approve), `(app)/agent-runs`,
`(app)/audit-log`. `(app)/changes`, `(app)/settings`, `(app)/users`
remain placeholders — out of scope for this pass (no backing endpoint
beyond `GET /admin/users`, which itself still returns `[]`).

## Loading / error states

Every data-driven page follows the same shape: `TableSkeleton`
(`src/components/table-skeleton.tsx`) or a `Skeleton` block while
`isPending`, `ErrorState` (`src/components/error-state.tsx`, reads
`ApiError.message`) with a Retry button on `isError`, an explicit empty
state when the list is genuinely empty, and the real content otherwise.

## Running locally

`docker compose up` (dev target, bind-mounted, hot reload) brings this
up alongside `api`/`worker`/`postgres`/`redis` — see the repo-root
`CLAUDE.md`. `npm run typecheck` / `npm run lint` / `npm run build` all
run clean; `npm run format` matches this repo's Prettier config.
