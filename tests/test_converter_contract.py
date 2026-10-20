"""
Guards for the converter contract.

Before this file existed, airports.py and runways.py read a field's converter with
`spec.get("converter", "text")` and dispatched with
`CONVERTER_REGISTRY.get(name, lambda x: (x, None))`. Both halves were silent: a
misspelled converter such as "elevaton" degraded to the text passthrough and no
test noticed. A declared converter is now a contract -- it either resolves or the
run stops naming the field.

pipeline/config.py already states the rule these tests enforce: a missing config
value raises ConfigError instead of acquiring a literal default.
"""

from pathlib import Path

import pytest
import yaml

from pipeline.config import ConfigError
from pipeline.parse.converters import (
    CONVERTER_REGISTRY,
    resolve_converter,
    validate_section_converters,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SECTIONS_YAML = REPO_ROOT / "config" / "sections.yaml"


def _declared_converters(sections_cfg: dict):
    """
    Yield (section_id, field_name, converter_name, driven) for every declared converter.

    `driven` is False for specs annotated `converter_driven: false`, which name a
    converter only to describe the emitted type; their parser derives the value
    itself and never dispatches through CONVERTER_REGISTRY.
    """
    for section in sections_cfg["sections"]:
        for field_name, field_spec in (section.get("fields") or {}).items():
            if isinstance(field_spec, dict) and "converter" in field_spec:
                driven = field_spec.get("converter_driven", True) is not False
                yield section["id"], field_name, field_spec["converter"], driven


# Specs allowed to name a converter that is not in the registry because they are
# explicitly annotated as not converter-driven. Pinned so the annotation cannot be
# used as a quiet way to smuggle broken converter references past this test.
INERT_CONVERTER_SPECS = {("AD-2.20", "pprRequired", "boolean")}


def test_every_declared_converter_in_real_config_exists():
    """The shipped config must not reference a converter that does not exist."""
    sections_cfg = yaml.safe_load(SECTIONS_YAML.read_text(encoding="utf-8"))

    offenders = [
        f"{section_id}.{field_name} -> {converter!r}"
        for section_id, field_name, converter, driven in _declared_converters(sections_cfg)
        if driven and converter not in CONVERTER_REGISTRY
    ]

    assert not offenders, (
        "config/sections.yaml declares converters absent from CONVERTER_REGISTRY: "
        + ", ".join(offenders)
    )

    # Every unknown-converter spec must be one of the pinned, intentional exemptions --
    # otherwise a real typo could be silenced by adding `converter_driven: false`.
    exempt = {
        (section_id, field_name, converter)
        for section_id, field_name, converter, driven in _declared_converters(sections_cfg)
        if not driven and converter not in CONVERTER_REGISTRY
    }
    assert exempt <= INERT_CONVERTER_SPECS, (
        "unexpected non-dispatch converter specs: " + repr(sorted(exempt - INERT_CONVERTER_SPECS))
    )

    # The validator must agree with the raw scan above.
    validate_section_converters(sections_cfg)


def test_validator_rejects_misspelled_converter():
    sections_cfg = {
        "sections": [
            {
                "id": "AD-2.13",
                "fields": {
                    "thresholdElevation": {"source": "table", "converter": "elevaton"},
                },
            }
        ]
    }

    with pytest.raises(ConfigError) as exc:
        validate_section_converters(sections_cfg)

    message = str(exc.value)
    assert "AD-2.13" in message
    assert "thresholdElevation" in message
    assert "elevaton" in message


def test_validator_accepts_minimal_valid_sections():
    sections_cfg = {
        "sections": [
            {"id": "AD-2.12", "fields": {"designator": {"converter": "text"}}},
            {"id": "AD-2.13", "fields": {"thresholdElevation": {"converter": "elevation"}}},
        ]
    }

    assert validate_section_converters(sections_cfg) is None


def test_misspelled_converter_raises_instead_of_falling_back_to_text():
    """
    Exercise the real dispatch path. A bad name must raise naming the field rather
    than returning the raw text untouched the way the old lambda did.
    """
    field_spec = {"source": "table", "converter": "elevaton"}

    with pytest.raises(ConfigError) as exc:
        resolve_converter(field_spec, "sections.yaml section 'AD-2.13' field 'thresholdElevation'")

    message = str(exc.value)
    assert "elevaton" in message
    assert "thresholdElevation" in message

    # A declared converter still resolves to the shipped callable.
    name, fn = resolve_converter({"converter": "elevation"}, "probe")
    assert name == "elevation"
    assert fn is CONVERTER_REGISTRY["elevation"]

    # An omitted converter is a config bug, not an implicit "text".
    with pytest.raises(ConfigError):
        resolve_converter({"source": "table"}, "sections.yaml section 'AD-2.13' field 'x'")
