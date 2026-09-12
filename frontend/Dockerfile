# Production image for the Next.js frontend, using Next's own "standalone"
# output (next.config.ts, Day 47) so the final image needs neither
# node_modules nor the rest of the source tree - just the traced server
# bundle plus static/public assets, verified locally with a real
# `npm run build` (Docker itself isn't available on this dev machine).
#
# node:20-slim (Debian, glibc), not alpine: this project's dependencies
# include native Rust bindings (Tailwind v4's oxide engine, lightningcss)
# published as per-platform prebuilt binaries - glibc has the broadest,
# best-tested coverage for those, and alpine's musl libc isn't guaranteed
# to have a published variant for every one of them.
#
# Pinned to Node 20 to match the version this whole project has been
# built and verified against (Day 44's same reasoning for CI).

# ---- deps: installed once, cached separately from source changes ----
FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---- builder: the actual `next build` ----
FROM node:20-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* variables are inlined into the compiled output by Next.js
# itself at build time, not read from the environment when the container
# starts - this has to be a build arg, or the running frontend would
# silently keep whatever default was baked in when the image was built,
# regardless of what's set on the container at deploy time.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
RUN npm run build

# ---- runner: minimal final image ----
FROM node:20-slim AS runner
WORKDIR /app

# No `useradd` here (Day 53's review, GitHub Actions run #10 - the
# actual failure a real `docker build` had never been run before this):
# node:20-slim already creates a `node` user at uid/gid 1000, so adding
# another user at that same uid fails outright (`useradd`: exit code 4,
# "UID not unique"). Reuse the image's own user instead of colliding with
# it.
ENV NODE_ENV=production

COPY --from=builder --chown=node:node /app/public ./public
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static

USER node

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

CMD ["node", "server.js"]
