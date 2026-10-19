import pytest
from pathlib import Path

from pipeline.config import load_crawler_config, require_str
from pipeline.parse.runways import RunwayParser
from pipeline.parse.waypoints import WaypointParser

def _cache_dir() -> Path:
    return Path(require_str(load_crawler_config(), "cache_dir", "crawler.yaml"))

def test_golden_values_ebbr():
    """Asserts authoritative real aeronautical numbers for Brussels-National (EBBR)."""
    cache_dir = _cache_dir()
    doc_path = cache_dir / "2026-10-01-current" / "EB-AD-2.EBBR-en-GB.html"
    if not doc_path.exists():
        pytest.skip("EBBR raw HTML not present")

    parser = RunwayParser("2026-10-01-current", "2026-10-01")
    feats, issues = parser.parse_airport_runways(
        "EBBR", doc_path.read_text(encoding="utf-8"), doc_path.name, "https://ops.skeyes.be", "2026-10-19T00:00:00Z"
    )
    feat_map = {f.natural_key: f for f in feats}

    # 1. Runway 25L Golden Values
    rwy_25l = feat_map["EBBR/RWY/25L"]
    assert rwy_25l.fields["lda"].value == 3210.0
    assert rwy_25l.fields["lda"].unit == "M"
    assert rwy_25l.fields["tora"].value == 3210.0
    assert rwy_25l.fields["width"].value == 45.0
    assert rwy_25l.fields["surface"].value == "ASPH"
    assert rwy_25l.geometry.type == "Point"
    # Longitude ~4.52°E, Latitude ~50.89°N
    lon, lat = rwy_25l.geometry.coordinates
    assert 4.50 < lon < 4.54
    assert 50.88 < lat < 50.92

    # 2. Runway 01 Golden Values
    rwy_01 = feat_map["EBBR/RWY/01"]
    assert rwy_01.fields["tora"].value == 2987.0
    assert rwy_01.fields["lda"].value == 2941.0
    assert rwy_01.fields["trueBearing"].value == 14.43

    # 3. Physical Runway 01-19 LineString
    rwy_pair = feat_map["EBBR/RWY/01-19"]
    assert rwy_pair.geometry.type == "LineString"
    assert len(rwy_pair.geometry.coordinates) == 2

def test_golden_values_ebci():
    """Asserts authoritative real aeronautical numbers for Charleroi (EBCI)."""
    cache_dir = _cache_dir()
    doc_path = cache_dir / "2026-10-01-current" / "EB-AD-2.EBCI-en-GB.html"
    if not doc_path.exists():
        pytest.skip("EBCI raw HTML not present")

    parser = RunwayParser("2026-10-01-current", "2026-10-01")
    feats, _ = parser.parse_airport_runways(
        "EBCI", doc_path.read_text(encoding="utf-8"), doc_path.name, "https://ops.skeyes.be", "2026-10-19T00:00:00Z"
    )
    feat_map = {f.natural_key: f for f in feats}

    rwy_06 = feat_map["EBCI/RWY/06"]
    assert rwy_06.fields["length"].value == 2905.0
    assert rwy_06.fields["width"].value == 45.0

def test_golden_values_eblg():
    """Asserts authoritative real aeronautical numbers for Liège (EBLG)."""
    cache_dir = _cache_dir()
    doc_path = cache_dir / "2026-10-01-current" / "EB-AD-2.EBLG-en-GB.html"
    if not doc_path.exists():
        pytest.skip("EBLG raw HTML not present")

    parser = RunwayParser("2026-10-01-current", "2026-10-01")
    feats, _ = parser.parse_airport_runways(
        "EBLG", doc_path.read_text(encoding="utf-8"), doc_path.name, "https://ops.skeyes.be", "2026-10-19T00:00:00Z"
    )
    feat_map = {f.natural_key: f for f in feats}

    rwy_04r = feat_map["EBLG/RWY/04R"]
    assert rwy_04r.fields["length"].value == 3690.0
    assert rwy_04r.fields["width"].value == 45.0

def test_golden_values_waypoints():
    """Asserts waypoint extraction from ENR 4.4."""
    cache_dir = _cache_dir()
    doc_path = cache_dir / "2026-10-01-current" / "EB-ENR-4.4-en-GB.html"
    if not doc_path.exists():
        pytest.skip("ENR-4.4 raw HTML not present")

    parser = WaypointParser("2026-10-01-current", "2026-10-01")
    feats, _ = parser.parse_waypoints(
        doc_path.read_text(encoding="utf-8"), doc_path.name, "https://ops.skeyes.be", "2026-10-19T00:00:00Z"
    )
    assert len(feats) > 250
    feat_map = {f.natural_key: f for f in feats}

    # ABBUW coordinates: 50°07'52"N 005°16'37"E -> Lon ~5.276944, Lat ~50.131111
    abbuw = feat_map["WPT/ABBUW"]
    lon, lat = abbuw.geometry.coordinates
    assert round(lon, 4) == 5.2769
    assert round(lat, 4) == 50.1311
