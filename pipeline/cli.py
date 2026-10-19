"""
Command-line interface for the deterministic aeronautical eAIP data pipeline.
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional, Set

from pipeline.config import (
    load_crawler_config,
    load_airports_config,
    load_database_config,
    load_sections_config,
    load_validation_config,
    require_str
)
from pipeline.ingest.crawler import PoliteCrawler
from pipeline.ingest.discovery import AIPDiscovery
from pipeline.ingest.manifest import ManifestManager
from pipeline.models.publication import Publication, PublicationStatus, SourceKind, PublicationStatistics
from pipeline.models.validation_issue import ValidationIssue, IssueSeverity
from pipeline.models.feature import Feature
from pipeline.parse.runways import RunwayParser
from pipeline.parse.waypoints import WaypointParser
from pipeline.parse.airports import AirportParser
from pipeline.parse.navaids import NavaidParser
from pipeline.parse.operational import OperationalParser
from pipeline.parse.routes import RouteParser
from pipeline.parse.airspace import AirspaceParser
from pipeline.validate.rules import ValidationEngine
from pipeline.diff.engine import DiffEngine, DiffReport
from pipeline.load.load import MongoLoader
from pymongo.errors import PyMongoError


class ExportError(RuntimeError):
    """A cycle cannot be exported: unknown cycle, or a cycle with no features."""


# Statuses the crawler emits when it produced a usable document on disk. Anything
# else (blocked_by_robots, failed_http_*, error: ..., failed_file_not_*) is a failure.
USABLE_FETCH_STATUSES = frozenset(
    {"fetched", "fetched_from_zip", "fetched_from_dir", "not_modified", "cached"}
)


def summarise_fetch_results(
    items: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split crawler results into (usable, failed) by status.

    Pure: no I/O, no network, no config. This is the decision that stops a failed
    crawl from overwriting a committed manifest (see cmd_ingest_fetch).
    """
    usable: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for item in items:
        if item.get("status") in USABLE_FETCH_STATUSES:
            usable.append(item)
        else:
            failed.append(item)
    return usable, failed


def _cache_dir(args=None) -> Path:
    """The raw cache root, single-sourced from crawler.yaml with CLI override."""
    if args and getattr(args, "cache_dir", None):
        return Path(args.cache_dir)
    return Path(require_str(load_crawler_config(), "cache_dir", "crawler.yaml"))

def _target_icaos(args=None) -> List[str]:
    """Resolves target ICAOs (explicit CLI override, else target_airports from airports.yaml)."""
    if args and getattr(args, "icao", None):
        return [args.icao]
    airports_cfg = load_airports_config()
    return [a["icao"] for a in airports_cfg.get("target_airports", [])]

def _meta_path(cache_dir: Path, cycle_id: str) -> Path:
    return cache_dir / cycle_id / "publication_meta.json"

