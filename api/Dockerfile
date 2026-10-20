# syntax=docker/dockerfile:1

# Stage 1 — build the console. Vite emits a hashed bundle into web/dist; the
# TypeScript check runs here too (npm run build), so a type error fails the image
# rather than the container at boot.
FROM node:22-alpine AS web-build
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./

# Optional CARTO vector-basemap key. Vite inlines VITE_* at build time, so the
# value must arrive here — a mounted .env at run time would change nothing. The
# map renders without it (styleUrl() falls back to the unkeyed raster style).
# Consequence: the key is extractable from the shipped JS bundle. Fine for a
# public basemap key, never acceptable for a secret.
ARG VITE_CARTO_API_KEY=""
ENV VITE_CARTO_API_KEY=$VITE_CARTO_API_KEY

RUN npm run build

# Stage 2 — the API image. api/package-lock.json is committed, so `npm ci` is
# reproducible; --omit=dev drops typescript and the @types packages, because
# node 22 strips the type annotations at load time and never needs them.
FROM node:22-alpine AS runtime
ENV NODE_ENV=production \
    PORT=3000
WORKDIR /app/api
COPY api/package.json api/package-lock.json ./
RUN npm ci --omit=dev
COPY api/ ./

# Paths are resolved from the module, not the cwd: db.ts reads
# <repo>/config/*.yaml, server.ts serves <api>/public and reads
# <repo>/data/export. So the image must keep the repo's layout, with the API at
# /app/api.
WORKDIR /app
COPY config/ ./config/
# The bundle comes from stage 1, not from the host: web/dist is gitignored, so a
# fresh clone has no build output to copy.
COPY --from=web-build /build/dist/ ./api/public/
COPY data/export/ ./data/export/

# Back into api/: `node server.ts` is resolved against the cwd, so the last
# WORKDIR is what CMD actually sees. server.ts itself is cwd-independent (it
# derives everything from import.meta.url); only its path is not.
WORKDIR /app/api

EXPOSE 3000
CMD ["node", "server.ts"]
