"""
Guards the container bootstrap against the two failure modes that are invisible
until someone clones the repo: a compose file that quietly couples the API to
the pipeline (so a failed ingest leaves the console serving nothing), and a
start.sh that stops enforcing its preconditions.

Deliberately hermetic: no Docker daemon, no network. It asserts the shape of the
artefacts, not that an image builds — `./start.sh` is the proof of that.
"""

import fnmatch
import os
import stat
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = REPO_ROOT / "compose.yaml"
START = REPO_ROOT / "start.sh"
API_DOCKERFILE = REPO_ROOT / "api" / "Dockerfile"
PIPELINE_DOCKERFILE = REPO_ROOT / "pipeline" / "Dockerfile"
MONGO_URI = "mongodb://mongo:27017/aero_pipeline"
DEFAULT_CYCLE = "2026-10-01-current"


@pytest.fixture(scope="module")
def compose() -> dict:
    with open(COMPOSE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_compose_declares_the_three_services(compose):
    assert set(compose["services"]) == {"mongo", "api", "pipeline"}


def test_api_does_not_depend_on_the_pipeline(compose):
    """An empty database is a valid state: the console renders 'Publication
    unavailable'. Making the API wait on an ingest turns a parse failure into a
    dead console."""
    api = compose["services"]["api"]
    depends = api.get("depends_on") or {}
    assert "pipeline" not in depends
    assert depends["mongo"]["condition"] == "service_healthy"


def test_pipeline_is_a_profiled_one_shot_tool(compose):
    pipeline = compose["services"]["pipeline"]
    assert "tools" in pipeline["profiles"]
    assert pipeline.get("restart") == "no"
    assert pipeline["depends_on"]["mongo"]["condition"] == "service_healthy"


def test_pipeline_mounts_the_committed_cache_read_only(compose):
    volumes = compose["services"]["pipeline"]["volumes"]
    assert "./data/raw:/app/data/raw:ro" in volumes
    assert "./config:/app/config:ro" in volumes


def test_mongo_is_pinned_capped_and_not_published(compose):
    mongo = compose["services"]["mongo"]
    assert mongo["image"] == "mongo:7"
    assert mongo["command"] == ["mongod", "--wiredTigerCacheSizeGB", "0.25"]
    # No host port: a developer's own mongod already owns 27017.
    assert "ports" not in mongo
    assert mongo["healthcheck"]["test"][0] == "CMD-SHELL"
    assert "mongosh" in mongo["healthcheck"]["test"][1]
    assert any(vol.endswith(":/data/db") for vol in mongo["volumes"])
    assert "mongo-data" in compose["volumes"]


def test_both_consumers_point_at_the_compose_hostname(compose):
    for service in ("api", "pipeline"):
        assert compose["services"][service]["environment"]["MONGO_URI"] == MONGO_URI


def test_api_is_published_on_loopback_only(compose):
    assert compose["services"]["api"]["ports"] == ["127.0.0.1:3000:3000"]


def test_start_script_is_executable_and_strict():
    mode = START.stat().st_mode
    assert mode & stat.S_IXUSR, "start.sh is not executable; `./start.sh` would fail"
    text = START.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert f'CYCLE="${{CYCLE:-{DEFAULT_CYCLE}}}"' in text


def test_start_script_never_starts_the_api_before_a_promotion():
    """The order is the contract: mongo healthy, then a pipeline run that must
    report a promoted publication, then api. A blind sleep or an unconditional
    `up -d api` would let the script exit 0 over an empty database."""
    text = START.read_text(encoding="utf-8")
    order = [
        text.index("up -d mongo"),
        text.index("pipeline run --cycle"),
        text.index("up -d api"),
    ]
    assert order == sorted(order)
    assert "status: promoted" in text
    assert "refusing to start the API" in text
    # Polling the healthcheck, not sleeping for a guessed interval.
    assert "docker inspect --format" in text
    assert "sleep" in text  # only inside the poll loops


def test_api_image_is_multi_stage_and_serves_the_built_console():
    text = API_DOCKERFILE.read_text(encoding="utf-8")
    assert text.count("FROM node:22-alpine") == 2
    assert "npm ci --omit=dev" in text
    # web/dist is gitignored, so the bundle must come from the build stage.
    assert "COPY --from=web-build" in text
    assert "EXPOSE 3000" in text
    assert 'CMD ["node", "server.ts"]' in text
    assert "PORT=3000" in text and "NODE_ENV=production" in text


def test_pipeline_image_has_an_executable_named_pipeline():
    text = PIPELINE_DOCKERFILE.read_text(encoding="utf-8")
    assert "FROM python:3.13-slim" in text
    assert "ghcr.io/astral-sh/uv" in text
    assert "/usr/local/bin/pipeline" in text
    assert "uv sync --frozen" in text


def test_dockerignore_excludes_host_build_output():
    """If the host's node_modules or web/dist leaked into the context, a fresh
    clone would build differently from a dirty working tree."""
    text = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    for pattern in ("**/node_modules", "web/dist", "data/raw", ".venv"):
        assert pattern in text


def _dockerignore_excludes(path: str) -> bool:
    """Last matching line wins, which is how BuildKit resolves the file. Root
    relative paths only — enough to prove the negation beats `.env.*`."""
    excluded = False
    for line in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if fnmatch.fnmatchcase(path, line[1:] if negated else line):
            excluded = not negated
    return excluded


def test_carto_key_is_optional_and_travels_as_a_build_arg(compose):
    """The map renders without a key (styleUrl() falls back to the unkeyed raster
    style), so the key must stay optional. It has to arrive as a build arg
    because Vite inlines VITE_* into the bundle at build time — a mounted .env at
    run time would change nothing. And it must not ride along in the context: the
    root .env holds a real key, and compose reads that same file for
    substitution."""
    build = compose["services"]["api"]["build"]
    assert build["args"]["VITE_CARTO_API_KEY"] == "${VITE_CARTO_API_KEY:-}", (
        "`:-` is what keeps an unset key from warning or failing the build"
    )

    text = API_DOCKERFILE.read_text(encoding="utf-8")
    arg = text.index("ARG VITE_CARTO_API_KEY")
    assert text.index("FROM node:22-alpine AS web-build") < arg, (
        "the ARG must live in the stage that builds the console"
    )
    assert arg < text.index("FROM node:22-alpine AS runtime")
    assert arg < text.index("RUN npm run build"), (
        "an ARG declared after `npm run build` is never seen by Vite"
    )
    assert "ENV VITE_CARTO_API_KEY=$VITE_CARTO_API_KEY" in text

    assert _dockerignore_excludes(".env"), ".env would reach the build context"
    assert not _dockerignore_excludes(".env.example"), (
        ".env.example must stay in the context; `.env.*` would swallow it without "
        "the explicit negation"
    )