def _save_publication_meta(cache_dir: Path, cycle_id: str, meta: Dict[str, Any]):
    p = _meta_path(cache_dir, cycle_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

def _load_publication_meta(cache_dir: Path, cycle_id: str) -> Optional[Dict[str, Any]]:
    p = _meta_path(cache_dir, cycle_id)
    if not p.is_file():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def cmd_check_updates(args):
    """Compares discovered cycles against local/promoted cycles and prints what needs ingesting."""
    print("[Pipeline] Checking for AIP updates from skeyes...")
    discovery = AIPDiscovery()
    discovered_cycles = discovery.discover_cycles()
    if not discovered_cycles:
        print("[Pipeline] No cycles discovered from publisher.")
        return

    cache_dir = _cache_dir(args)
    local_cycles = set()
    if cache_dir.exists():
        for d in cache_dir.iterdir():
            if d.is_dir() and (d / "manifest.jsonl").exists():
                local_cycles.add(d.name)

    print(f"\nDiscovered {len(discovered_cycles)} cycle(s) from skeyes:")
    print(f"{'Role':<12} {'Cycle ID (Effective)':<22} {'Amendment':<22} {'Status':<15}")
    print("-" * 75)

    needs_ingestion = []
    for c in discovered_cycles:
        cid = c["cycle_id"]
        is_local = cid in local_cycles
        status = "CACHED" if is_local else "NEW (Needs Ingest)"
        print(f"{c['role']:<12} {cid:<22} {c['amendment_id']:<22} {status:<15}")
        if not is_local:
            needs_ingestion.append(c)

    if needs_ingestion:
        print(f"\nAction needed: {len(needs_ingestion)} cycle(s) require ingestion:")
        for c in needs_ingestion:
            print(f"  -> uv run python -m pipeline.cli ingest fetch --cycle {c['cycle_id']}")
    else:
        print("\nAll discovered cycles are up-to-date in local cache.")

def cmd_ingest_cycles(args):
    """Lists discovered cycles and their metadata."""
    discovery = AIPDiscovery()
    cycles = discovery.discover_cycles()
    print(json.dumps(cycles, indent=2))

def cmd_ingest_fetch(args):
    """Fetches documents for a cycle and writes manifest.jsonl and publication_meta.json.

    Writes nothing when the run produced no usable document: overwriting a committed
    manifest with a run of `blocked_by_robots` rows would destroy the SHA-256 chain of
    custody for a snapshot that is still good on disk (ADR 3).
    """
    cycle_id = args.cycle
    cache_dir = _cache_dir(args)
    from_zip = Path(args.from_zip) if getattr(args, "from_zip", None) else None
    from_dir = Path(args.from_dir) if getattr(args, "from_dir", None) else None

    print(f"[Pipeline] Ingesting cycle: {cycle_id}")
    discovery = AIPDiscovery()
    cycles = discovery.discover_cycles()
    cycle_match = next((c for c in cycles if c["cycle_id"] == cycle_id), None)
    if not cycle_match:
        # If offline fallback is used, synthesize minimal cycle metadata
        if from_zip or from_dir:
            cycle_match = {
                "cycle_id": cycle_id,
                "role": "offline",
                "effective_date": cycle_id[:10],
                "amendment_id": f"OFFLINE-{cycle_id}",
                "publication_date": cycle_id[:10],
                "source_kind": "ZIP" if from_zip else "DIR"
            }
        else:
            print(f"Error: Cycle '{cycle_id}' not discovered from publisher. Available cycles: {[c['cycle_id'] for c in cycles]}")
            sys.exit(1)

    print(f"Discovered cycle metadata: {cycle_match['amendment_id']} effective {cycle_match['effective_date']}")

    sections = discovery.discover_sections_for_cycle(cycle_match) if not (from_zip or from_dir) else []
    # If offline, generate document list from sections.yaml directly
    if from_zip or from_dir:
        sections_cfg = load_sections_config().get("sections", [])
        airports_cfg = load_airports_config()
        icaos = [a["icao"] for a in airports_cfg.get("target_airports", [])]
        seen_files = set()
        for s in sections_cfg:
            if s.get("scope") == "per_airport":
                for icao in icaos:
                    fname = s["file_template"].format(icao=icao)
                    if fname not in seen_files:
                        seen_files.add(fname)
                        sections.append({"document_filename": fname, "section_id": f"AD-2.{icao}", "url": f"file://{fname}", "category": "AD"})
            elif s.get("scope") == "global" and s.get("file_pattern"):
                fname = s["file_pattern"]
                if fname not in seen_files:
                    seen_files.add(fname)
                    sections.append({"document_filename": fname, "section_id": s["id"], "url": f"file://{fname}", "category": s.get("category", "ENR")})

    print(f"Targeting {len(sections)} document(s):")
    crawler = PoliteCrawler(cache_dir=cache_dir)
    manifest_items = []

    base_url = require_str(load_crawler_config(), "base_product_url", "crawler.yaml")
    from urllib.parse import urljoin

    for s in sections:
        fname = s["document_filename"]
        sec_id = s["section_id"]
        url = s.get("url") or urljoin(base_url, fname)

        if from_zip:
            print(f"  Extracting from ZIP: {fname}")
            res = crawler.fetch_from_zip(from_zip, cycle_id, sec_id, fname)
        elif from_dir:
            print(f"  Copying from DIR: {fname}")
            res = crawler.fetch_from_dir(from_dir, cycle_id, sec_id, fname)
        else:
            print(f"  Fetching: {url}")
            res = crawler.fetch_url(url, cycle_id, sec_id, fname)

        print(f"    -> Status: {res['status']} | Size: {res['bytes_length']} bytes | SHA256: {res['sha256'][:12]}...")
        manifest_items.append(res)

    usable, failed = summarise_fetch_results(manifest_items)

    if not manifest_items:
        print(
            f"Error: no documents were enumerated for cycle '{cycle_id}'. Nothing written.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not usable:
        if all(item.get("status") == "blocked_by_robots" for item in failed):
            reason = f"every document was refused by robots.txt (status blocked_by_robots, {len(failed)} documents)"
        else:
            reason = f"no document could be fetched ({len(failed)} failed)"
        print(f"\n[Pipeline] Fetch produced nothing usable: {reason}.", file=sys.stderr)
        for item in failed:
            print(f"[Pipeline]   {item.get('section_id')}: {item.get('status')}", file=sys.stderr)
        print(
            f"[Pipeline] Refusing to write {cache_dir}/{cycle_id}/manifest.jsonl — "
            "the existing manifest and cached documents are untouched.",
            file=sys.stderr,
        )
        sys.exit(1)

    if failed:
        failed_ids = ", ".join(str(item.get("section_id")) for item in failed)
        print(
            f"\n[Pipeline] WARNING: partial fetch — {len(usable)} usable, {len(failed)} failed ({failed_ids})",
            file=sys.stderr,
        )

    _save_publication_meta(cache_dir, cycle_id, cycle_match)
    manifest_sha = ManifestManager.write_manifest(str(cache_dir), cycle_id, manifest_items)
    print(f"\nManifest written to: {cache_dir}/{cycle_id}/manifest.jsonl")
    print(f"Manifest SHA-256: {manifest_sha}")

def cmd_ingest_status(args):
    """Displays status and counts of an ingested cycle manifest."""
    cycle_id = args.cycle
    cache_dir = _cache_dir(args)
    items = ManifestManager.load_manifest(str(cache_dir), cycle_id)
    if not items:
        print(f"No manifest found for cycle '{cycle_id}' at {cache_dir}/{cycle_id}/manifest.jsonl")
        return

    print(f"Cycle: {cycle_id}")
    print(f"Total documents in manifest: {len(items)}")
    total_bytes = sum(i.bytes_length for i in items)
    print(f"Total cached size: {total_bytes / (1024 * 1024):.2f} MB")
    status_counts = {}
    for i in items:
        status_counts[i.status] = status_counts.get(i.status, 0) + 1
    for st, count in status_counts.items():
        print(f"  Status '{st}': {count}")

def cmd_ingest_verify(args):
    """Verifies SHA-256 hashes of all cached files against the manifest."""
    cycle_id = args.cycle
    cache_dir = _cache_dir(args)
    ok, errors = ManifestManager.verify_manifest(str(cache_dir), cycle_id)
    if ok:
        print(f"Verification PASSED for cycle '{cycle_id}'. All files match their manifest SHA-256.")
    else:
        print(f"Verification FAILED for cycle '{cycle_id}':")
        for err in errors:
            print(f"  ERROR: {err}")
        sys.exit(1)

def cmd_ingest_list(args):
    """Lists cached files for a cycle."""
    cycle_id = args.cycle
    cache_dir = _cache_dir(args)
    items = ManifestManager.load_manifest(str(cache_dir), cycle_id)
    print(f"{'Section':<15} {'Status':<12} {'Size':<10} {'SHA256':<16} {'Filename'}")
    print("-" * 80)
    for i in items:
        p = Path(i.local_path).name if i.local_path else "NONE"
        sha_short = i.sha256[:14] + ".." if i.sha256 else "NONE"
        print(f"{i.section_id:<15} {i.status:<12} {i.bytes_length:<10} {sha_short:<16} {p}")

def _parse_airport_runways(cycle_id: str, icao: str, cache_dir: Path) -> Tuple[List[Feature], List[ValidationIssue]]:
    sections_cfg = load_sections_config()
    ad12_spec = next((s for s in sections_cfg.get("sections", []) if s["id"] == "AD-2.12"), None)
    if not ad12_spec:
        raise ValueError("Missing AD-2.12 spec in sections.yaml")

    expected_filename = ad12_spec["file_template"].format(icao=icao)
    doc_path = cache_dir / cycle_id / expected_filename
    if not doc_path.exists():
        raise FileNotFoundError(
            f"Missing cached document at {doc_path}. "
            f"Run 'pipeline ingest fetch --cycle {cycle_id}' first."
        )

    with open(doc_path, "r", encoding="utf-8") as f:
        html = f.read()

    manifest_items = ManifestManager.load_manifest(str(cache_dir), cycle_id)
    doc_meta = next((item for item in manifest_items if item.section_id == f"AD-2.{icao}"), None)
    if not doc_meta:
        raise ValueError(f"Document {expected_filename} missing from manifest.jsonl for cycle {cycle_id}")

    meta = _load_publication_meta(cache_dir, cycle_id) or {}
    effective_date = meta.get("effective_date", cycle_id[:10])

    parser = RunwayParser(cycle_id=cycle_id, effective_date=effective_date)
    return parser.parse_airport_runways(
        icao=icao,
        html_content=html,
        document_filename=doc_path.name,
        source_url=doc_meta.url,
        retrieved_at=doc_meta.fetched_at
    )

def _parse_enroute_navaids(cycle_id: str, cache_dir: Path) -> Tuple[List[Feature], List[ValidationIssue]]:
    sections_cfg = load_sections_config()
    nav_spec = next((s for s in sections_cfg.get("sections", []) if s["id"] == "ENR-4.1"), None)
    if not nav_spec:
        return [], []

    nav_filename = nav_spec.get("file_pattern", "EB-ENR-4.1-en-GB.html")
    doc_path = cache_dir / cycle_id / nav_filename
    if not doc_path.exists():
        return [], []

    manifest_items = ManifestManager.load_manifest(str(cache_dir), cycle_id)
    doc_meta = next((item for item in manifest_items if item.section_id == "ENR-4.1"), None)
    source_url = doc_meta.url if doc_meta else f"file://{doc_path}"
    retrieved_at = doc_meta.fetched_at if doc_meta else datetime.now(timezone.utc).isoformat()

    meta = _load_publication_meta(cache_dir, cycle_id) or {}
    effective_date = meta.get("effective_date", cycle_id[:10])

    parser = NavaidParser(cycle_id=cycle_id, effective_date=effective_date)
    return parser.parse_enroute_navaids(
        html_content=doc_path.read_text(encoding="utf-8"),
        document_filename=doc_path.name,
        source_url=source_url,
        retrieved_at=retrieved_at
    )

def _parse_waypoints(cycle_id: str, cache_dir: Path) -> Tuple[List[Feature], List[ValidationIssue]]:
    sections_cfg = load_sections_config()
    wpt_spec = next((s for s in sections_cfg.get("sections", []) if s["id"] == "ENR-4.4"), None)
    if not wpt_spec:
        return [], []

    wpt_filename = wpt_spec.get("file_pattern", "EB-ENR-4.4-en-GB.html")
    doc_path = cache_dir / cycle_id / wpt_filename
    if not doc_path.exists():
        return [], []

    manifest_items = ManifestManager.load_manifest(str(cache_dir), cycle_id)
    doc_meta = next((item for item in manifest_items if item.section_id == "ENR-4.4"), None)
    source_url = doc_meta.url if doc_meta else f"file://{doc_path}"
    retrieved_at = doc_meta.fetched_at if doc_meta else datetime.now(timezone.utc).isoformat()

    meta = _load_publication_meta(cache_dir, cycle_id) or {}
    effective_date = meta.get("effective_date", cycle_id[:10])

    parser = WaypointParser(cycle_id=cycle_id, effective_date=effective_date)
    return parser.parse_waypoints(
        html_content=doc_path.read_text(encoding="utf-8"),
        document_filename=doc_path.name,
        source_url=source_url,
        retrieved_at=retrieved_at
    )

def _parse_airport_summary(cycle_id: str, icao: str, cache_dir: Path) -> Tuple[Optional[Feature], List[ValidationIssue]]:
    sections_cfg = load_sections_config()
    ad22_spec = next((s for s in sections_cfg.get("sections", []) if s["id"] == "AD-2.2"), None)
    if not ad22_spec:
        return None, []
    template = require_str(ad22_spec, "file_template", "sections.yaml AD-2.2")
    doc_path = cache_dir / cycle_id / template.format(icao=icao)
    if not doc_path.exists():
        return None, []

    manifest_items = ManifestManager.load_manifest(str(cache_dir), cycle_id)
    doc_meta = next((item for item in manifest_items if item.section_id == f"AD-2.{icao}"), None)
    source_url = doc_meta.url if doc_meta else f"file://{doc_path}"
    retrieved_at = doc_meta.fetched_at if doc_meta else datetime.now(timezone.utc).isoformat()

    meta = _load_publication_meta(cache_dir, cycle_id) or {}
    effective_date = meta.get("effective_date", cycle_id[:10])

    parser = AirportParser(cycle_id=cycle_id, effective_date=effective_date)
    return parser.parse_airport_summary(
        icao=icao,
        html_content=doc_path.read_text(encoding="utf-8"),
        document_filename=doc_path.name,
        source_url=source_url,
        retrieved_at=retrieved_at
    )

def _parse_airport_navaids(cycle_id: str, icao: str, cache_dir: Path) -> Tuple[List[Feature], List[ValidationIssue]]:
    """AD 2.19 radio navigation and landing aids (ILS LOC/GP/DME, DVOR/DME, VOR, NDB) for one airport."""
    sections_cfg = load_sections_config()
    ad19_spec = next((s for s in sections_cfg.get("sections", []) if s["id"] == "AD-2.19"), None)
    if not ad19_spec:
        return [], []
    template = require_str(ad19_spec, "file_template", "sections.yaml AD-2.19")
    doc_path = cache_dir / cycle_id / template.format(icao=icao)
    if not doc_path.exists():
        return [], []

    manifest_items = ManifestManager.load_manifest(str(cache_dir), cycle_id)
    doc_meta = next((item for item in manifest_items if item.section_id == f"AD-2.{icao}"), None)
    source_url = doc_meta.url if doc_meta else f"file://{doc_path}"
    retrieved_at = doc_meta.fetched_at if doc_meta else datetime.now(timezone.utc).isoformat()

    meta = _load_publication_meta(cache_dir, cycle_id) or {}
    effective_date = meta.get("effective_date", cycle_id[:10])

    parser = NavaidParser(cycle_id=cycle_id, effective_date=effective_date)
    return parser.parse_airport_navaids(
        icao=icao,
        html_content=doc_path.read_text(encoding="utf-8"),
        document_filename=doc_path.name,
        source_url=source_url,
        retrieved_at=retrieved_at
    )

def _parse_airport_operational(cycle_id: str, icao: str, cache_dir: Path) -> Tuple[List[Feature], List[ValidationIssue]]:
    """AD 2.21 noise abatement and AD 2.20 local regulations for one airport.

    Both are prose: the feature carries the verbatim text, not a structure read into it.
    """
    sections_cfg = load_sections_config()
    specs = [
        (next((s for s in sections_cfg.get("sections", []) if s["id"] == "AD-2.21"), None), "noise"),
        (next((s for s in sections_cfg.get("sections", []) if s["id"] == "AD-2.20"), None), "local"),
    ]

    features: List[Feature] = []
    issues: List[ValidationIssue] = []
    meta = _load_publication_meta(cache_dir, cycle_id) or {}
    effective_date = meta.get("effective_date", cycle_id[:10])
    parser = OperationalParser(cycle_id=cycle_id, effective_date=effective_date)

    for spec, kind in specs:
        if not spec:
            continue
        template = require_str(spec, "file_template", f"sections.yaml {spec['id']}")
        doc_path = cache_dir / cycle_id / template.format(icao=icao)
        if not doc_path.exists():
            continue

        manifest_items = ManifestManager.load_manifest(str(cache_dir), cycle_id)
        doc_meta = next((item for item in manifest_items if item.section_id == f"AD-2.{icao}"), None)
        source_url = doc_meta.url if doc_meta else f"file://{doc_path}"
        retrieved_at = doc_meta.fetched_at if doc_meta else datetime.now(timezone.utc).isoformat()
        html = doc_path.read_text(encoding="utf-8")

        if kind == "noise":
            feat, feat_issues = parser.parse_noise(
                icao, html, doc_path.name, source_url, retrieved_at
            )
        else:
            feat, feat_issues = parser.parse_local_regulations(
                icao, html, doc_path.name, source_url, retrieved_at
            )
        if feat is not None:
            features.append(feat)
        issues.extend(feat_issues)

    return features, issues

def _parse_airspaces(cycle_id: str, cache_dir: Path) -> Tuple[List[Feature], List[ValidationIssue]]:
    sections_cfg = load_sections_config()
    asp_spec = next((s for s in sections_cfg.get("sections", []) if s["id"] == "ENR-2.1"), None)
    if not asp_spec:
        return [], []
    fname = asp_spec.get("file_pattern", "EB-ENR-2.1-en-GB.html")
    doc_path = cache_dir / cycle_id / fname
    if not doc_path.exists():
        return [], []
    manifest_items = ManifestManager.load_manifest(str(cache_dir), cycle_id)
    doc_meta = next((item for item in manifest_items if item.section_id == "ENR-2.1"), None)
    source_url = doc_meta.url if doc_meta else f"file://{doc_path}"
    retrieved_at = doc_meta.fetched_at if doc_meta else datetime.now(timezone.utc).isoformat()
    meta = _load_publication_meta(cache_dir, cycle_id) or {}
    parser = AirspaceParser(cycle_id=cycle_id, effective_date=meta.get("effective_date", cycle_id[:10]))
    return parser.parse_airspace_enr21(doc_path.read_text(encoding="utf-8"), doc_path.name, source_url, retrieved_at)

def _parse_special_use_areas(cycle_id: str, cache_dir: Path) -> Tuple[List[Feature], List[ValidationIssue]]:
    sections_cfg = load_sections_config()
    all_feats: List[Feature] = []
    all_issues: List[ValidationIssue] = []
    manifest_items = ManifestManager.load_manifest(str(cache_dir), cycle_id)
    meta = _load_publication_meta(cache_dir, cycle_id) or {}
    parser = AirspaceParser(cycle_id=cycle_id, effective_date=meta.get("effective_date", cycle_id[:10]))

    for sec_id in ("ENR-5.1", "ENR-5.2"):
        sec_spec = next((s for s in sections_cfg.get("sections", []) if s["id"] == sec_id), None)
        if not sec_spec:
            continue
        fname = sec_spec.get("file_pattern")
        if not fname:
            continue
        doc_path = cache_dir / cycle_id / fname
        if not doc_path.exists():
            continue
        doc_meta = next((item for item in manifest_items if item.section_id == sec_id), None)
        source_url = doc_meta.url if doc_meta else f"file://{doc_path}"
        retrieved_at = doc_meta.fetched_at if doc_meta else datetime.now(timezone.utc).isoformat()
        feats, issues = parser.parse_special_use_document(doc_path.read_text(encoding="utf-8"), sec_id, doc_path.name, source_url, retrieved_at)
        all_feats.extend(feats)
        all_issues.extend(issues)
    return all_feats, all_issues

def _parse_routes(cycle_id: str, cache_dir: Path, known_wpts: Set[str], known_navs: Set[str]) -> Tuple[List[Feature], List[ValidationIssue]]:
    sections_cfg = load_sections_config()
    all_feats: List[Feature] = []
    all_issues: List[ValidationIssue] = []

    meta = _load_publication_meta(cache_dir, cycle_id) or {}
    effective_date = meta.get("effective_date", cycle_id[:10])
    parser = RouteParser(cycle_id=cycle_id, effective_date=effective_date)
    manifest_items = ManifestManager.load_manifest(str(cache_dir), cycle_id)

    for sec_id in ("ENR-3.1", "ENR-3.2"):
        sec_spec = next((s for s in sections_cfg.get("sections", []) if s["id"] == sec_id), None)
        if not sec_spec:
            continue
        fname = sec_spec.get("file_pattern")
        if not fname:
            continue
        doc_path = cache_dir / cycle_id / fname
        if not doc_path.exists():
            continue

        doc_meta = next((item for item in manifest_items if item.section_id == sec_id), None)
        source_url = doc_meta.url if doc_meta else f"file://{doc_path}"
        retrieved_at = doc_meta.fetched_at if doc_meta else datetime.now(timezone.utc).isoformat()

        feats, issues = parser.parse_routes_document(
            html_content=doc_path.read_text(encoding="utf-8"),
            section_id=sec_id,
            document_filename=doc_path.name,
            source_url=source_url,
            retrieved_at=retrieved_at,
            known_waypoint_keys=known_wpts,
            known_navaid_keys=known_navs
        )
        all_feats.extend(feats)
        all_issues.extend(issues)

    return all_feats, all_issues

def _parse_all(cycle_id: str, args=None) -> Tuple[List[Feature], List[ValidationIssue]]:
    """Single comprehensive parse pass: parses all configured airports, runway networks, and global waypoints."""
    cache_dir = _cache_dir(args)
    explicit_icao = getattr(args, "icao", None) if args else None
    target_icaos = [explicit_icao] if explicit_icao else _target_icaos(args)

    all_features = []
    all_issues = []

    for icao in target_icaos:
        try:
            feats, issues = _parse_airport_runways(cycle_id, icao, cache_dir)
            all_features.extend(feats)
            all_issues.extend(issues)

            ap_feat, ap_issues = _parse_airport_summary(cycle_id, icao, cache_dir)
            if ap_feat:
                all_features.append(ap_feat)
            all_issues.extend(ap_issues)

            nav19_feats, nav19_issues = _parse_airport_navaids(cycle_id, icao, cache_dir)
            all_features.extend(nav19_feats)
            all_issues.extend(nav19_issues)

            ops_feats, ops_issues = _parse_airport_operational(cycle_id, icao, cache_dir)
            all_features.extend(ops_feats)
            all_issues.extend(ops_issues)
        except FileNotFoundError as e:
            if explicit_icao:
                raise e
            ad_spec = next((s for s in load_sections_config().get("sections", []) if s.get("scope") == "per_airport"), {})
            doc_pattern = ad_spec.get("file_template", "{icao}.html").format(icao=icao)
            all_issues.append(ValidationIssue(
                publication_id=cycle_id,
                severity=IssueSeverity.ERROR,
                rule_id="RULE_MISSING_DOCUMENT",
                feature_key=f"{icao}/RWY",
                section=f"{icao} AD 2",
                message=str(e),
                raw_snippet=doc_pattern
            ))

    # Parse ENR 4.4 waypoints, ENR 4.1 navaids, and ENR 3.1/3.2 ATS routes if not targeting a single airport
    if not explicit_icao:
        wpt_feats, wpt_issues = _parse_waypoints(cycle_id, cache_dir)
        all_features.extend(wpt_feats)
        all_issues.extend(wpt_issues)

        nav_feats, nav_issues = _parse_enroute_navaids(cycle_id, cache_dir)
        all_features.extend(nav_feats)
        all_issues.extend(nav_issues)

        known_wpts = {f.natural_key for f in wpt_feats}
        known_navs = {f.natural_key for f in nav_feats}
        route_feats, route_issues = _parse_routes(cycle_id, cache_dir, known_wpts, known_navs)
        all_features.extend(route_feats)
        all_issues.extend(route_issues)

        # Airspace and Special-Use Areas (attributes only, geometry strictly None)
        asp_feats, asp_issues = _parse_airspaces(cycle_id, cache_dir)
        all_features.extend(asp_feats)
        all_issues.extend(asp_issues)

        sua_feats, sua_issues = _parse_special_use_areas(cycle_id, cache_dir)
        all_features.extend(sua_feats)
        all_issues.extend(sua_issues)

    return all_features, all_issues

def cmd_parse(args):
    """Parses raw HTML documents for a cycle into normalized features."""
    cycle_id = args.cycle
    print(f"[Pipeline] Parsing features for cycle: {cycle_id}")
    features, issues = _parse_all(cycle_id, args)

    print(f"Extracted {len(features)} feature(s) and {len(issues)} parsing issue(s):")
    for f in features[:15]:
        geom_type = f.geometry.type if f.geometry else "None"
        print(f"  • [{f.feature_type.value:<16}] {f.natural_key:<20} Parent: {str(f.parent_key):<18} Geom: {geom_type:<10} Hash: {f.content_hash[:12]}...")
    if len(features) > 15:
        print(f"  ... and {len(features) - 15} more features.")

    if issues:
        print(f"\nParsing issues found:")
        for iss in issues:
            print(f"  [{iss.severity.value.upper()}] {iss.rule_id} ({iss.section}): {iss.message}")

def cmd_validate(args):
    """Validates parsed features against declarative safety rules."""
    cycle_id = args.cycle
    print(f"[Pipeline] Validating features for cycle: {cycle_id}")

    features, parse_issues = _parse_all(cycle_id, args)
    engine = ValidationEngine()
    val_issues = engine.validate_features(features, cycle_id)
    all_issues = parse_issues + val_issues

    errors = [i for i in all_issues if i.severity == IssueSeverity.ERROR]
    warnings = [i for i in all_issues if i.severity == IssueSeverity.WARNING]

    print(f"Validation complete: {len(features)} feature(s) inspected.")
    print(f"  Errors:   {len(errors)}")
    print(f"  Warnings: {len(warnings)}")

    if all_issues:
        print("\nValidation issues:")
        for iss in all_issues:
            print(f"  [{iss.severity.value.upper()}] {iss.rule_id} ({iss.feature_key}): {iss.message}")

    passed, gate_msg = engine.evaluate_promotion_gate(all_issues)
    if not passed:
        print(f"\nPromotion Gate Check: FAILED ({gate_msg})")
        sys.exit(1)
    else:
        print(f"\nPromotion Gate Check: PASSED ({gate_msg})")

def cmd_diff(args):
    """Compares two cycles by naturalKey + contentHash and reports field-level changes."""
    from_cycle = args.from_cycle
    to_cycle = args.to_cycle
    cache_dir = _cache_dir(args)
    db_cfg = load_database_config()
    loader = MongoLoader(uri=getattr(args, "mongo_uri", None) or db_cfg["uri"])

    # Load from MongoDB if available, otherwise parse from cache
    from_raw = loader.get_features(from_cycle)
    if from_raw:
        from_feats = [Feature.model_validate(f) for f in from_raw]
    else:
        from_feats, _ = _parse_all(from_cycle, args)

    to_raw = loader.get_features(to_cycle)
    if to_raw:
        to_feats = [Feature.model_validate(f) for f in to_raw]
    else:
        to_feats, _ = _parse_all(to_cycle, args)

    print(f"[Pipeline] Diffing Cycle '{from_cycle}' ({len(from_feats)} features) -> '{to_cycle}' ({len(to_feats)} features)...")
    report = DiffEngine.compare_features(from_cycle, to_cycle, from_feats, to_feats)

    print(f"\nDiff Summary:")
    print(f"  NEW:       {report.counts['new']}")
    print(f"  MODIFIED:  {report.counts['modified']}")
    print(f"  DELETED:   {report.counts['deleted']}")
    print(f"  UNCHANGED: {report.counts['unchanged']}")
    print(f"  Disappeared Runways:  {report.disappeared_runways_pct}%")
    print(f"  Disappeared Features: {report.disappeared_features_pct}%")

    if report.changes:
        print("\nDetailed Changes:")
        for c in report.changes[:20]:
            print(f"  [{c.status.value:<8}] {c.feature_type.value:<16} {c.feature_key}")
            for fc in c.field_changes:
                print(f"    • {fc.field}: {fc.previous} -> {fc.new}")

    # Persist change rows to MongoDB
    loader.save_changes(from_cycle, to_cycle, report.changes)

def cmd_load(args):
    """Loads features and issues into MongoDB, evaluating promotion gate and saving rejection on failure."""
    cycle_id = args.cycle
    promote = getattr(args, "promote", False)
    cache_dir = _cache_dir(args)
    db_cfg = load_database_config()
    mongo_uri = getattr(args, "mongo_uri", None) or db_cfg["uri"]

    print(f"[Pipeline] Processing cycle {cycle_id} for MongoDB ({mongo_uri})...")
    features, parse_issues = _parse_all(cycle_id, args)

    engine = ValidationEngine()
    val_issues = engine.validate_features(features, cycle_id)
    all_issues = parse_issues + val_issues

    errors = [i for i in all_issues if i.severity == IssueSeverity.ERROR]
    warnings = [i for i in all_issues if i.severity == IssueSeverity.WARNING]

    # Diff against previously promoted cycle to get disappearance percentages
    loader = MongoLoader(uri=mongo_uri)
    prev_pub = loader.get_promoted_publication()
    disappeared_rwy_pct = 0.0
    disappeared_feat_pct = 0.0
    if prev_pub:
        prev_cid = prev_pub.get("cycleId") or prev_pub.get("cycle_id")
        if prev_cid != cycle_id:
            prev_raw = loader.get_features(prev_cid)
            prev_feats = [Feature.model_validate(f) for f in prev_raw]
            diff_report = DiffEngine.compare_features(prev_cid, cycle_id, prev_feats, features)
            disappeared_rwy_pct = diff_report.disappeared_runways_pct
            disappeared_feat_pct = diff_report.disappeared_features_pct
            loader.save_changes(prev_cid, cycle_id, diff_report.changes)

    passed, gate_msg = engine.evaluate_promotion_gate(all_issues, disappeared_rwy_pct, disappeared_feat_pct)

    meta = _load_publication_meta(cache_dir, cycle_id) or {}
    amdt_id = meta.get("amendment_id", f"AMDT-{cycle_id}")
    eff_date = meta.get("effective_date", cycle_id[:10])

    manifest_items = ManifestManager.load_manifest(str(cache_dir), cycle_id)
    manifest_path = Path(ManifestManager.get_manifest_path(str(cache_dir), cycle_id))
    manifest_sha = ""
    if manifest_path.exists():
        import hashlib
        with open(manifest_path, "rb") as mf:
            manifest_sha = hashlib.sha256(mf.read()).hexdigest()

    status = PublicationStatus.STAGED
    if promote:
        if not passed:
            print(f"[Pipeline] Promotion gate FAILED: {gate_msg}. Setting status to 'rejected'.")
            status = PublicationStatus.REJECTED
        else:
            status = PublicationStatus.PROMOTED
            print(f"[Pipeline] Promotion gate PASSED: {gate_msg}. Setting status to 'promoted'.")

    pub = Publication(
        cycle_id=cycle_id,
        effective_date=eff_date,
        amendment_id=amdt_id,
        source_kind=SourceKind.LIVE,
        manifest_SHA256=manifest_sha,
        ingested_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        stats=PublicationStatistics(
            total_documents=len(manifest_items),
            parsed_features=len(features),
            issue_count=len(all_issues),
            error_count=len(errors),
            warning_count=len(warnings)
        )
    )

    # PERSIST TO DATABASE BEFORE ANY EXIT (Fixes S1: quarantine path is always persisted)
    loader.save_publication(pub)
    deleted_features = loader.save_features(features)
    loader.save_validation_issues(cycle_id, all_issues)

    if promote and status == PublicationStatus.PROMOTED:
        loader.promote_publication(cycle_id, eff_date)

    print(f"Successfully persisted to MongoDB:")
    print(f"  • Publication '{pub.cycle_id}' (status: {pub.status.value})")
    stale = f" ({deleted_features} stale rows removed)" if deleted_features else ""
    print(f"  • {len(features)} features{stale}")
    print(f"  • {len(all_issues)} validation issues")

    if promote and not passed:
        sys.exit(1)

# ---------------------------------------------------------------------------
# Dataset export
#
# Pure serialisation helpers, plus one thin command that reads MongoDB. Keeping
# the shaping functions free of any database handle is what makes them unit
# testable without a running MongoDB (see tests/test_export.py).
# ---------------------------------------------------------------------------

# The camelCase envelope the API already serves. Anything else a Mongo document
# carries (_id, future bookkeeping columns) is deliberately not published.
FEATURE_ENVELOPE_KEYS: Tuple[str, ...] = (
    "naturalKey",
    "parentKey",
    "featureType",
    "publicationId",
    "effectiveFrom",
    "effectiveTo",
    "sourceClass",
    "temporality",
    "contentHash",
    "fields",
)


def strip_mongo_id(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Drop `_id`: an ObjectId is neither JSON serialisable nor part of the contract."""
    return {key: value for key, value in doc.items() if key != "_id"}


def build_feature_collection(feature_docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Wrap stored feature documents in a GeoJSON FeatureCollection, sorted by naturalKey."""
    features: List[Dict[str, Any]] = []
    for doc in feature_docs:
        clean = strip_mongo_id(doc)
        features.append({
            "type": "Feature",
            "geometry": clean.get("geometry"),
            "properties": {key: clean.get(key) for key in FEATURE_ENVELOPE_KEYS},
        })
    features.sort(key=lambda feature: str(feature["properties"].get("naturalKey") or ""))
    return {"type": "FeatureCollection", "features": features}


def build_publication_list(publication_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Publication documents sorted by effectiveDate (then cycleId, for a total order)."""
    cleaned = [strip_mongo_id(doc) for doc in publication_docs]
    return sorted(
        cleaned,
        key=lambda doc: (str(doc.get("effectiveDate") or ""), str(doc.get("cycleId") or "")),
    )


def build_issue_list(issue_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validation issues sorted by (ruleId, featureKey); issues without a feature key sort first."""
    cleaned = [strip_mongo_id(doc) for doc in issue_docs]
    return sorted(
        cleaned,
        key=lambda doc: (str(doc.get("ruleId") or ""), str(doc.get("featureKey") or "")),
    )


def serialise_json(payload: Any) -> str:
    """Deterministic JSON text: stable key order, UTF-8 preserved, trailing newline."""
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def require_exportable(
    cycle_id: str,
    publication_docs: List[Dict[str, Any]],
    feature_docs: List[Dict[str, Any]],
) -> None:
    """Raise ExportError rather than writing empty files for a cycle that cannot be exported."""
    if not publication_docs:
        raise ExportError(f"Unknown cycle '{cycle_id}': no publication document for it in the database.")
    if not feature_docs:
        raise ExportError(
            f"Cycle '{cycle_id}' has no features stored; refusing to write an empty dataset."
        )


def write_export(
    out_root: Path,
    cycle_id: str,
    feature_docs: List[Dict[str, Any]],
    publication_docs: List[Dict[str, Any]],
    issue_docs: List[Dict[str, Any]],
) -> Dict[str, Path]:
    """Write the three dataset files under `<out_root>/<cycle_id>/` and return their paths."""
    target = out_root / cycle_id
    target.mkdir(parents=True, exist_ok=True)
    payloads = {
        "features.geojson": build_feature_collection(feature_docs),
        "publications.json": build_publication_list(publication_docs),
        "validation_issues.json": build_issue_list(issue_docs),
    }
    paths: Dict[str, Path] = {}
    for filename, payload in payloads.items():
        path = target / filename
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(serialise_json(payload))
        paths[filename] = path
    return paths


def cmd_export(args):
    """Exports one cycle's dataset as reviewable JSON, straight from MongoDB."""
    cycle_id = args.cycle
    out_root = Path(args.out)

    try:
        loader = MongoLoader(uri=getattr(args, "mongo_uri", None))
        publication_docs = loader.get_publications(cycle_id)
        feature_docs = loader.get_features(cycle_id)
        issue_docs = loader.get_issues(cycle_id)
    except PyMongoError as exc:
        print(f"[Export] MongoDB error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        require_exportable(cycle_id, publication_docs, feature_docs)
    except ExportError as exc:
        print(f"[Export] {exc}", file=sys.stderr)
        sys.exit(1)

    paths = write_export(out_root, cycle_id, feature_docs, publication_docs, issue_docs)

    print(f"[Export] Cycle:    {cycle_id}")
    print(f"[Export] Features: {len(feature_docs)}")
    print(f"[Export] Issues:   {len(issue_docs)}")
    for filename, path in paths.items():
        print(f"[Export] {filename:<22} {path.resolve()}")


def cmd_run(args):
    """Single-pass end-to-end runner: verifies cache, parses once, validates, diffs, and promotes or quarantines."""
    cycle_id = args.cycle
    cache_dir = _cache_dir(args)

    print(f"============================================================")
    print(f"  RUNNING PIPELINE FOR CYCLE: {cycle_id}")
    print(f"============================================================\n")

    ok, errors = ManifestManager.verify_manifest(str(cache_dir), cycle_id)
    if not ok:
        print(f"[Step 1] Ingest verification failed: {errors}. Fetching first...")
        cmd_ingest_fetch(args)
    else:
        print(f"[Step 1] Raw cache verified: all files match manifest SHA-256.")

    args.promote = True
    cmd_load(args)
    print(f"\nPipeline run completed successfully for cycle '{cycle_id}'.")

def main():
    parser = argparse.ArgumentParser(prog="pipeline", description="Deterministic Aeronautical eAIP Data Pipeline")
    parser.add_argument("--cache-dir", help="Override raw cache directory path")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # check-updates
    p_check = subparsers.add_parser("check-updates", help="Check discovered vs promoted cycles")
    p_check.set_defaults(func=cmd_check_updates)

    # ingest
    p_ingest = subparsers.add_parser("ingest", help="Ingestion phase commands")
    ingest_sub = p_ingest.add_subparsers(dest="action", required=True)

    p_ing_cycles = ingest_sub.add_parser("cycles", help="Discover available cycles")
    p_ing_cycles.set_defaults(func=cmd_ingest_cycles)

    p_ing_fetch = ingest_sub.add_parser("fetch", help="Fetch raw documents for a cycle")
    p_ing_fetch.add_argument("--cycle", required=True, help="Cycle ID (effective date ISO YYYY-MM-DD)")
    p_ing_fetch.add_argument("--from-zip", help="Path to local ZIP archive")
    p_ing_fetch.add_argument("--from-dir", help="Path to local unpacked directory")
    p_ing_fetch.set_defaults(func=cmd_ingest_fetch)

    p_ing_status = ingest_sub.add_parser("status", help="Show manifest status")
    p_ing_status.add_argument("--cycle", required=True, help="Cycle ID")
    p_ing_status.set_defaults(func=cmd_ingest_status)

    p_ing_verify = ingest_sub.add_parser("verify", help="Verify raw cache integrity against manifest")
    p_ing_verify.add_argument("--cycle", required=True, help="Cycle ID")
    p_ing_verify.set_defaults(func=cmd_ingest_verify)

    p_ing_list = ingest_sub.add_parser("list", help="List cached files for a cycle")
    p_ing_list.add_argument("--cycle", required=True, help="Cycle ID")
    p_ing_list.set_defaults(func=cmd_ingest_list)

    # parse
    p_parse = subparsers.add_parser("parse", help="Parse raw HTML into features")
    p_parse.add_argument("--cycle", required=True, help="Cycle ID")
    p_parse.add_argument("--icao", help="Specific airport ICAO (default: all target airports)")
    p_parse.set_defaults(func=cmd_parse)

    # validate
    p_val = subparsers.add_parser("validate", help="Validate features against declarative rules")
    p_val.add_argument("--cycle", required=True, help="Cycle ID")
    p_val.add_argument("--icao", help="Specific airport ICAO (default: all target airports)")
    p_val.set_defaults(func=cmd_validate)

    # diff
    p_diff = subparsers.add_parser("diff", help="Diff two cycles and emit field-level changes")
    p_diff.add_argument("--from", dest="from_cycle", required=True, help="Source cycle ID")
    p_diff.add_argument("--to", dest="to_cycle", required=True, help="Target cycle ID")
    p_diff.add_argument("--mongo-uri", help="MongoDB connection URI")
    p_diff.set_defaults(func=cmd_diff)

    # load
    p_load = subparsers.add_parser("load", help="Load features into MongoDB")
    p_load.add_argument("--cycle", required=True, help="Cycle ID")
    p_load.add_argument("--icao", help="Specific airport ICAO (default: all target airports)")
    p_load.add_argument("--promote", action="store_true", help="Promote publication if checks pass")
    p_load.add_argument("--mongo-uri", help="MongoDB connection URI")
    p_load.set_defaults(func=cmd_load)

    # run
    p_run = subparsers.add_parser("run", help="Run end-to-end pipeline: parse, validate, load, promote")
    p_run.add_argument("--cycle", required=True, help="Cycle ID")
    p_run.add_argument("--icao", help="Specific airport ICAO (default: all target airports)")
    p_run.add_argument("--from-zip", help="Path to local ZIP archive")
    p_run.add_argument("--from-dir", help="Path to local unpacked directory")
    p_run.set_defaults(func=cmd_run)

    # export
    p_export = subparsers.add_parser("export", help="Export one cycle's dataset as JSON files")
    p_export.add_argument("--cycle", required=True, help="Cycle ID")
    p_export.add_argument("--out", required=True, help="Output root directory, e.g. data/export")
    p_export.add_argument("--mongo-uri", help="MongoDB connection URI")
    p_export.set_defaults(func=cmd_export)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
