import json
import pytest
import tempfile
from pathlib import Path
from pipeline.models.manifest import ManifestItem
from pipeline.ingest.manifest import ManifestManager
from pipeline.ingest.discovery import parse_aip_date

def test_parse_aip_date():
    assert parse_aip_date("01 OCT 2026") == "2026-10-01"
    assert parse_aip_date("Effective date: 29 OCT 2026") == "2026-10-29"
    assert parse_aip_date("Publication date: 17 SEP 2026") == "2026-09-17"
    assert parse_aip_date("Invalid date string") is None

def test_manifest_write_and_verify(tmp_path):
    cache_dir = tmp_path / "raw"
    cycle_id = "2026-10-01"
    cycle_dir = cache_dir / cycle_id
    cycle_dir.mkdir(parents=True)

    # Create dummy raw file
    dummy_file = cycle_dir / "test-doc.html"
    content = b"<html><body>AIP Test Data</body></html>"
    dummy_file.write_bytes(content)

    import hashlib
    file_sha = hashlib.sha256(content).hexdigest()

    items = [{
        "url": "https://example.com/test-doc.html",
        "local_path": str(dummy_file),
        "sha256": file_sha,
        "etag": '"12345"',
        "fetched_at": "2026-10-19T00:00:00Z",
        "status": "fetched",
        "bytes_length": len(content),
        "section_id": "TEST-1.1",
        "document_type": "html"
    }]

    manifest_sha = ManifestManager.write_manifest(str(cache_dir), cycle_id, items)
    assert len(manifest_sha) == 64

    # Verify manifest loads correctly
    loaded = ManifestManager.load_manifest(str(cache_dir), cycle_id)
    assert len(loaded) == 1
    assert loaded[0].sha256 == file_sha

    # Verify integrity passes
    ok, errors = ManifestManager.verify_manifest(str(cache_dir), cycle_id)
    assert ok is True
    assert len(errors) == 0

    # Tamper with file
    dummy_file.write_bytes(b"Tampered content")
    ok, errors = ManifestManager.verify_manifest(str(cache_dir), cycle_id)
    assert ok is False
    assert len(errors) == 1
    assert "SHA-256 mismatch" in errors[0]
