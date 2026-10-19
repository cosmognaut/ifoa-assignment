"""
AD 2.14 approach and runway lighting.

Real cached documents, no MongoDB, no network. The tables are ragged (a caption row,
then key/value pairs whose meaning depends on the section label above them), so these
tests pin the derived values, the natural-key contract, the link to the runway
direction, and the fallback when a runway publishes no readable pairs.
"""
import re
from pathlib import Path

import pytest

from pipeline.config import load_crawler_config, load_validation_config, require, require_str
from pipeline.models.feature import FeatureType
from pipeline.cli import _parse_airport_runways
from pipeline.parse.lighting import LightingParser

CYCLE = "2026-10-01-current"
EFFECTIVE = "2026-10-01"
CACHE_DIR = Path(require_str(load_crawler_config(), "cache_dir", "crawler.yaml"))
SOURCE_URL = "https://ops.skeyes.be/html/belgocontrol_static/eaip/eAIP_Main/html/eAIP/"
RETRIEVED_AT = "2026-10-19T00:00:00+00:00"
AIRPORTS = ("EBBR", "EBCI", "EBLG")


def _html(icao: str) -> str:
    path = CACHE_DIR / CYCLE / f"EB-AD-2.{icao}-en-GB.html"
    if not path.exists():
        pytest.skip(f"cached AD 2 document not present: {path}")
    return path.read_text(encoding="utf-8")


def _parse(icao: str):
    parser = LightingParser(CYCLE, EFFECTIVE)
    return parser.parse_airport_lighting(icao, _html(icao), f"EB-AD-2.{icao}-en-GB.html", SOURCE_URL, RETRIEVED_AT)


def _key_regex() -> re.Pattern:
    cfg = load_validation_config()
    formats = require(cfg, "natural_key_formats", "validation.yaml")
    return re.compile(require_str(formats, "operationalInfo", "validation.yaml natural_key_formats"))


def test_runway_lighting_carries_approach_type_vasis_and_meht():
    features, issues = _parse("EBBR")
    by_key = {f.natural_key: f for f in features}

    assert issues == []
    assert "EBBR/LIGHTING/01" in by_key
    lighting = by_key["EBBR/LIGHTING/01"]

    assert lighting.feature_type == FeatureType.OPERATIONAL_INFO
    assert lighting.geometry is None
    assert lighting.fields["approachLightingType"].value == "PALS CAT II / III"
    assert "PAPI" in lighting.fields["vasisType"].value
    assert lighting.fields["meht"].value == pytest.approx(59.0)
    assert lighting.fields["meht"].unit == "FT"
    assert lighting.fields["approachLightingLength"].value == pytest.approx(900.0)
    assert lighting.fields["approachLightingLength"].unit == "M"
    assert lighting.fields["approachLightingIntensity"].value == "LIH"
    assert lighting.fields["thresholdLightColour"].value == "green"
    assert lighting.fields["touchdownZoneLights"].value == "900 M"
    assert lighting.fields["lightingRawText"].section == "EBBR AD 2.14"
    assert "Approach lighting system" in lighting.fields["lightingRawText"].value


def test_every_key_matches_the_pattern_and_links_to_a_real_runway_direction():
    pattern = _key_regex()
    seen = set()
    for icao in AIRPORTS:
        features, _ = _parse(icao)
        assert features, f"no lighting features for {icao}"

        runway_features, _ = _parse_airport_runways(CYCLE, icao, CACHE_DIR)
        direction_keys = {f.natural_key for f in runway_features if f.feature_type.value == "runwayDirection"}

        for feature in features:
            assert pattern.match(feature.natural_key), feature.natural_key
            assert feature.parent_key is not None
            assert feature.parent_key in direction_keys, (feature.natural_key, feature.parent_key)
            assert feature.natural_key not in seen
            seen.add(feature.natural_key)

    assert len(seen) == 12  # EBBR 6 runways + EBCI 2 + EBLG 4


def test_a_caption_without_pairs_warns_but_still_emits_the_raw_text():
    snippet = """
    <div id="EBBR-AD-2.14">
      <table>
        <tr><td>RWY 99</td></tr>
        <tr><td>Remarks</td><td>Lighting data not published this cycle</td></tr>
      </table>
    </div>
    """
    parser = LightingParser(CYCLE, EFFECTIVE)
    features, issues = parser.parse_airport_lighting(
        "EBBR", snippet, "synthetic.html", SOURCE_URL, RETRIEVED_AT
    )

    assert len(features) == 1
    feature = features[0]
    assert feature.natural_key == "EBBR/LIGHTING/99"
    assert feature.parent_key == "EBBR/RWY/99"
    assert list(feature.fields) == ["lightingRawText"]
    assert "RWY 99" in feature.fields["lightingRawText"].value

    assert [i.rule_id for i in issues] == ["RULE_LIGHTING_UNPARSED"]
    assert issues[0].severity.value == "warning"
    assert "99" in issues[0].feature_key


def test_unparseable_caption_is_skipped_with_a_warning():
    snippet = """
    <div id="EBBR-AD-2.14">
      <table><tr><td>Runway lighting summary</td></tr><tr><td>Type:</td><td>PALS</td></tr></table>
    </div>
    """
    parser = LightingParser(CYCLE, EFFECTIVE)
    features, issues = parser.parse_airport_lighting(
        "EBBR", snippet, "synthetic.html", SOURCE_URL, RETRIEVED_AT
    )

    assert features == []
    assert [i.rule_id for i in issues] == ["RULE_LIGHTING_DESIGNATOR"]


def test_existing_keys_still_validate():
    pattern = _key_regex()
    navaid_pattern = re.compile(
        require_str(
            require(load_validation_config(), "natural_key_formats", "validation.yaml"),
            "navaid",
            "validation.yaml natural_key_formats",
        )
    )

    assert navaid_pattern.match("NAV/AFI")
    assert navaid_pattern.match("NAV/EBBR/IBX/GP")
    assert pattern.match("EBBR/OPS/NOISE")
    assert pattern.match("EBBR/OPS/LOCAL-REGS")
    assert pattern.match("EBLG/LIGHTING/22R")
    assert not pattern.match("EBBR/LIGHTING/TOOLONG")
