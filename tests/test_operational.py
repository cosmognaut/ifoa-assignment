"""
AD 2.21 noise abatement and AD 2.20 local aerodrome regulations.

Real cached documents, no MongoDB, no network. The point of these features is the
verbatim text with provenance, so the tests pin the text (length and section), the
three derived facts, and the natural-key contract — and they check `pprRequired`
against the actual document text rather than assuming false.
"""
import re
from pathlib import Path

import pytest

from pipeline.config import load_crawler_config, load_validation_config, require, require_str
from pipeline.models.feature import FeatureType
from pipeline.parse.operational import OperationalParser

CYCLE = "2026-10-01-current"
EFFECTIVE = "2026-10-01"
CACHE_DIR = Path(require_str(load_crawler_config(), "cache_dir", "crawler.yaml"))
SOURCE_URL = "https://ops.skeyes.be/html/belgocontrol_static/eaip/eAIP_Main/html/eAIP/"
RETRIEVED_AT = "2026-10-19T00:00:00+00:00"
AIRPORTS = ("EBBR", "EBCI", "EBLG")


def _parse(icao: str):
    path = CACHE_DIR / CYCLE / f"EB-AD-2.{icao}-en-GB.html"
    if not path.exists():
        pytest.skip(f"cached AD 2 document not present: {path}")
    parser = OperationalParser(CYCLE, EFFECTIVE)
    html = path.read_text(encoding="utf-8")
    noise, noise_issues = parser.parse_noise(icao, html, path.name, SOURCE_URL, RETRIEVED_AT)
    local, local_issues = parser.parse_local_regulations(
        icao, html, path.name, SOURCE_URL, RETRIEVED_AT
    )
    return noise, noise_issues, local, local_issues


def _operational_key_regex() -> re.Pattern:
    """The validator's own pattern, loaded from config rather than restated here."""
    cfg = load_validation_config()
    formats = require(cfg, "natural_key_formats", "validation.yaml")
    return re.compile(require_str(formats, "operationalInfo", "validation.yaml natural_key_formats"))


def test_noise_feature_carries_aircraft_types_and_verbatim_text():
    noise, issues, _, _ = _parse("EBBR")

    assert noise is not None
    assert noise.feature_type == FeatureType.OPERATIONAL_INFO
    assert noise.natural_key == "EBBR/OPS/NOISE"
    assert noise.parent_key == "EBBR"
    assert noise.geometry is None
    assert issues == []

    types = noise.fields["noiseRelevantAircraftTypes"]
    assert isinstance(types.value, list) and types.value
    assert "A388" in types.value
    assert all(re.match(r"^[A-Z][A-Z0-9]{1,3}$", code) for code in types.value)
    # Document order is preserved and the header cell is not a type code.
    assert types.value[0] == "A124"

    text = noise.fields["noiseAbatementText"]
    assert len(text.value) > 500
    assert text.section == "EBBR AD 2.21"
    assert text.raw_text == text.value
    assert "noise restrictions" in text.value.lower()


def test_airports_without_a_noise_table_omit_the_types_and_warn():
    for icao in ("EBCI", "EBLG"):
        noise, issues, _, _ = _parse(icao)
        assert noise is not None, icao
        assert "noiseRelevantAircraftTypes" not in noise.fields, icao
        assert "noiseAbatementText" in noise.fields, icao
        assert [i.rule_id for i in issues] == ["RULE_NOISE_AIRCRAFT_TYPES"], icao
        assert issues[0].severity.value == "warning"


def test_local_regulations_feature_captures_the_section_text():
    _, _, local, issues = _parse("EBBR")

    assert local is not None
    assert local.natural_key == "EBBR/OPS/LOCAL-REGS"
    assert local.parent_key == "EBBR"
    assert local.geometry is None
    assert issues == []

    text = local.fields["localRegulationsText"]
    assert len(text.value) > 500
    assert text.section == "EBBR AD 2.20"
    assert text.raw_text == text.value

    emails = local.fields["regulationContactEmails"].value
    assert isinstance(emails, list) and emails
    assert all("@" in address for address in emails)
    assert "safetymanagement@brusselsairport.be" in emails


def test_email_field_is_omitted_when_the_section_has_none():
    for icao in ("EBCI", "EBLG"):
        _, _, local, _ = _parse(icao)
        assert local is not None
        assert "@" not in local.fields["localRegulationsText"].value or (
            "regulationContactEmails" not in local.fields
        )
        assert "regulationContactEmails" not in local.fields, icao


def test_ppr_required_matches_the_document_text():
    """Verify the signal against each document instead of asserting a constant."""
    token = re.compile(r"\bPPR\b")
    phrase = re.compile(r"prior permission", re.IGNORECASE)
    seen_true = False
    for icao in AIRPORTS:
        _, _, local, _ = _parse(icao)
        text = local.fields["localRegulationsText"].value
        expected = bool(token.search(text) and phrase.search(text))

        assert isinstance(local.fields["pprRequired"].value, bool), icao
        assert local.fields["pprRequired"].value is expected, icao
        seen_true = seen_true or expected

    # EBCI publishes both signals, so the positive branch is exercised too.
    assert seen_true, "no airport exercised the PPR=true branch"


def test_ppr_requires_both_signals_independently():
    parser = OperationalParser(CYCLE, EFFECTIVE)
    base = '<div id="XXXX-AD-2.20"><p>{}</p></div>'
    cases = {
        "PPR only": ("A PPR slot is needed.", False),
        "phrase only": ("Prior permission is required.", False),
        "both": ("PPR required: prior permission is mandatory.", True),
        "neither": ("Standard regulations apply.", False),
    }
    for label, (sentence, expected) in cases.items():
        html = base.format(sentence).replace("XXXX", "EBBR")
        feature, _ = parser.parse_local_regulations(
            "EBBR", html, "synthetic.html", SOURCE_URL, RETRIEVED_AT
        )
        assert feature is not None, label
        assert feature.fields["pprRequired"].value is expected, label


def test_operational_keys_match_the_configured_pattern():
    pattern = _operational_key_regex()
    keys = set()
    for icao in AIRPORTS:
        noise, _, local, _ = _parse(icao)
        for feature in (noise, local):
            assert feature is not None
            assert pattern.match(feature.natural_key), feature.natural_key
            keys.add(feature.natural_key)

    assert keys == {
        f"{icao}/OPS/{suffix}"
        for icao in AIRPORTS
        for suffix in ("NOISE", "LOCAL-REGS")
    }
