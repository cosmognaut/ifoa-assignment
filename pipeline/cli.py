"""
Command-line interface for the deterministic aeronautical eAIP data pipeline.
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional

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
from pipeline.parse.runways import RunwayParser
from pipeline.validate.rules import ValidationEngine
from pipeline.load.load import MongoLoader

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
    """
    Compares discovered cycles against local/promoted cycles and prints what needs ingesting.
    """
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
    """Fetches documents for a cycle and writes manifest.jsonl and publication_meta.json."""
    cycle_id = args.cycle
    cache_dir = _cache_dir(args)

    print(f"[Pipeline] Ingesting cycle: {cycle_id}")
    discovery = AIPDiscovery()
    cycles = discovery.discover_cycles()
    cycle_match = next((c for c in cycles if c["cycle_id"] == cycle_id), None)
    if not cycle_match:
        print(f"Error: Cycle '{cycle_id}' not discovered from publisher. Available cycles: {[c['cycle_id'] for c in cycles]}")
        sys.exit(1)

    print(f"Discovered cycle metadata: {cycle_match['amendment_id']} effective {cycle_match['effective_date']}")
    _save_publication_meta(cache_dir, cycle_id, cycle_match)

    sections = discovery.discover_sections_for_cycle(cycle_match)
    print(f"Discovered {len(sections)} target document(s) matching configuration:")
    for s in sections:
        sec_name = s.get("name", s.get("section_id", ""))
        print(f"  • [{s['category']}] {s['section_id']}: {s['document_filename']} ({len(s.get('anchors', []))} anchors) - {sec_name}")

    crawler = PoliteCrawler()
    manifest_items = []

    print("\nFetching documents (with 1 req/s rate limit & byte-for-byte caching)...")
    for s in sections:
        url = s["url"]
        fname = s["document_filename"]
        sec_id = s["section_id"]
        print(f"  Fetching: {url}")
        res = crawler.fetch_url(url, cycle_id, sec_id, fname)
        print(f"    -> Status: {res['status']} | Size: {res['bytes_length']} bytes | SHA256: {res['sha256'][:12]}...")
        manifest_items.append(res)

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

def _parse_airport_runways(cycle_id: str, icao: str, cache_dir: Path):
    doc_path = cache_dir / cycle_id / f"EB-AD-2.{icao}-en-GB.html"
    if not doc_path.exists():
        raise FileNotFoundError(
            f"Missing cached document at {doc_path}. "
            f"Run 'pipeline ingest fetch --cycle {cycle_id}' first."
        )

    with open(doc_path, "r", encoding="utf-8") as f:
        html = f.read()

    manifest_items = ManifestManager.load_manifest(str(cache_dir), cycle_id)
    doc_meta = next((item for item in manifest_items if item.section_id == f"AD-2.{icao}"), None)
    source_url = doc_meta.url if doc_meta else f"https://ops.skeyes.be/html/belgocontrol_static/eaip/eAIP_Main/html/eAIP/EB-AD-2.{icao}-en-GB.html"
    retrieved_at = doc_meta.fetched_at if doc_meta else datetime.now(timezone.utc).isoformat()

    meta = _load_publication_meta(cache_dir, cycle_id) or {}
    effective_date = meta.get("effective_date", cycle_id[:10])

    parser = RunwayParser(cycle_id=cycle_id, effective_date=effective_date)
    return parser.parse_airport_runways(
        icao=icao,
        html_content=html,
        document_filename=doc_path.name,
        source_url=source_url,
        retrieved_at=retrieved_at
    )

def _parse_all_target_airports(cycle_id: str, args=None) -> Tuple[List[Any], List[ValidationIssue]]:
    """
    Loops over target airports (either explicit --icao or all airports from airports.yaml).
    """
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
        except FileNotFoundError as e:
            if explicit_icao:
                raise e
            all_issues.append(ValidationIssue(
                publication_id=cycle_id,
                severity=IssueSeverity.ERROR,
                rule_id="RULE_MISSING_DOCUMENT",
                feature_key=f"{icao}/RWY",
                section=f"{icao} AD 2",
                message=str(e),
                raw_snippet=f"EB-AD-2.{icao}-en-GB.html"
            ))

    return all_features, all_issues

def cmd_parse(args):
    """Parses raw HTML documents for a cycle into normalized features."""
    cycle_id = args.cycle
    target_icaos = [args.icao] if getattr(args, "icao", None) else _target_icaos(args)
    print(f"[Pipeline] Parsing runways for airport(s) {target_icaos} in cycle: {cycle_id}")

    features, issues = _parse_all_target_airports(cycle_id, args)
    print(f"Extracted {len(features)} feature(s) and {len(issues)} parsing issue(s):")
    for f in features:
        geom_type = f.geometry.type if f.geometry else "None"
        print(f"  • [{f.feature_type.value:<15}] {f.natural_key:<20} Parent: {str(f.parent_key):<18} Geom: {geom_type:<10} Hash: {f.content_hash[:12]}...")

    if issues:
        print(f"\nParsing issues found:")
        for iss in issues:
            print(f"  [{iss.severity.value.upper()}] {iss.rule_id} ({iss.section}): {iss.message}")

def cmd_validate(args):
    """Validates parsed features against declarative safety rules."""
    cycle_id = args.cycle
    target_icaos = [args.icao] if getattr(args, "icao", None) else _target_icaos(args)
    print(f"[Pipeline] Validating airport(s) {target_icaos} features for cycle: {cycle_id}")

    features, parse_issues = _parse_all_target_airports(cycle_id, args)
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

def cmd_load(args):
    """Saves publication, features, and validation issues to MongoDB."""
    cycle_id = args.cycle
    promote = getattr(args, "promote", False)
    cache_dir = _cache_dir(args)

    db_cfg = load_database_config()
    mongo_uri = getattr(args, "mongo_uri", None) or db_cfg["uri"]

    print(f"[Pipeline] Loading cycle {cycle_id} into MongoDB ({mongo_uri})...")
    features, parse_issues = _parse_all_target_airports(cycle_id, args)

    engine = ValidationEngine()
    val_issues = engine.validate_features(features, cycle_id)
    all_issues = parse_issues + val_issues

    errors = [i for i in all_issues if i.severity == IssueSeverity.ERROR]
    warnings = [i for i in all_issues if i.severity == IssueSeverity.WARNING]

    manifest_items = ManifestManager.load_manifest(str(cache_dir), cycle_id)
    manifest_path = Path(ManifestManager.get_manifest_path(str(cache_dir), cycle_id))
    manifest_sha = ""
    if manifest_path.exists():
        import hashlib
        with open(manifest_path, "rb") as mf:
            manifest_sha = hashlib.sha256(mf.read()).hexdigest()

    meta = _load_publication_meta(cache_dir, cycle_id) or {}
    amdt_id = meta.get("amendment_id", f"AMDT-{cycle_id}")
    eff_date = meta.get("effective_date", cycle_id[:10])

    passed, gate_msg = engine.evaluate_promotion_gate(all_issues)
    status = PublicationStatus.STAGED
    if promote:
        if not passed:
            print(f"[Pipeline] Promotion gate FAILED: {gate_msg}. Marking status as 'rejected'.")
            status = PublicationStatus.REJECTED
        else:
            status = PublicationStatus.PROMOTED
            print(f"[Pipeline] Promotion gate PASSED: {gate_msg}. Promoting cycle {cycle_id} to 'promoted'.")

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

    loader = MongoLoader(uri=mongo_uri)
    loader.save_publication(pub)
    loader.save_features(features)
    loader.save_validation_issues(cycle_id, all_issues)

    print(f"Successfully loaded into MongoDB:")
    print(f"  • Publication '{pub.cycle_id}' (amendment: {pub.amendment_id}, status: {pub.status.value})")
    print(f"  • {len(features)} features")
    print(f"  • {len(all_issues)} validation issues")

def cmd_run(args):
    """Executes full pipeline: parse -> validate -> load -> promote."""
    cycle_id = args.cycle
    cache_dir = _cache_dir(args)

    print(f"============================================================")
    print(f"  RUNNING END-TO-END PIPELINE FOR CYCLE: {cycle_id}")
    print(f"============================================================\n")

    ok, errors = ManifestManager.verify_manifest(str(cache_dir), cycle_id)
    if not ok:
        print(f"[Step 1] Ingest verification failed: {errors}. Fetching first...")
        cmd_ingest_fetch(args)
    else:
        print(f"[Step 1] Raw cache verified: all files match manifest SHA-256.")

    cmd_validate(args)

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

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
