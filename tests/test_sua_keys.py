"""
Special-use airspace natural keys and the validator that has to catch them.

The ENR 5.x sections contain tables that are not airspaces (single-letter legend keys,
numbered procedure sections) and designations printed with typographic dashes, mixed
case and an interior slash. This suite pins the three things that must stay true:
non-data tables never become features, keys are normalised and unique, and the
`designation` field keeps the verbatim published text.
"""
import re
from pathlib import Path

import pytest

from pipeline.cli import _parse_special_use_areas
from pipeline.config import (
    load_crawler_config,
    load_sections_config,
    load_validation_config,
    require,
    require_str,
)
from pipeline.parse.airspace import AirspaceParser

CYCLE = "2026-10-01-current"
EFFECTIVE = "2026-10-01"
CACHE_DIR = Path(require_str(load_crawler_config(), "cache_dir", "crawler.yaml"))


def _key_patterns():
    formats = require(load_validation_config(), "natural_key_formats", "validation.yaml")
    return (
        re.compile(require_str(formats, "specialUseArea", "validation.yaml")),
        re.compile(require_str(formats, "airspace", "validation.yaml")),
    )


@pytest.fixture(scope="module")
def sua_features():
    features, _ = _parse_special_use_areas(CYCLE, CACHE_DIR)
    if not features:
        pytest.skip("cached ENR 5 documents not present")
    return features


def test_legend_and_numbered_sections_are_not_features(sua_features):
    keys = [f.natural_key for f in sua_features]

    assert not any("1.3.7" in key for key in keys), "a numbered procedure section became an airspace"
    # The legend table published single-letter values ('a', 'b', 'c'); no remaining
    # feature may consist entirely of one-character field values.
    for feature in sua_features:
        populated = [str(v.value) for v in feature.fields.values() if v.value not in (None, "")]
        assert not (populated and all(len(v) < 2 for v in populated)), feature.natural_key


def test_keys_are_normalised_unique_and_valid(sua_features):
    pattern, _ = _key_patterns()
    keys = [f.natural_key for f in sua_features]

    assert len(keys) == len(set(keys)), "duplicate natural keys in the special-use family"
    for key in keys:
        assert "_" not in key, key
        assert key.count("/") == 1, key
        assert pattern.match(key), key


def test_designation_keeps_the_verbatim_published_text(sua_features):
    by_key = {f.natural_key: f for f in sua_features}

    # En dash in the AIP, hyphen normalised out of the key only.
    diekirch = by_key["SUA/ELTSA6"]
    assert diekirch.fields["designation"].value == "ELTSA6 \u2013 DIEKIRCH NORTH"
    assert diekirch.fields["designation"].raw_text.strip() == "ELTSA6 \u2013 DIEKIRCH NORTH"

    # Interior slash: one key segment, original text preserved.
    ursel = by_key["SUA/TRA-TSA11"]
    assert ursel.fields["designation"].value == "TRA/TSA11 - URSEL"
    assert key_pattern_excludes_extra_segment(ursel.natural_key)


def key_pattern_excludes_extra_segment(key: str) -> bool:
    return key.startswith("SUA/TRA-TSA11") and key.count("/") == 1


def test_every_key_in_both_families_passes_the_strict_patterns():
    sua_pattern, asp_pattern = _key_patterns()
    parser = AirspaceParser(CYCLE, EFFECTIVE)

    enr21 = CACHE_DIR / CYCLE / "EB-ENR-2.1-en-GB.html"
    if not enr21.exists():
        pytest.skip("cached ENR 2.1 document not present")
    asp_features, _ = parser.parse_airspace_enr21(
        enr21.read_text(encoding="utf-8"), enr21.name, "file://enr21", "2026-10-19T00:00:00+00:00"
    )
    sua_features, _ = _parse_special_use_areas(CYCLE, CACHE_DIR)

    assert asp_features and sua_features
    for feature in asp_features:
        assert asp_pattern.match(feature.natural_key), feature.natural_key
    for feature in sua_features:
        assert sua_pattern.match(feature.natural_key), feature.natural_key


def test_the_strict_patterns_reject_the_known_bad_forms():
    sua_pattern, asp_pattern = _key_patterns()

    for bad in (
        "SUA/1.3.7.1_Airspace_Allocation_Procedures",
        "SUA/ELTSA6_\u2013_DIEKIRCH_NORTH",
        "SUA/TRA/TSA11",
        "SUA/tra-tsa11",
    ):
        assert not sua_pattern.match(bad), bad

    for good in ("SUA/EBR01", "SUA/ELTSA6", "SUA/TRA-TSA11", "SUA/TRA NA"):
        assert sua_pattern.match(good), good

    assert not asp_pattern.match("ASP/LIÈGE TMA ONE")
    assert asp_pattern.match("ASP/LIEGE TMA ONE")


def test_a_section_of_only_non_data_tables_reports_one_empty_section_warning(tmp_path):
    parser = AirspaceParser(CYCLE, EFFECTIVE)
    legend = """
    <div id="ENR-5.2">
      <table><caption>1.3.7.1 Airspace Allocation Procedures</caption>
        <tr><td>a</td><td>b</td><td>c</td><td>d</td></tr>
      </table>
      <table><caption>LEGEND</caption>
        <tr><td>a</td><td>b</td><td>c</td><td>d</td></tr>
      </table>
    </div>
    """
    features, issues = parser.parse_special_use_document(
        legend, "ENR-5.2", "synthetic.html", "file://synthetic", "2026-10-19T00:00:00+00:00"
    )

    assert features == []
    assert [i.rule_id for i in issues] == ["RULE_EMPTY_SECTION"]
    assert issues[0].severity.value == "warning"


def test_legend_rows_are_rejected_by_the_value_length_rule(tmp_path):
    """The configured rule on its own — no numbered-section prefix to catch it first."""
    parser = AirspaceParser(CYCLE, EFFECTIVE)
    legend = """
    <div id="ENR-5.2">
      <table><caption>LEGEND OF TERMS</caption>
        <tr><td>a</td><td>b</td><td>c</td><td>d</td></tr>
      </table>
      <table><caption>EBR01 - BRUSSELS CITY</caption>
        <tr><td>505311N 0042013E - 505316N 0042247E</td><td>GND / 4500 FT AMSL</td><td>restricted</td><td>H24</td></tr>
      </table>
    </div>
    """
    features, issues = parser.parse_special_use_document(
        legend, "ENR-5.2", "synthetic.html", "file://synthetic", "2026-10-19T00:00:00+00:00"
    )

    assert [f.natural_key for f in features] == ["SUA/EBR01"]
    assert issues == []


def test_the_rules_come_from_config_not_from_python():
    sections_cfg = load_sections_config()
    normalisation = require(sections_cfg, "natural_key_normalisation", "sections.yaml")
    row_rules = require(sections_cfg, "non_data_row_rules", "sections.yaml")

    assert "\u2013" in normalisation["replacements"]
    assert normalisation["replacements"]["/"] == "-"
    assert normalisation["code_separator"] == " - "
    assert row_rules["min_value_length"] >= 2
    assert re.match(require_str(row_rules, "designation_pattern", "sections.yaml"), "1.3.7.1 Something")
