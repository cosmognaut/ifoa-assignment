import pytest
from pathlib import Path

from pipeline.config import load_crawler_config, load_validation_config, require_str
from pipeline.parse.converters import (
    dms_to_dd, parse_runway_dimensions, parse_bearing, parse_distance, parse_elevation,
    CONVERTER_REGISTRY
)
from pipeline.parse.runways import RunwayParser
from pipeline.models.feature import Feature, FeatureType, FeatureField
from pipeline.utils.hash_utils import compute_content_hash
from pipeline.models.validation_issue import ValidationIssue, IssueSeverity
from pipeline.validate.rules import ValidationEngine

def _cache_dir() -> Path:
    return Path(require_str(load_crawler_config(), "cache_dir", "crawler.yaml"))

def test_converter_dispatch_registry():
    assert "coordinates" in CONVERTER_REGISTRY
    assert "dimensions" in CONVERTER_REGISTRY
    assert "bearing" in CONVERTER_REGISTRY
    assert "distance" in CONVERTER_REGISTRY
    assert "elevation" in CONVERTER_REGISTRY
    assert "surface_strength" in CONVERTER_REGISTRY
    assert "text" in CONVERTER_REGISTRY

    val, unit = CONVERTER_REGISTRY["bearing"]("014.43°")
    assert val == 14.43
    assert unit == "DEG"

    (length, width), unit = CONVERTER_REGISTRY["dimensions"]("2 987 x 50")
    assert length == 2987.0
    assert width == 50.0
    assert unit == "M"

    (lon, lat), unit = CONVERTER_REGISTRY["coordinates"]("505314.39N 0042929.68E")
    assert round(lat, 2) == 50.89
    assert round(lon, 2) == 4.49

def test_reciprocal_designator():
    parser = RunwayParser("test", "test")
    assert parser.get_reciprocal_designator("01") == "19"
    assert parser.get_reciprocal_designator("19") == "01"
    assert parser.get_reciprocal_designator("07R") == "25L"
    assert parser.get_reciprocal_designator("25L") == "07R"
    assert parser.get_reciprocal_designator("07L") == "25R"
    assert parser.get_reciprocal_designator("25R") == "07L"
    assert parser.get_reciprocal_designator("18C") == "36C"

def test_physical_pair_key():
    parser = RunwayParser("test", "test")
    assert parser.get_physical_pair_key("01") == "01-19"
    assert parser.get_physical_pair_key("19") == "01-19"
    assert parser.get_physical_pair_key("07R") == "07R-25L"
    assert parser.get_physical_pair_key("25L") == "07R-25L"
    assert parser.get_physical_pair_key("07L") == "07L-25R"
    assert parser.get_physical_pair_key("25R") == "07L-25R"

def test_runway_parser_and_deterministic_hash():
    cache_dir = _cache_dir()
    raw_path = cache_dir / "2026-10-01-current" / "EB-AD-2.EBBR-en-GB.html"
    if not raw_path.exists():
        pytest.skip("Raw EBBR document not present")

    html = raw_path.read_text(encoding="utf-8")
    parser = RunwayParser("2026-10-01-current", "2026-10-01")

    # Parse 1st time
    feats1, issues1 = parser.parse_airport_runways(
        "EBBR", html, "EB-AD-2.EBBR-en-GB.html", "https://ops.skeyes.be", "2026-10-19T08:00:00Z"
    )

    # Parse 2nd time with DIFFERENT timestamp & url
    feats2, issues2 = parser.parse_airport_runways(
        "EBBR", html, "EB-AD-2.EBBR-en-GB.html", "https://different-url.be", "2026-10-25T23:59:59Z"
    )

    assert len(issues1) == 0
    assert len(feats1) == 9  # 6 directions + 3 physical pairs

    # Verify publisher_name from YAML is used (no hardcoded "Belgium AIP")
    crawler_cfg = load_crawler_config()
    publisher_name = crawler_cfg.get("publisher", {}).get("name") or crawler_cfg.get("publisher_name")
    for f in feats1:
        for fld in f.fields.values():
            assert fld.source == publisher_name

    # Assert 100% hash determinism
    hashes1 = [f.content_hash for f in feats1]
    hashes2 = [f.content_hash for f in feats2]
    assert hashes1 == hashes2

