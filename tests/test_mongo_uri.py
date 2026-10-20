"""
Tests for `pipeline.load.load.resolve_mongo_uri`, the single source of truth for
which MongoDB the pipeline talks to.

The defect these pin down: `MONGO_URI` was read by `MongoLoader` but ignored by the
CLI, so a containerised run connected to `mongodb://mongo:27017/aero_pipeline` while
printing `mongodb://localhost:27017/aero_pipeline`. The log named a database the
pipeline was not using. The precedence must therefore be asserted, not inferred.

Hermetic: no MongoClient is constructed and no socket is opened. Every test sets or
deletes MONGO_URI explicitly, so an ambient `MONGO_URI` in the shell or in a compose
environment cannot make these pass or fail for the wrong reason.
"""

from typing import Any, Dict

import pytest

from pipeline.config import ConfigError
from pipeline.load.load import resolve_mongo_uri

ENV_URI = "mongodb://mongo:27017/aero_pipeline"
ARG_URI = "mongodb://argument-host:27017/argument_db"
CFG_URI = "mongodb://config-host:27017/config_db"


def _db_cfg(uri: str) -> Dict[str, Any]:
    """The `database:` block as load_database_config() returns it."""
    return {"uri": uri, "name": "aero_pipeline", "collections": {}}


def test_env_var_beats_argument_and_config(monkeypatch):
    """MONGO_URI wins over a contradictory explicit argument and a contradictory config."""
    monkeypatch.setenv("MONGO_URI", ENV_URI)

    assert resolve_mongo_uri(ARG_URI, _db_cfg(CFG_URI)) == ENV_URI


def test_argument_is_used_when_env_is_absent(monkeypatch):
    monkeypatch.delenv("MONGO_URI", raising=False)

    assert resolve_mongo_uri(ARG_URI, _db_cfg(CFG_URI)) == ARG_URI


def test_config_fallback_when_env_and_argument_are_absent(monkeypatch):
    monkeypatch.delenv("MONGO_URI", raising=False)

    assert resolve_mongo_uri(None, _db_cfg(CFG_URI)) == CFG_URI


def test_missing_everywhere_raises_config_error(monkeypatch):
    """No env, no argument, and a config block without `uri`: ConfigError, not a default."""
    monkeypatch.delenv("MONGO_URI", raising=False)

    with pytest.raises(ConfigError) as excinfo:
        resolve_mongo_uri(None, {"name": "aero_pipeline", "collections": {}})

    assert "uri" in str(excinfo.value)
    assert "database.yaml" in str(excinfo.value)


def test_empty_env_var_does_not_win(monkeypatch):
    """An empty MONGO_URI is falsy, exactly as it was in the loader's `or` chain."""
    monkeypatch.setenv("MONGO_URI", "")

    assert resolve_mongo_uri(ARG_URI, _db_cfg(CFG_URI)) == ARG_URI


def test_db_cfg_is_read_from_disk_when_not_supplied(monkeypatch):
    """The CLI's export path passes no config block; the resolver must load one itself."""
    monkeypatch.delenv("MONGO_URI", raising=False)

    assert resolve_mongo_uri() == "mongodb://localhost:27017/aero_pipeline"
