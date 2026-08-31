# syntax=docker/dockerfile:1
#
# Next.js admin dashboard. Build context is this directory (apps/admin-dashboard),
# unlike the Python services — the frontend has no dependency on the monorepo's
# root-level Python packages.
#
# Build with:
#   docker build -f apps/admin-dashboard/Dockerfile -t hungrx-admin-dashboard apps/admin-dashboard
# Targets: "dev" (next dev, hot reload) and "prod" (standalone output, default).

FROM node:20-alpine AS base
WORKDIR /app
RUN apk add --no-cache libc6-compat curl

# ---------------------------------------------------------------------------
# deps — installed once, cached across source changes
# ---------------------------------------------------------------------------
FROM base AS deps

COPY package.json package-lock.json ./
RUN npm ci

# ---------------------------------------------------------------------------
# dev — full source bind-mounted at runtime by docker-compose; hot reload
# via `next dev`
# ---------------------------------------------------------------------------
FROM deps AS dev

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]

# ---------------------------------------------------------------------------
# builder — production build only, not shipped as a final image itself
# ---------------------------------------------------------------------------
FROM deps AS builder

COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---------------------------------------------------------------------------
# prod — minimal standalone runtime, non-root user, no source/node_modules
# beyond what Next.js traced as actually needed
# ---------------------------------------------------------------------------
FROM base AS prod

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:3000/ || exit 1

CMD ["node", "server.js"]
