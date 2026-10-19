"""
Guards for the "config is the only source of truth" invariant.

These tests exist because config/sections.yaml was previously loaded and then
ignored, while discovery.py carried its own parallel copy of the same facts.
They fail if that pattern comes back.
"""

import json
from pathlib import Path
from urllib.parse import urljoin

import pytest

from pipeline.config import ConfigError
from pipeline.ingest.discovery import AIPDiscovery

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "data" / "raw" / "2026-10-01-current" / "manifest.jsonl"

# Facts that moved out of Python and into config/crawler.yaml + config/sections.yaml.
# If any of these reappear in the ingestion modules, someone has re-hardcoded them.
FORBIDDEN_IN_INGEST = [
    "eAIP_Previous",
    "eAIP_Main",
    "eAIP_Next",
    "EB-cover-en-GB.html",
    "EB-menu-en-GB.html",
    "EB-AD-2.",
    "EB-ENR-",
    "EB-GEN-",
    "Effective date:",
    "Publication date:",
    "AD 2.12",
    "AD 2.13",
]


@pytest.fixture(scope="module")
def discovery():
    return AIPDiscovery()


def _committed_manifest():
    if not MANIFEST.exists():
        pytest.skip(f"committed manifest fixture not present: {MANIFEST}")
    with open(MANIFEST, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_resolved_documents_match_committed_manifest(discovery):
    """Config alone must reproduce the exact document set the old literals produced."""
    docs = discovery.resolve_documents()
    from_config = {(d["document_id"], d["document_filename"]) for d in docs}
    from_manifest = {
        (item["section_id"], item["url"].rsplit("/", 1)[-1])
        for item in _committed_manifest()
    }
    assert from_config == from_manifest


def test_document_urls_match_committed_manifest(discovery):
    """base_eaip_url is derived from the configured cover path; it must land where the files live."""
    cover_template = discovery.publisher["cycle_cover_path_template"]
    folder = discovery.publisher["cycle_folders"]["current"]
    cover_url = urljoin(
        discovery.base_url,
        cover_template.format(
            folder=folder,
            html_subdir=discovery.publisher["html_subdir"],
            cover_document=discovery.publisher["cover_document"],
        ),
    )
    base_eaip = urljoin(cover_url, f"{discovery.publisher['eaip_subdir']}/")

    built = {urljoin(base_eaip, d["document_filename"]) for d in discovery.resolve_documents()}
    committed = {item["url"] for item in _committed_manifest()}
    assert built == committed


def test_every_declared_section_resolves_to_a_document(discovery):
    resolved_ids = {
        section_id
        for doc in discovery.resolve_documents()
        for section_id in doc["section_ids"]
    }
    declared_ids = {section["id"] for section in discovery.section_defs}
    assert resolved_ids == declared_ids


def test_per_airport_sections_expand_once_per_configured_icao(discovery):
    per_airport_ids = {
        s["id"] for s in discovery.section_defs if s["scope"] == "per_airport"
    }
    docs = discovery.resolve_documents()
    for icao in discovery.target_icaos:
        covered = {
            section_id
            for doc in docs
            if doc["icao"] == icao
            for section_id in doc["section_ids"]
        }
        assert per_airport_ids <= covered


def test_sections_sharing_a_document_id_are_downloaded_once(discovery):
    docs = discovery.resolve_documents()
    document_ids = [doc["document_id"] for doc in docs]
    assert len(document_ids) == len(set(document_ids)), "duplicate document in fetch list"

    filenames = [doc["document_filename"] for doc in docs]
    assert len(filenames) == len(set(filenames)), "same file queued for download twice"


def test_no_hardcoded_publisher_facts_in_ingest_layer():
    offenders = {}
    for module in (REPO_ROOT / "pipeline" / "ingest").glob("*.py"):
        source = module.read_text(encoding="utf-8")
        hits = [literal for literal in FORBIDDEN_IN_INGEST if literal in source]
        if hits:
            offenders[module.name] = hits
    assert offenders == {}, f"publisher facts leaked back into code: {offenders}"


def test_missing_publisher_config_fails_loudly(tmp_path):
    """A missing key must raise, never silently fall back to a literal."""
    import yaml

    broken = tmp_path / "crawler.yaml"
    broken.write_text(
        yaml.safe_dump({"crawler": {"cache_dir": "data/raw"}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="publisher"):
        AIPDiscovery(crawler_cfg_path=str(broken))


def test_invalid_scope_is_rejected(tmp_path):
    import yaml

    sections = tmp_path / "sections.yaml"
    sections.write_text(
        yaml.safe_dump({
            "sections": [{
                "id": "X-1",
                "name": "X",
                "category": "GEN",
                "scope": "sometimes",
                "file_pattern": "X.html",
            }]
        }),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="scope"):
        AIPDiscovery(sections_cfg_path=str(sections))