def test_validation_engine_config_driven():
    engine = ValidationEngine()

    cache_dir = _cache_dir()
    raw_path = cache_dir / "2026-10-01-current" / "EB-AD-2.EBBR-en-GB.html"
    if not raw_path.exists():
        pytest.skip("Raw EBBR document not present")

    html = raw_path.read_text(encoding="utf-8")
    parser = RunwayParser("2026-10-01-current", "2026-10-01")
    feats, _ = parser.parse_airport_runways(
        "EBBR", html, "EB-AD-2.EBBR-en-GB.html", "https://ops.skeyes.be", "2026-10-19T08:00:00Z"
    )

    # Clean data satisfies promotion gate
    issues = engine.validate_features(feats, "2026-10-01-current")
    passed, msg = engine.evaluate_promotion_gate(issues)
    assert passed is True

    # Test IssueSeverity & Promotion Gate failure when hard error is injected
    bad_feat = feats[0].model_copy(deep=True)
    bad_feat.fields["tora"].value = 4000.0
    bad_feat.fields["asda"].value = 4000.0
    bad_feat.fields["toda"].value = 3000.0
    bad_issues = engine.validate_feature(bad_feat, "2026-10-01-current")
    assert len(bad_issues) == 1
    assert bad_issues[0].severity == IssueSeverity.ERROR
    assert bad_issues[0].rule_id == "RULE_DECLARED_DIST_TORA_LE_TODA"

    passed, msg = engine.evaluate_promotion_gate(bad_issues)
    assert passed is False
    assert "Hard error count (1) exceeds allowed maximum" in msg

    # Test rule selection flag: if require_tora_le_toda is toggled False, no issue emitted!
    custom_cfg = dict(engine.cfg)
    custom_cfg["declared_distances"] = dict(custom_cfg["declared_distances"])
    custom_cfg["declared_distances"]["require_tora_le_toda"] = False
    custom_engine = ValidationEngine(validation_cfg=custom_cfg)
    bypassed_issues = custom_engine.validate_feature(bad_feat, "2026-10-01-current")
    assert len(bypassed_issues) == 0  # Bypassed via config!

    # Test dynamic severity from YAML: change bounding box severity from "warning" to "error"
    custom_cfg["operational_bounding_box"] = dict(custom_cfg["operational_bounding_box"])
    custom_cfg["operational_bounding_box"]["severity"] = "error"
    strict_bbox_engine = ValidationEngine(validation_cfg=custom_cfg)
    out_of_bounds_feat = feats[0].model_copy(deep=True)
    out_of_bounds_feat.geometry.coordinates = [10.0, 50.0]  # Outside FIR
    strict_issues = strict_bbox_engine.validate_feature(out_of_bounds_feat, "2026-10-01-current")
    bbox_issue = next(i for i in strict_issues if i.rule_id == "RULE_OPERATIONAL_BBOX")
    assert bbox_issue.severity == IssueSeverity.ERROR

def test_task_g_validation_rules():
    """Asserts Task G rules: natural_key_formats, RULE_DUPLICATE_NATURAL_KEY, RULE_LIMIT_UNIT_MISSING."""
    engine = ValidationEngine()

    # 1. Test duplicate natural key detection
    f1 = Feature(
        feature_type=FeatureType.WAYPOINT,
        natural_key="WPT/TEST1",
        publication_id="cycle",
        effective_from="2026-10-01",
        content_hash="h1"
    )
    f2 = Feature(
        feature_type=FeatureType.WAYPOINT,
        natural_key="WPT/TEST1",  # Duplicate key!
        publication_id="cycle",
        effective_from="2026-10-01",
        content_hash="h2"
    )
    dup_issues = engine.validate_features([f1, f2], "cycle")
    dup_err = next((i for i in dup_issues if i.rule_id == "RULE_DUPLICATE_NATURAL_KEY"), None)
    assert dup_err is not None
    assert dup_err.severity == IssueSeverity.ERROR

    # 2. Test natural key format from config
    bad_key_feat = Feature(
        feature_type=FeatureType.WAYPOINT,
        natural_key="INVALID_PREFIX/TEST",
        publication_id="cycle",
        effective_from="2026-10-01",
        content_hash="h3"
    )
    fmt_issues = engine.validate_feature(bad_key_feat, "cycle")
    fmt_err = next((i for i in fmt_issues if i.rule_id == "RULE_NATURAL_KEY_FORMAT"), None)
    assert fmt_err is not None
    assert fmt_err.severity == IssueSeverity.ERROR

    # 3. Test RULE_LIMIT_UNIT_MISSING on route with bare limit
    bare_lim_feat = Feature(
        feature_type=FeatureType.ROUTE,
        natural_key="RTE/L999",
        publication_id="cycle",
        effective_from="2026-10-01",
        content_hash="h4",
        fields={
            "upperLimit": FeatureField(
                value={"value": 195.0, "unit": None, "reference": None},
                unit=None, source="test", section="ENR 3.2",
                effective_date="2026-10-01", retrieved_at="now",
                source_url="url", parser_version="1.0", raw_text="195"
            )
        }
    )
    lim_issues = engine.validate_feature(bare_lim_feat, "cycle")
    lim_warn = next((i for i in lim_issues if i.rule_id == "RULE_LIMIT_UNIT_MISSING"), None)
    assert lim_warn is not None
    assert lim_warn.severity == IssueSeverity.WARNING
