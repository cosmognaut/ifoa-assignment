import pytest
from pathlib import Path

from pipeline.config import load_crawler_config, require_str
from pipeline.models.feature import FeatureType
from pipeline.parse.runways import RunwayParser
from pipeline.parse.waypoints import WaypointParser
from pipeline.parse.airports import AirportParser
from pipeline.parse.navaids import NavaidParser
from pipeline.parse.routes import RouteParser
from pipeline.parse.airspace import AirspaceParser

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

def test_golden_values_navaids():
    """Asserts authentic operational data for navaids (ENR 4.1): AFI, ANT, BUB, BBE."""
    cache_dir = _cache_dir()
    doc_path = cache_dir / "2026-10-01-current" / "EB-ENR-4.1-en-GB.html"
    if not doc_path.exists():
        pytest.skip("ENR-4.1 raw HTML not present")

    parser = NavaidParser("2026-10-01-current", "2026-10-01")
    feats, issues = parser.parse_enroute_navaids(
        doc_path.read_text(encoding="utf-8"), doc_path.name, "https://ops.skeyes.be", "2026-10-19T00:00:00Z"
    )
    assert len(feats) >= 20
    assert len(issues) == 0
    feat_map = {f.natural_key: f for f in feats}

    # 1. AFI (Affligem DVOR/DME)
    afi = feat_map["NAV/AFI"]
    assert afi.fields["designator"].value == "AFI"
    assert afi.fields["frequency"].value == 114.9
    assert afi.fields["frequencyUnit"].value == "MHZ"
    assert afi.fields["dmeElevation"].value == 300.0
    assert afi.fields["navaidType"].value == "DVOR/DME"
    lon, lat = afi.geometry.coordinates
    assert round(lon, 4) == 4.1389
    assert round(lat, 4) == 50.9078

    # 2. ANT (Antwerpen DVOR/DME)
    ant = feat_map["NAV/ANT"]
    assert ant.fields["frequency"].value == 113.5
    assert ant.fields["dmeElevation"].value == 100.0
    assert ant.fields["navaidType"].value == "DVOR/DME"

    # 3. BUB (Brussels DVOR/DME)
    bub = feat_map["NAV/BUB"]
    assert bub.fields["frequency"].value == 114.6
    assert bub.fields["dmeElevation"].value == 200.0

    # 4. BBE (Beauvechain TACAN)
    bbe = feat_map["NAV/BBE"]
    assert bbe.fields["frequency"].value == 107.0
    assert bbe.fields["frequencyUnit"].value == "CH"
    assert bbe.fields["navaidType"].value == "TACAN"

def test_golden_values_routes():
    """Asserts route parsing, waypoint sequence, and vertical limits for ENR 3.2."""
    cache_dir = _cache_dir()
    doc_path = cache_dir / "2026-10-01-current" / "EB-ENR-3.2-en-GB.html"
    if not doc_path.exists():
        pytest.skip("ENR-3.2 raw HTML not present")

    wpt_parser = WaypointParser("2026-10-01-current", "2026-10-01")
    wpt_doc = cache_dir / "2026-10-01-current" / "EB-ENR-4.4-en-GB.html"
    wpt_feats, _ = wpt_parser.parse_waypoints(wpt_doc.read_text(encoding="utf-8"), wpt_doc.name, "https://ops.skeyes.be", "2026-10-19T00:00:00Z")
    known_wpts = {f.natural_key for f in wpt_feats}

    nav_parser = NavaidParser("2026-10-01-current", "2026-10-01")
    nav_doc = cache_dir / "2026-10-01-current" / "EB-ENR-4.1-en-GB.html"
    nav_feats, _ = nav_parser.parse_enroute_navaids(nav_doc.read_text(encoding="utf-8"), nav_doc.name, "https://ops.skeyes.be", "2026-10-19T00:00:00Z")
    known_navs = {f.natural_key for f in nav_feats}

    route_parser = RouteParser("2026-10-01-current", "2026-10-01")
    feats, issues = route_parser.parse_routes_document(
        doc_path.read_text(encoding="utf-8"), "ENR-3.2", doc_path.name, "https://ops.skeyes.be", "2026-10-19T00:00:00Z",
        known_waypoint_keys=known_wpts, known_navaid_keys=known_navs
    )
    assert len(feats) >= 40
    feat_map = {f.natural_key: f for f in feats}

    # Verify L608
    assert "RTE/L608" in feat_map
    l608 = feat_map["RTE/L608"]
    seq = l608.fields["waypointSequence"].value
    assert "WPT/SASKI" in seq
    assert "WPT/KEGIT" in seq
    assert l608.geometry.type == "LineString"
    assert len(l608.geometry.coordinates) >= 5

    # Verify vertical limits
    upper = l608.fields["upperLimit"].value
    assert upper["value"] == 660.0
    assert upper["unit"] == "FL"
    assert upper["reference"] == "AMSL"

    lower = l608.fields["lowerLimit"].value
    assert lower["value"] == 195.0
    assert lower["unit"] == "FL"
    assert lower["reference"] == "AMSL"

def test_airspace_and_sua_geometry_is_strictly_none_and_has_lateral_limits():
    """Asserts ABSOLUTE PROHIBITION: geometry is None and lateralLimitsRaw is non-empty for all airspace/SUA."""
    cache_dir = _cache_dir()
    parser = AirspaceParser("2026-10-01-current", "2026-10-01")

    # 1. ENR-2.1 Airspace
    doc_21 = cache_dir / "2026-10-01-current" / "EB-ENR-2.1-en-GB.html"
    if doc_21.exists():
        asp_feats, _ = parser.parse_airspace_enr21(doc_21.read_text(encoding="utf-8"), doc_21.name, "https://ops.skeyes.be", "2026-10-19T00:00:00Z")
        assert len(asp_feats) >= 50
        for f in asp_feats:
            assert f.feature_type == FeatureType.AIRSPACE
            assert f.geometry is None, f"VIOLATION: geometry must be None for {f.natural_key}"
            assert "lateralLimitsRaw" in f.fields
            assert len(f.fields["lateralLimitsRaw"].value.strip()) > 0
            assert f.fields["lateralLimitsRaw"].section == "ENR 2.1"

    # 2. ENR-5.1 / 5.2 Special Use Airspace
    doc_51 = cache_dir / "2026-10-01-current" / "EB-ENR-5.1-en-GB.html"
    if doc_51.exists():
        sua_feats, _ = parser.parse_special_use_document(doc_51.read_text(encoding="utf-8"), "ENR-5.1", doc_51.name, "https://ops.skeyes.be", "2026-10-19T00:00:00Z")
        assert len(sua_feats) >= 50
        for f in sua_feats:
            assert f.feature_type == FeatureType.SPECIAL_USE_AREA
            assert f.geometry is None, f"VIOLATION: geometry must be None for {f.natural_key}"
            assert "lateralLimitsRaw" in f.fields
            assert len(f.fields["lateralLimitsRaw"].value.strip()) > 0
            assert f.fields["lateralLimitsRaw"].section == "ENR 5.1"
