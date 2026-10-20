# syntax=docker/dockerfile:1

# python:3.13-slim, not alpine: lxml ships manylinux wheels for cp313, so this
# needs no compiler. On musl there is no wheel and lxml falls back to a source
# build, which would drag in libxml2-dev/libxslt-dev/gcc.
FROM python:3.13-slim

# uv resolves against the committed uv.lock, so the image's dependency set is the
# one the tests ran against. UV_PYTHON_DOWNLOADS=never keeps the base
# interpreter; UV_LINK_MODE=copy avoids hardlink warnings across layers.
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
# --no-install-project: `pipeline/` is a namespace package used from the source
# tree, so there is nothing to install — only its dependencies.
RUN uv sync --frozen --no-dev --no-install-project

COPY pipeline/ ./pipeline/

ENV PATH="/app/.venv/bin:$PATH"

# `docker compose run pipeline pipeline run --cycle ...` needs an executable
# called `pipeline` on PATH; there is no console-script for it in pyproject.toml,
# so this wrapper is the image's entry point rather than a change to the repo's
# packaging.
RUN printf '#!/bin/sh\nexec python -m pipeline.cli "$@"\n' > /usr/local/bin/pipeline \
    && chmod +x /usr/local/bin/pipeline

# config/ and data/raw are mounted read-only by compose: the run reads the
# committed cache and never writes to it.
CMD ["pipeline", "run", "--cycle", "2026-10-01-current"]
