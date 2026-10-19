import pytest
from pathlib import Path

from pipeline.config import load_crawler_config, require_str
from pipeline.parse.runways import RunwayParser
from pipeline.parse.waypoints import WaypointParser
from pipeline.parse.airports import AirportParser

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

    # 4. Runway 07R Remarks (Operational Restriction - ISSUES.md §1)
    rwy_07r = feat_map["EBBR/RWY/07R"]
    assert "remarks" in rwy_07r.fields
    assert rwy_07r.fields["remarks"].value == "No TKOF before PSN H"
    assert rwy_07r.fields["remarks"].section == "EBBR AD 2.13"

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

def test_golden_values_airports():
    """Asserts authentic operational data for airports (AD 2.2, 2.3, 2.6)."""
    cache_dir = _cache_dir()
    parser = AirportParser("2026-10-01-current", "2026-10-01")

    # EBBR
    ebbr_path = cache_dir / "2026-10-01-current" / "EB-AD-2.EBBR-en-GB.html"
    if ebbr_path.exists():
        ebbr_ad, _ = parser.parse_airport_summary("EBBR", ebbr_path.read_text(encoding="utf-8"), ebbr_path.name, "https://ops.skeyes.be", "2026-10-19T00:00:00Z")
        assert ebbr_ad.fields["elevation"].value == 175.0
        assert ebbr_ad.fields["elevation"].unit == "FT"
        assert ebbr_ad.fields["hoursOperator"].value == "H24"
        assert ebbr_ad.fields["rffsCategory"].value == "CAT 10"
        lon, lat = ebbr_ad.geometry.coordinates
        assert round(lon, 4) == 4.4844
        assert round(lat, 4) == 50.9014

    # EBCI
    ebci_path = cache_dir / "2026-10-01-current" / "EB-AD-2.EBCI-en-GB.html"
    if ebci_path.exists():
        ebci_ad, _ = parser.parse_airport_summary("EBCI", ebci_path.read_text(encoding="utf-8"), ebci_path.name, "https://ops.skeyes.be", "2026-10-19T00:00:00Z")
        assert ebci_ad.fields["elevation"].value == 606.0
        assert "CAT 7" in ebci_ad.fields["rffsCategory"].value

    # EBLG
    eblg_path = cache_dir / "2026-10-01-current" / "EB-AD-2.EBLG-en-GB.html"
    if eblg_path.exists():
        eblg_ad, _ = parser.parse_airport_summary("EBLG", eblg_path.read_text(encoding="utf-8"), eblg_path.name, "https://ops.skeyes.be", "2026-10-19T00:00:00Z")
        assert eblg_ad.fields["elevation"].value == 651.0
        assert "CAT 10" in eblg_ad.fields["rffsCategory"].value

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
