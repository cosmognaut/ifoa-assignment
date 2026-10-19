"""
Tests for the dataset export helpers.

These exercise the pure serialisation functions only: no MongoDB, no loader and
no config are touched, so the file passes on a machine with no database at all.
The DB-reading shell (MongoLoader + cmd_export) is covered by the CLI itself.
"""
import json
from typing import Any, Dict, List

import pytest

from pipeline.cli import (
    ExportError,
    build_feature_collection,
    build_issue_list,
    build_publication_list,
    require_exportable,
    serialise_json,
    strip_mongo_id,
    write_export,
)


def _feature_doc(natural_key: str, feature_type: str, geometry: Any) -> Dict[str, Any]:
    """One stored feature document, `_id` included to prove it gets projected away."""
    return {
        "_id": {"$oid": "66f1c0ffee0000000000abcd"},
        "naturalKey": natural_key,
        "parentKey": None,
        "featureType": feature_type,
        "publicationId": "2026-10-01-current",
        "effectiveFrom": "2026-10-01",
        "effectiveTo": None,
        "sourceClass": "AIP",
        "temporality": "permanent",
        "geometry": geometry,
        "contentHash": "0" * 64,
        "fields": {},
    }


def _publication_doc(cycle_id: str, effective_date: str) -> Dict[str, Any]:
    return {
        "_id": {"$oid": "66f1c0ffee0000000000dcba"},
        "cycleId": cycle_id,
        "effectiveDate": effective_date,
        "amendmentId": "AMDT 010/2026",
        "status": "promoted",
    }


def _issue_doc(rule_id: str, feature_key: Any) -> Dict[str, Any]:
    return {
        "_id": {"$oid": "66f1c0ffee0000000000beef"},
        "publicationId": "2026-10-01-current",
        "ruleId": rule_id,
        "featureKey": feature_key,
        "severity": "warning",
        "section": "ENR 3.2",
        "message": "synthetic",
        "rawSnippet": "",
    }


def test_null_geometry_survives_as_null():
    """An airspace with geometry null must stay a Feature with "geometry": null."""
    doc = _feature_doc("ASP/EBR04", "specialUseArea", None)

    collection = build_feature_collection([doc])

    assert collection["type"] == "FeatureCollection"
    assert len(collection["features"]) == 1
    feature = collection["features"][0]
    assert feature["type"] == "Feature"
    assert feature["geometry"] is None
    assert "geometry" in feature
    assert feature["properties"]["naturalKey"] == "ASP/EBR04"


def test_geometry_is_passed_through_untouched():
    geometry = {"type": "Point", "coordinates": [5.317778, 50.770833]}

    collection = build_feature_collection([_feature_doc("WPT/GIVOR", "waypoint", geometry)])

    assert collection["features"][0]["geometry"] == geometry


def test_id_is_stripped_from_every_output():
    features = build_feature_collection([_feature_doc("EBBR", "aerodrome", None)])
    publications = build_publication_list([_publication_doc("2026-10-01-current", "2026-10-01")])
    issues = build_issue_list([_issue_doc("RULE_OPERATIONAL_BBOX", "WPT/GIVOR")])

    assert "_id" not in json.dumps(features)
    assert "_id" not in json.dumps(publications)
    assert "_id" not in json.dumps(issues)
    assert strip_mongo_id({"_id": 1, "keep": 2}) == {"keep": 2}


def test_properties_carry_the_full_camel_case_envelope():
    collection = build_feature_collection([_feature_doc("EBBR", "aerodrome", None)])

    assert sorted(collection["features"][0]["properties"]) == [
        "contentHash",
        "effectiveFrom",
        "effectiveTo",
        "featureType",
        "fields",
        "naturalKey",
        "parentKey",
        "publicationId",
        "sourceClass",
        "temporality",
    ]


def test_features_are_sorted_by_natural_key():
    shuffled: List[Dict[str, Any]] = [
        _feature_doc("WPT/GIVOR", "waypoint", None),
        _feature_doc("ASP/AIRWAYS", "airspace", None),
        _feature_doc("EBBR/RWY/25L", "runwayDirection", None),
        _feature_doc("EBBR", "aerodrome", None),
    ]

    keys = [
        feature["properties"]["naturalKey"]
        for feature in build_feature_collection(shuffled)["features"]
    ]

    assert keys == ["ASP/AIRWAYS", "EBBR", "EBBR/RWY/25L", "WPT/GIVOR"]


def test_issues_are_sorted_by_rule_id_then_feature_key():
    shuffled = [
        _issue_doc("RULE_UNRESOLVED_WAYPOINT", "RTE/Z310"),
        _issue_doc("RULE_OPERATIONAL_BBOX", "WPT/GIVOR"),
        _issue_doc("RULE_UNRESOLVED_WAYPOINT", None),
        _issue_doc("RULE_UNRESOLVED_WAYPOINT", "RTE/L179"),
    ]

    ordered = [(doc["ruleId"], doc["featureKey"]) for doc in build_issue_list(shuffled)]

    assert ordered == [
        ("RULE_OPERATIONAL_BBOX", "WPT/GIVOR"),
        ("RULE_UNRESOLVED_WAYPOINT", None),
        ("RULE_UNRESOLVED_WAYPOINT", "RTE/L179"),
        ("RULE_UNRESOLVED_WAYPOINT", "RTE/Z310"),
    ]


def test_publications_are_sorted_by_effective_date():
    shuffled = [
        _publication_doc("2026-11-01-current", "2026-11-01"),
        _publication_doc("2026-10-01-current", "2026-10-01"),
    ]

    ordered = [doc["cycleId"] for doc in build_publication_list(shuffled)]

    assert ordered == ["2026-10-01-current", "2026-11-01-current"]


def test_serialisation_is_byte_identical_across_runs():
    features = [_feature_doc("WPT/GIVOR", "waypoint", None), _feature_doc("EBBR", "aerodrome", None)]

    first = serialise_json(build_feature_collection(features))
    second = serialise_json(build_feature_collection(list(reversed(features))))

    assert first == second
    assert first.endswith("\n")
    assert json.loads(first)["type"] == "FeatureCollection"


def test_write_export_is_idempotent_on_disk(tmp_path):
    features = [_feature_doc("EBBR", "aerodrome", None)]
    publications = [_publication_doc("2026-10-01-current", "2026-10-01")]
    issues = [_issue_doc("RULE_OPERATIONAL_BBOX", "WPT/GIVOR")]

    first = write_export(tmp_path, "2026-10-01-current", features, publications, issues)
    first_bytes = {name: path.read_bytes() for name, path in first.items()}
    second = write_export(tmp_path, "2026-10-01-current", features, publications, issues)

    assert sorted(first) == ["features.geojson", "publications.json", "validation_issues.json"]
    assert all(path.stat().st_size > 0 for path in second.values())
    for name, path in second.items():
        assert path.read_bytes() == first_bytes[name]


def test_unknown_cycle_is_rejected():
    with pytest.raises(ExportError) as excinfo:
        require_exportable("2099-01-01", [], [])

    assert "Unknown cycle '2099-01-01'" in str(excinfo.value)


def test_cycle_without_features_is_rejected():
    publications = [_publication_doc("2026-10-01-current", "2026-10-01")]

    with pytest.raises(ExportError) as excinfo:
        require_exportable("2026-10-01-current", publications, [])

    assert "no features" in str(excinfo.value)
