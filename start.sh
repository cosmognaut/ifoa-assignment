#!/usr/bin/env bash
#
# One command from a clean clone to a populated database and a live console.
# Requires Docker with the compose plugin. Nothing else on the host is used.
#
#   ./start.sh                 # cycle 2026-10-01-current
#   CYCLE=2026-10-29 ./start.sh
#
set -euo pipefail

CYCLE="${CYCLE:-2026-10-01-current}"

readonly API_BASE="http://127.0.0.1:3000"
readonly MONGO_HEALTH_TIMEOUT=60
readonly API_HEALTH_TIMEOUT=90
readonly HEALTH_INTERVAL=2

step() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# --- 1. Docker present -------------------------------------------------------
step "Checking Docker"
command -v docker >/dev/null 2>&1 \
  || fail "docker is not installed or not on PATH."
docker info >/dev/null 2>&1 \
  || fail "cannot talk to the Docker daemon (is it running, and can this user reach the socket?)"
docker compose version >/dev/null 2>&1 \
  || fail "the docker compose plugin is missing (needed: 'docker compose', not 'docker-compose')."
printf '    docker %s, compose %s\n' \
  "$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)" \
  "$(docker compose version --short 2>/dev/null || echo unknown)"

# Build first, not on demand: the pipeline image takes ~40 s and the API image
# builds the Vite bundle. Building up front keeps the health-wait steps honest
# and lets BuildKit build both in parallel.
step "Building images (first run only; cached afterwards)"
docker compose --profile tools build

# --- 2. Mongo, and wait for its own healthcheck ------------------------------
step "Starting MongoDB"
docker compose up -d mongo

printf '    waiting for the mongosh ping healthcheck '
deadline=$((SECONDS + MONGO_HEALTH_TIMEOUT))
mongo_status=unknown
while :; do
  container_id="$(docker compose ps -q mongo 2>/dev/null || true)"
  if [ -n "$container_id" ]; then
    mongo_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$container_id" 2>/dev/null || echo unknown)"
  fi
  case "$mongo_status" in
    healthy) printf '\n    mongo: healthy\n'; break ;;
    unhealthy) printf '\n'; fail "mongo reported unhealthy. Check: docker compose logs mongo" ;;
  esac
  if [ "$SECONDS" -ge "$deadline" ]; then
    printf '\n'
    docker compose logs --tail=30 mongo || true
    fail "mongo did not become healthy within ${MONGO_HEALTH_TIMEOUT}s (state: ${mongo_status})."
  fi
  printf '.'
  sleep "$HEALTH_INTERVAL"
done

# --- 3. Pipeline: one run does verify -> fetch-if-needed -> load -> promote ---
step "Ingesting cycle '${CYCLE}'"
printf '    This verifies the committed SHA-256 manifest, parses the read-only raw\n'
printf '    cache, validates, diffs against the previously promoted cycle and\n'
printf '    promotes the result. It can take ~30-60 s.\n'
pipeline_log="$(mktemp -t ifoa-pipeline.XXXXXX.log)"
pipeline_status=0
docker compose --profile tools run --rm pipeline \
  pipeline run --cycle "$CYCLE" 2>&1 | tee "$pipeline_log" || pipeline_status=$?

if [ "$pipeline_status" -ne 0 ]; then
  printf '\n--- tail of pipeline output (%s) ---\n' "$pipeline_log" >&2
  tail -n 40 "$pipeline_log" >&2 || true
  fail "pipeline exited ${pipeline_status}; refusing to start the API over an unpopulated database."
fi
if ! grep -q 'status: promoted' "$pipeline_log"; then
  printf '\n--- tail of pipeline output (%s) ---\n' "$pipeline_log" >&2
  tail -n 40 "$pipeline_log" >&2 || true
  fail "pipeline exited 0 but never promoted '$CYCLE' (quarantined or wrong cycle?)."
fi
rm -f "$pipeline_log"

# --- 4. API ----------------------------------------------------------------
step "Starting the API"
docker compose up -d api

# --- 5. Wait for features > 0 ----------------------------------------------
step "Waiting for ${API_BASE}/api/health to report features"
# curl if the host has it; otherwise ask the API container's own node, so the
# script still needs no host package beyond Docker.
probe() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 5 "${API_BASE}/api/health" 2>/dev/null
  else
    docker compose exec -T api node --input-type=module -e \
      "fetch('http://127.0.0.1:3000/api/health').then(r=>r.text()).then(t=>process.stdout.write(t)).catch(()=>process.exit(1))" \
      2>/dev/null
  fi
}

deadline=$((SECONDS + API_HEALTH_TIMEOUT))
features=0
body=''
printf '    '
while :; do
  if body="$(probe)" && [ -n "$body" ]; then
    features="$(printf '%s' "$body" | sed -n 's/.*"features":[[:space:]]*\([0-9][0-9]*\).*/\1/p')"
    features="${features:-0}"
    if [ "$features" -gt 0 ]; then
      printf '\n'
      break
    fi
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    printf '\n'
    docker compose logs --tail=40 api || true
    fail "the API did not report features > 0 within ${API_HEALTH_TIMEOUT}s (last body: ${body:-<none>})."
  fi
  printf '.'
  sleep "$HEALTH_INTERVAL"
done

step "Ready"
printf '    Console:  %s/\n' "$API_BASE"
printf '    Health:   %s/api/health -> %s\n' "$API_BASE" "$body"
printf '    Features: %s\n' "$features"
printf '    Stop:     docker compose down        (add -v to drop the data volume)\n'
