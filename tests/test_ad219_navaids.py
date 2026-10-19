"""
AD 2.19 — airport radio navigation and landing aids.

Runs against the committed raw documents (no MongoDB, no network). The table mixes
flat aid rows with ILS sub-rows grouped under a caption, so these tests pin the row
shapes, the derived aid types, the ILS category/component and the natural-key rule.
"""
import re
from pathlib import Path

import pytest

from pipeline.config import load_crawler_config, load_validation_config, require, require_str
from pipeline.models.feature import FeatureType
from pipeline.parse.navaids import NavaidParser

CYCLE = "2026-10-01-current"
EFFECTIVE = "2026-10-01"
CACHE_DIR = Path(require_str(load_crawler_config(), "cache_dir", "crawler.yaml"))
SOURCE_URL = "https://ops.skeyes.be/html/belgocontrol_static/eaip/eAIP_Main/html/eAIP/"
RETRIEVED_AT = "2026-10-19T00:00:00+00:00"


def _doc_path(icao: str) -> Path:
    return CACHE_DIR / CYCLE / f"EB-AD-2.{icao}-en-GB.html"


def _parse(icao: str):
    path = _doc_path(icao)
    if not path.exists():
        pytest.skip(f"cached AD 2 document not present: {path}")
    parser = NavaidParser(CYCLE, EFFECTIVE)
    return parser.parse_airport_navaids(
        icao=icao,
        html_content=path.read_text(encoding="utf-8"),
        document_filename=path.name,
        source_url=SOURCE_URL,
        retrieved_at=RETRIEVED_AT,
    )


def _navaid_key_regex() -> re.Pattern:
    """The same pattern the validator applies — read from config, not restated here."""
    cfg = load_validation_config()
    formats = require(cfg, "natural_key_formats", "validation.yaml")
    return re.compile(require_str(formats, "navaid", "validation.yaml natural_key_formats"))


def test_flat_aid_dvordme_is_parsed_with_geometry():
    features, _ = _parse("EBBR")
    by_key = {f.natural_key: f for f in features}

    assert "NAV/EBBR/BUB" in by_key
    bub = by_key["NAV/EBBR/BUB"]

    assert bub.feature_type == FeatureType.NAVAID
    assert bub.parent_key == "EBBR"
    assert bub.fields["frequency"].value == 114.6
    assert bub.fields["frequency"].value == pytest.approx(114.6)
    assert bub.fields["frequencyUnit"].value == "MHZ"
    assert "DVOR" in bub.fields["aidType"].value and "DME" in bub.fields["aidType"].value
    assert bub.fields["associatedAirport"].value == "EBBR"
    assert bub.fields["dmeElevation"].value == 200
    assert bub.fields["dmeElevation"].unit == "FT"

    assert bub.geometry is not None
    lon, lat = bub.geometry.coordinates
    # The AIP prints "505408.4N 0043217.1E" — latitude 50°54'08.4"N, longitude 004°32'17.1"E.
    assert lat == pytest.approx(50.902333, abs=0.01)
    assert lon == pytest.approx(4.538083, abs=0.01)


def test_airport_and_enroute_sources_agree_on_the_same_facility():
    """AD 2.19 and ENR 4.1 describe the same BUB station; the two parses must agree."""
    parser = NavaidParser(CYCLE, EFFECTIVE)
    enr_path = CACHE_DIR / CYCLE / "EB-ENR-4.1-en-GB.html"
    if not enr_path.exists():
        pytest.skip("cached ENR 4.1 document not present")
    enr_features, _ = parser.parse_enroute_navaids(
        enr_path.read_text(encoding="utf-8"), enr_path.name, SOURCE_URL, RETRIEVED_AT
    )
    enr_bub = next(f for f in enr_features if f.natural_key == "NAV/BUB")
    ad_features, _ = _parse("EBBR")
    ad_bub = next(f for f in ad_features if f.natural_key == "NAV/EBBR/BUB")

    assert ad_bub.geometry is not None and enr_bub.geometry is not None
    for ad_value, enr_value in zip(ad_bub.geometry.coordinates, enr_bub.geometry.coordinates):
        assert ad_value == pytest.approx(enr_value, abs=1e-3)


def test_ils_components_carry_category_and_component():
    features, _ = _parse("EBBR")
    ils = [f for f in features if "ilsCategory" in f.fields]

    assert ils, "no ILS components parsed from EBBR AD 2.19"
    assert {f.fields["component"].value for f in ils} == {"LOC", "GP", "DME"}
    for feature in ils:
        assert feature.fields["ilsCategory"].value.startswith("CAT")
        assert isinstance(feature.fields["frequency"].value, float)
        assert feature.geometry is not None

    loc = next(f for f in ils if f.fields["component"].value == "LOC")
    assert loc.fields["designator"].value in {"IBX", "IBM", "IBL", "IBR"}
    assert loc.fields["frequencyUnit"].value == "MHZ"
    dme = next(f for f in ils if f.fields["component"].value == "DME")
    assert dme.fields["frequencyUnit"].value == "CH"


def test_every_key_is_unique_and_matches_the_validator_pattern():
    pattern = _navaid_key_regex()
    seen = set()
    for icao in ("EBBR", "EBCI", "EBLG"):
        features, _ = _parse(icao)
        assert features, f"no AD 2.19 features parsed for {icao}"
        for feature in features:
            assert feature.geometry is not None, f"{feature.natural_key} has no geometry"
            assert pattern.match(feature.natural_key), feature.natural_key
            assert feature.parent_key == icao
            assert feature.natural_key not in seen, f"duplicate key {feature.natural_key}"
            seen.add(feature.natural_key)
    # Marker beacons ("dash / dash") are not aids and must never become features.
    assert not any("dash" in key.lower() for key in seen)


def test_enroute_key_still_matches_the_pattern():
    pattern = _navaid_key_regex()

    assert pattern.match("NAV/AFI")
    assert pattern.match("NAV/EBBR/BUB")
    assert not pattern.match("NAV/")  # a key with no designator is still rejected


def test_unparsable_position_warns_and_emits_no_feature():
    snippet = """
    <div id="EBBR-AD-2.19">
      <table>
        <tr>
          <td>Type of aid ( MAG VAR )</td><td>ID</td><td>Frequency</td><td>Hours of operation</td>
          <td>Position of transmitting antenna</td><td>DME antenna elevation</td><td>Remarks</td>
        </tr>
        <tr><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td><td>7</td></tr>
        <tr>
          <td>DVOR / DME</td><td>XXX</td><td>110.000 MHZ</td><td>H24</td>
          <td>NOT-A-POSITION</td><td></td><td></td>
        </tr>
      </table>
    </div>
    """
    parser = NavaidParser(CYCLE, EFFECTIVE)
    features, issues = parser.parse_airport_navaids(
        icao="EBBR",
        html_content=snippet,
        document_filename="synthetic.html",
        source_url=SOURCE_URL,
        retrieved_at=RETRIEVED_AT,
    )

    assert features == []
    assert len(issues) == 1
    assert issues[0].rule_id == "RULE_COORDINATE_FORMAT"
    assert issues[0].severity.value == "warning"
    assert "NOT-A-POSITION" in issues[0].raw_snippet
