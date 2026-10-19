"""
Basic CLI interface for testing manifest
"""
import sys
import yaml
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
from pipeline.ingest.crawler import PoliteCrawler
from pipeline.ingest.discovery import AIPDiscovery
from pipeline.ingest.manifest import ManifestManager
from pipeline.models.publication import Publication, PublicationStatus, SourceKind, PublicationStatistics

def cmd_check_updates(args):
    """
    Compares discovered cycles against local/promoted cycles and prints what needs ingesting.
    Runs periodically (daily or weekly via CI, ex. GitHub actions) to see if skeyes has published a new AIRAC cycle. Scans the local filesystem (data/raw) to see which cycle directories exist with valid manifest.jsonl. Then it compares the two sets and outputs a formatted table along with a command required to ingest a new cycle if it's missing.
    """
    print("[Pipeline] Checking for AIP updates from skeyes...")
    discovery = AIPDiscovery()
    discovered_cycles = discovery.discover_cycles()
    if not discovered_cycles:
        print("[Pipeline] No cycles discovered from publisher.")
        return

    cache_dir = Path("data/raw")
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
    """Fetches documents for a cycle and writes manifest.jsonl."""
    cycle_id = args.cycle
    print(f"[Pipeline] Ingesting cycle: {cycle_id}")
    discovery = AIPDiscovery()
    cycles = discovery.discover_cycles()
    cycle_match = next((c for c in cycles if c["cycle_id"] == cycle_id), None)
    if not cycle_match:
        print(f"Error: Cycle '{cycle_id}' not discovered from publisher. Available cycles: {[c['cycle_id'] for c in cycles]}")
        sys.exit(1)

    print(f"Discovered cycle metadata: {cycle_match['amendment_id']} effective {cycle_match['effective_date']}")
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

    # Write manifest.jsonl
    cache_dir = crawler.cache_dir
    manifest_sha = ManifestManager.write_manifest(str(cache_dir), cycle_id, manifest_items)
    print(f"\nManifest written to: {cache_dir}/{cycle_id}/manifest.jsonl")
    print(f"Manifest SHA-256: {manifest_sha}")

def cmd_ingest_status(args):
    """Displays status and counts of an ingested cycle manifest."""
    cycle_id = args.cycle
    cache_dir = "data/raw"
    items = ManifestManager.load_manifest(cache_dir, cycle_id)
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
    cache_dir = "data/raw"
    ok, errors = ManifestManager.verify_manifest(cache_dir, cycle_id)
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
    cache_dir = "data/raw"
    items = ManifestManager.load_manifest(cache_dir, cycle_id)
    print(f"{'Section':<15} {'Status':<12} {'Size':<10} {'SHA256':<16} {'Filename'}")
    print("-" * 80)
    for i in items:
        p = Path(i.local_path).name if i.local_path else "NONE"
        sha_short = i.sha256[:14] + ".." if i.sha256 else "NONE"
        print(f"{i.section_id:<15} {i.status:<12} {i.bytes_length:<10} {sha_short:<16} {p}")

def main():
    parser = argparse.ArgumentParser(prog="pipeline", description="Deterministic Aeronautical eAIP Data Pipeline")
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

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
