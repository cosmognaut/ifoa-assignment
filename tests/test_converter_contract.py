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

    `driven` is False for specs annotated `converter_driven: false`. That flag is
    documentation of intent: such a field's parser derives the value itself and no
    dispatch site reads the flag. It is NOT an exemption from resolution -- a spec
    that names a converter must name a real one whether or not it is dispatched.
    """
    for section in sections_cfg["sections"]:
        for field_name, field_spec in (section.get("fields") or {}).items():
            if isinstance(field_spec, dict) and "converter" in field_spec:
                driven = field_spec.get("converter_driven", True) is not False
                yield section["id"], field_name, field_spec["converter"], driven


# Deliberately empty: no shipped spec is permitted to name a converter outside the
# registry, dispatched or not. AD-2.20/pprRequired used to sit here as a pinned
# exemption for the dead "boolean" name; the spec now declares no converter key at
# all, which is the honest shape. Add nothing here without a registry entry to match.
INERT_CONVERTER_SPECS: set = set()


def test_every_declared_converter_in_real_config_exists():
    """The shipped config must not reference a converter that does not exist."""
    sections_cfg = yaml.safe_load(SECTIONS_YAML.read_text(encoding="utf-8"))

    offenders = [
        f"{section_id}.{field_name} -> {converter!r}"
        for section_id, field_name, converter, _ in _declared_converters(sections_cfg)
        if converter not in CONVERTER_REGISTRY
    ]

    assert not offenders, (
        "config/sections.yaml declares converters absent from CONVERTER_REGISTRY: "
        + ", ".join(offenders)
    )

    # Non-dispatch specs get no special treatment above, but assert the pin is still
    # honest: anything sitting in it must actually exist in the real config.
    named = {
        (section_id, field_name, converter)
        for section_id, field_name, converter, _ in _declared_converters(sections_cfg)
    }
    assert INERT_CONVERTER_SPECS <= named, (
        "INERT_CONVERTER_SPECS pins specs that are no longer declared: "
        + repr(sorted(INERT_CONVERTER_SPECS - named))
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


def test_converter_driven_false_is_not_an_exemption_from_resolution():
    """A non-dispatch spec naming a nonexistent converter must still raise."""
    sections_cfg = {
        "sections": [
            {
                "id": "AD-2.20",
                "fields": {
                    "pprRequired": {
                        "source": "section-text",
                        "converter": "total_nonsense",
                        "converter_driven": False,
                    },
                },
            }
        ]
    }

    with pytest.raises(ConfigError) as exc:
        validate_section_converters(sections_cfg)

    message = str(exc.value)
    assert "AD-2.20" in message
    assert "pprRequired" in message
    assert "total_nonsense" in message


def test_non_dispatch_spec_without_converter_key_is_accepted():
    """
    `converter_driven: false` plus no `converter` key is the honest shape for a field
    whose parser derives the value itself: nothing is declared, so nothing resolves.
    """
    sections_cfg = {
        "sections": [
            {
                "id": "AD-2.20",
                "fields": {
                    "pprRequired": {
                        "source": "section-text",
                        "converter_driven": False,
                        "emit_when": "standalone PPR token AND a 'prior permission' phrase",
                    },
                },
            }
        ]
    }

    assert validate_section_converters(sections_cfg) is None

    # The shipped config must actually have that shape -- guards a regression that
    # re-adds a `converter` key to the real AD-2.20/pprRequired spec.
    shipped = yaml.safe_load(SECTIONS_YAML.read_text(encoding="utf-8"))
    spec = next(s for s in shipped["sections"] if s["id"] == "AD-2.20")["fields"][
        "pprRequired"
    ]
    assert "converter" not in spec, (
        "AD-2.20/pprRequired declares a converter it can never dispatch: "
        + repr(spec.get("converter"))
    )
    assert spec["converter_driven"] is False


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
