"""
Guards for the fetch and verify failure paths.

These exist because a blocked live crawl (the publisher's robots.txt disallows `*`)
used to overwrite a good manifest with twelve `blocked_by_robots` rows, and because
`verify_manifest` raised IsADirectoryError on an empty `local_path` instead of
reporting the file as missing. Neither test needs a network or a database.
"""

import hashlib

from pipeline.cli import summarise_fetch_results
from pipeline.ingest.manifest import ManifestManager

CYCLE = "2026-10-01-current"


def _item(local_path: str, sha256: str = "0" * 64, status: str = "fetched", section_id: str = "ENR-4.1") -> dict:
    """One crawler result, shaped exactly like the dicts `fetch_url` returns."""
    return {
        "url": "https://example.invalid/EB-ENR-4.1-en-GB.html",
        "local_path": local_path,
        "sha256": sha256,
        "etag": None,
        "fetched_at": "2026-10-19T00:00:00+00:00",
        "status": status,
        "bytes_length": 0,
        "section_id": section_id,
        "document_type": "html",
    }


def test_verify_manifest_reports_empty_local_path_instead_of_raising(tmp_path):
    """A blocked entry has no local_path; verify must report it, not open '.'."""
    ManifestManager.write_manifest(
        str(tmp_path), CYCLE, [_item("", status="blocked_by_robots")]
    )

    ok, errors = ManifestManager.verify_manifest(str(tmp_path), CYCLE)

    assert ok is False
    assert errors, "an empty local_path must produce an error entry"
    assert "Missing file" in errors[0]


def test_verify_manifest_reports_directory_local_path(tmp_path):
    """A local_path that resolves to a directory counts as missing, and must not raise."""
    ManifestManager.write_manifest(str(tmp_path), CYCLE, [_item(str(tmp_path))])

    ok, errors = ManifestManager.verify_manifest(str(tmp_path), CYCLE)

    assert ok is False
    assert "Missing file" in errors[0]


def test_verify_manifest_still_accepts_a_matching_file(tmp_path):
    """The happy path is unchanged: a real file whose digest matches verifies clean."""
    document = tmp_path / "EB-ENR-4.1-en-GB.html"
    payload = b"<html>real bytes</html>"
    document.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    ManifestManager.write_manifest(str(tmp_path), CYCLE, [_item(str(document), sha256=digest)])

    ok, errors = ManifestManager.verify_manifest(str(tmp_path), CYCLE)

    assert ok is True
    assert errors == []


def test_summarise_fetch_results_all_blocked_is_not_usable():
    blocked = [_item("", status="blocked_by_robots", section_id=f"S-{n}") for n in range(12)]

    usable, failed = summarise_fetch_results(blocked)

    assert usable == []
    assert len(failed) == 12


def test_summarise_fetch_results_mixed_counts_both_sides():
    items = [
        _item("/cache/a.html", status="fetched", section_id="A"),
        _item("/cache/b.html", status="not_modified", section_id="B"),
        _item("/cache/c.html", status="cached", section_id="C"),
        _item("", status="blocked_by_robots", section_id="D"),
        _item("", status="failed_http_503", section_id="E"),
    ]

    usable, failed = summarise_fetch_results(items)

    assert [item["section_id"] for item in usable] == ["A", "B", "C"]
    assert [item["section_id"] for item in failed] == ["D", "E"]


def test_summarise_fetch_results_all_fetched_has_no_failures():
    items = [
        _item("/cache/a.html", status="fetched", section_id="A"),
        _item("/cache/b.html", status="fetched_from_dir", section_id="B"),
        _item("/cache/c.html", status="fetched_from_zip", section_id="C"),
    ]

    usable, failed = summarise_fetch_results(items)

    assert len(usable) == 3
    assert failed == []
