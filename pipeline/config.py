"""
Single entry point for reading the YAML config tree.

Repository rule: every source-specific or section-specific fact lives in
config/*.yaml. Modules must therefore never carry a literal fallback for a value
that already exists in config. A literal fallback is a second source of truth:
it stays silent when the config key is renamed or absent, and it has already
caused real drift in this repo (two competing `base_product_url` values, four
competing `cache_dir` values, two competing `PARSER_VERSION` values).

Therefore this module raises ConfigError instead of substituting defaults.
A missing key is a bug in the config, and it should stop the pipeline. Zero fallback defaults -- that's INTENDED.
"""

from pathlib import Path
from typing import Any, Dict

import yaml


class ConfigError(RuntimeError):
    """A required configuration value is missing or malformed."""


def load_yaml(path: str) -> Dict[str, Any]:
    """Read a YAML file and assert that its top level is a mapping."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"Config file not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(
            f"{p}: expected a YAML mapping at the top level, got {type(data).__name__}"
        )
    return data


def require(mapping: Any, key: str, context: str) -> Any:
    """Return mapping[key] or raise ConfigError naming both the key and the file."""
    if not isinstance(mapping, dict):
        raise ConfigError(
            f"{context}: expected a mapping to read '{key}' from, got {type(mapping).__name__}"
        )
    if key not in mapping:
        raise ConfigError(f"{context}: missing required key '{key}'")
    return mapping[key]


def require_str(mapping: Any, key: str, context: str) -> str:
    """Return mapping[key], asserting it is a non-empty string."""
    value = require(mapping, key, context)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{context}: '{key}' must be a non-empty string, got {value!r}")
    return value


def load_crawler_config(path: str = "config/crawler.yaml") -> Dict[str, Any]:
    """Return the `crawler:` block of crawler.yaml."""
    return require(load_yaml(path), "crawler", path)


def load_airports_config(path: str = "config/airports.yaml") -> Dict[str, Any]:
    """Return the whole airports.yaml mapping."""
    return load_yaml(path)


def load_sections_config(path: str = "config/sections.yaml") -> Dict[str, Any]:
    """Return the whole sections.yaml mapping."""
    return load_yaml(path)


def load_validation_config(path: str = "config/validation.yaml") -> Dict[str,Any]:
       return require(load_yaml(path), "validation", path)


def load_database_config(path: str = "config/database.yaml") -> Dict[str, Any]:
       return require(load_yaml(path), "database", path)
