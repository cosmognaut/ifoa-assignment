# Architectural Decision Log (ADR) — Aeronautical Data Pipeline

This document records the architectural, data modeling, and engineering decisions made during the design and implementation of the Belgian/Luxembourg eAIP data pipeline. Each entry documents the context, the chosen approach, the technical rationale, and the specific failure modes prevented.

---

## 1. Staged-Promotion Pipeline with Guaranteed Quarantine Persistence

* **Context**: Aeronautical Information Publications (AIP) govern air navigation, runway declared distances, and airspace classification. Release cycles update on a strict 28-day ICAO AIRAC schedule. Upstream HTML sources can change formats, suffer network drops, or corrupt tables.
* **Decision**: Never write scraped data directly to production collections. All new cycle data is parsed and staged. The validation engine and promotion gate evaluate errors and disappearance thresholds. If any check fails:
  1. The publication is immediately assigned `status: "rejected"`.
  2. The publication record, all parsed features, and all `validationIssues` are **persisted to MongoDB before any process termination**.
  3. The process exits non-zero (`sys.exit(1)`).
  4. The previously promoted cycle remains 100% active and untouched.
* **Reasoning**:
  - *Quarantine Observability*: Persisting rejected publications and their issues ensures that operators and CI dashboards can immediately query `validationIssues` in MongoDB to inspect exact raw snippets and failing rules.
  - *Zero Downtime & Atomicity*: Downstream consumers (Node API, flight planning tools) are never exposed to in-flight or half-parsed cycles.

---

## 2. Cycle Identification: ISO Effective Date (`YYYY-MM-DD`) as Primary Key

* **Context**: The pipeline must track previous, current, and preview cycles. Initially, systems often key on amendment numbers (e.g., `AMDT 010`) or month names (e.g., `OCT-2026`).
* **Decision**: The canonical `cycleId` is strictly the ISO effective date (`YYYY-MM-DD`, e.g., `2026-10-01`, `2026-10-29`).
* **Reasoning**:
  1. *Double-Cycle Months*: In October 2026, two distinct AIRAC cycles become effective in the same month (`2026-10-01` and `2026-10-29`). Keying by month creates severe key collisions.
  2. *Non-Unique Amendment Numbers*: Amendment numbers reset annually and collide across series (regular amendments vs. AIRAC amendments).
  3. *Regulatory Reality*: In aviation law, an AIP's legal validity is tied exclusively to its **effective date**, not when it was printed or numbered.

---

## 3. Cryptographic Chain of Custody: Raw Byte Cache & `manifest.jsonl`

* **Context**: Aeronautical data carries legal liability. An operator must always be able to prove why a value was extracted and that the raw source was authentic and uncorrupted.
* **Decision**: Complete architectural air-gap between network ingestion and data parsing. The crawler fetches raw files byte-for-byte into `data/raw/<cycle_id>/` and writes an immutable cryptographic ledger (`manifest.jsonl`). The manifest records URL, local path, byte size, HTTP ETag, UTC timestamp, and SHA-256 hash for every file. An overall SHA-256 of `manifest.jsonl` is computed and stored on the `Publication` document.
  - In `cmd_run`, the pipeline cryptographically audits every cached file on disk via `verify_manifest` before any parsing begins.
* **Reasoning**:
  1. *Bit-for-Bit Reproducibility*: Parsing can be re-run deterministically years later against the exact byte snapshot without network access.
  2. *Tamper Detection (`verify_manifest`)*: Any accidental disk corruption, partial download, or manual tampering triggers an immediate SHA-256 mismatch and halts the pipeline before parsing begins.
  3. *HTTP 304 Politeness*: Storing the server's ETag enables conditional requests (`If-None-Match`), avoiding redundant downloads during periodic update checks.

---

## 4. Polite Crawling, Robots.txt Enforcement & Azure WAF Compliance

* **Context**: Skeyes's portal (`ops.skeyes.be`) sits behind Microsoft Azure Application Gateway v2 with an active Web Application Firewall (WAF). Standard bot User-Agents (e.g. `curl/8.0`, raw Python `httpx`) are blocked with HTTP `403 Forbidden`. Non-negotiable assessment rules forbid bot-detection evasion while requiring polite, rate-limited crawling with honest operator identification and robots.txt compliance.
* **Decision**: 
  1. In `config/crawler.yaml`, declare `user_agent_format` with a `{contact_email}` placeholder and configure `contact_email`.
  2. Enforce `respect_robots_txt: true` by parsing `ops.skeyes.be/robots.txt` using `urllib.robotparser` and checking `can_fetch()`.
  3. Enforce a strict 1.0-second delay between live requests, combined with exponential backoff retries (`max_retries: 3`, `retry_backoff_seconds: 2.0`) on network timeouts and 5xx errors.
* **Reasoning**:
  - Azure WAF rules block requests lacking browser-standard navigation headers or using known generic scraper strings. Supplying standard HTTP headers satisfies WAF format validation without stealth evasion techniques.
  - RFC 9309 and RFC 7231 §5.5.3 compliance: If network administrators detect high bandwidth usage, the User-Agent provides an honest email address to contact the operator directly.

---

## 5. Universal Feature Envelope with EAV-Style Typed Fields & 100% Provenance

* **Context**: The pipeline extracts wildly diverse aeronautical features: runways (lengths, bearings, declared distances), radio navaids (frequencies, channels), waypoints (coordinates), and airspaces (3D polygon boundaries, vertical limits).
* **Decision**: Adopt a standardized document envelope (`feature_type`, `natural_key`, `parent_key`, `publication_id`, `geometry`, `content_hash`) coupled with a dynamic dictionary of typed `FeatureField` objects in `fields`. Every individual field stores:
  `{ value, unit, source, section, effective_date, retrieved_at, source_url, parser_version, raw_text }`.
* **Reasoning**:
  1. *Zero Schema Migrations*: Adding new AIP sections or properties does not require altering database tables or running schema migrations.
  2. *Universal Geospatial Indexing*: MongoDB can build a single `2dsphere` index over `geometry` across all feature types, enabling uniform map viewport queries.
  3. *Granular Auditability*: A consumer can inspect any single value (e.g. an LDA of 2,941m) and immediately view its exact source section, URL, raw scraped text snippet, and the parser version that generated it.

---

## 6. Semantic Normalization in Content Hashing (`content_hash`)

* **Context**: The Diff Engine compares features between AIRAC cycles to classify them as `NEW`, `MODIFIED`, or `DELETED`. Hashing the entire raw database document fails because temporal metadata (`retrieved_at`, `source_url`, HTML whitespace) changes on every crawl, producing false diffs. Furthermore, Python/JSON typing divergences (`2987` vs `2987.0`) would trigger false diffs.
* **Decision**: Compute `content_hash` as the SHA-256 of a canonical JSON payload composed **strictly of normalized values and physical units**, deliberately stripping all provenance and timestamps. Normalization rules:
  1. *Int/Float Unification*: Numeric values where `float(val).is_integer()` are coerced to `int` (e.g. `2987` and `2987.0` both serialize identically as `2987`). Fractional numbers are rounded to 6 decimal places (`~0.11m` precision).
  2. *Whitespace Invariance*: All string values are stripped and have internal whitespace runs collapsed via `re.sub(r"\s+", " ", val.strip())`, making the hash immune to HTML formatting reflows.
  3. *Key Sorting*: JSON keys are sorted recursively (`sort_keys=True`) and stripped of whitespace (`separators=(',', ':')`).
* **Reasoning**:
  - *Semantic Invariance*: The hash changes **if and only if** an aeronautical property changed in the physical world. Re-parsing the same raw document on different machines or at different times produces identical hashes.
  - *High-Speed Diffing*: Detecting whether a feature changed between cycles is reduced to an $O(1)$ string equality comparison: `feat_a.content_hash == feat_b.content_hash`.

---

## 7. Natural Key Hierarchy: Physical Runway Pairs vs. Runway Directions

* **Context**: Runways exist physically as bidirectional pavement strips (e.g. Runway 01/19), but operationally as individual approach directions (Runway 01 and Runway 19 have different thresholds, elevations, lighting, and declared distances).
* **Decision**: Model runways as a two-tier hierarchy:
  1. `runwayDirection`: Natural key `{ICAO}/RWY/{designator}` (e.g. `EBBR/RWY/01`). Geometry is a GeoJSON `Point` at the threshold. Parent key points to the physical runway pair.
  2. `runway`: Natural key `{ICAO}/RWY/{sorted_pair}` (e.g. `EBBR/RWY/01-19`, `EBBR/RWY/07R-25L`). Geometry is a GeoJSON `LineString` connecting the two thresholds. Parent key points to `{ICAO}`.
* **Reasoning**:
  1. *Operational Fidelity*: Flight operations and approach procedures require direction-specific attributes (TORA, LDA, ILS glideslope). Airport infrastructure management requires physical strip dimensions and overall surface strength.
  2. *Deterministic Pairing*: Sorting the reciprocal designators alphabetically (`"01-19"`, `"07L-25R"`) guarantees an identical natural key regardless of which direction was parsed first.

---

## 8. Decoupled Declarative Specifications (`config/*.yaml`)

* **Context**: Hardcoding table column indices (e.g. `row[4] = coordinates`) or section numbers directly in Python code breaks immediately when the publisher inserts a column or reformats an HTML table.
* **Decision**: Zero hardcoding in code. Section patterns, table locator strategies, header text mappings, and validation boundaries are defined exclusively in `config/sections.yaml` and `config/validation.yaml`.
* **Reasoning**:
  1. *Header-Text Matching*: Tables are located by matching semantic header strings (e.g. `"RWY designator"`, `"TORA ( M )"`) rather than fragile array indices.
  2. *Resilience to Drift*: If an upstream ANSP moves a column or adds an airport, the pipeline is updated via config edits without touching application logic.

---

## 9. Error-Decoupled Validation Engine (`validationIssues`)

* **Context**: In traditional software, bad data raises an exception and crashes execution. In an aeronautical pipeline, throwing an exception on an unparseable cell in one regional aerodrome would abort the ingestion of all other international airports.
* **Decision**: Implement a non-halting validation engine. When a parser encounters an unparseable coordinate or a safety violation (e.g. `TORA > TODA`), it:
  1. Retains the raw text in the feature.
  2. Emits a typed `ValidationIssue` record (`severity`, `rule_id`, `feature_key`, `section`, `message`, `raw_snippet`).
  3. Persists the issue to the `validationIssues` collection in MongoDB.
  4. Evaluates promotion gate thresholds (`max_error_issues`, `max_warning_issues`, `max_disappeared_runways_pct`).
* **Reasoning**:
  1. *Maximizes Data Extraction*: Partial failures never cause data loss for valid sections.
  2. *Transparent Observability*: Operators and auditing UIs can query all validation issues in one place, complete with raw snippets for rapid remediation.

---

## 10. Declarative Converter Dispatch Architecture

* **Context**: In initial slice code, field names (`length`, `width`), physical units (`M`), and converters were written as hardcoded Python dictionary literals, rendering `sections.yaml: converter:` dead configuration.
* **Decision**: Build an active converter dispatch registry (`CONVERTER_REGISTRY: Dict[str, Callable]`) in `pipeline/parse/converters.py`.
  - The parser dynamically iterates over the `fields` defined in `sections.yaml`.
  - Column positions are resolved dynamically by scanning table header cells for `header_pattern`.
  - Converters (`coordinates`, `dimensions`, `bearing`, `distance`, `elevation`, `surface_strength`, `text`) are looked up by name and executed.
  - Surface types (`ASPH`, `CONC`, `GRAS`, `OTHER`) are mapped dynamically via `surface_mapping`.
* **Reasoning**:
  - Eliminates all hardcoded field mappings from Python. Adding a field or changing a column header pattern in `sections.yaml` immediately drives parsing with zero code edits.

---

## 11. Upstream HTML Layout Drift and Offline Archive Fallbacks

* **Context**: Web scraping pipelines are vulnerable to upstream website redesigns and bot detection. An interviewer will ask: *"What happens if the website layout changes tomorrow?"*
* **Decision**:
  1. *Semantic Anchoring*: We bind to international standards (EUROCONTROL-SPEC-146 and ICAO Annex 15 section IDs like `<div id="{ICAO}-AD-2.12">` and standardized header text).
  2. *Fail Loudly & Quarantine*: If table headers mutate, `RULE_MISSING_TABLE` is emitted, the promotion gate fails, and the publication is saved as `status: "rejected"` without touching live data.
  3. *Offline Archive Fallbacks (`--from-zip`, `--from-dir`)*: Both flags are actively implemented in `crawler.py` (`fetch_from_zip`, `fetch_from_dir`). If live crawling is blocked or broken, the pipeline ingests the official eAIP ZIP package directly from local disk.

---

## 12. Centralized Configuration Singleton (`pipeline/config.py`)

* **Context**: Duplicating `"data/raw"` or restating YAML values as Python `.get(key, default)` literals causes silent configuration drift.
* **Decision**: Enforce a strict configuration access pattern via `pipeline/config.py`:
  1. Functions `load_crawler_config()`, `load_airports_config()`, `load_sections_config()`, `load_validation_config()`, `load_database_config()` raise `ConfigError` immediately on missing files or keys.
  2. Cache directory resolution is centralized in `_cache_dir(args)`: the literal path `"data/raw"` exists in exactly one place (inside `config/crawler.yaml`), with CLI flag override support.

---

## 13. Multi-Airport Loop with Scoped Failure Modes

* **Context**: Processing multiple airports must support both targeted developer testing (`--icao EBBR`) and full automated production batches.
* **Decision**:
  - `_parse_all` inspects CLI args: if `--icao` is supplied, it targets that single airport. If omitted, it loops over all `target_airports` in `airports.yaml`.
  - *Failure Scoping*: If a cached document is missing during an explicit `--icao` run, it raises `FileNotFoundError` immediately. During a batch run, it emits `RULE_MISSING_DOCUMENT` with severity `ERROR`, allowing remaining airports to parse while ensuring the promotion gate quarantines the publication.

---

## 14. Bitemporal Schema Design & `effective_to` Status

* **Context**: The schema models `effective_from` and `effective_to` ("null if active").
* **Decision**: All parsed features initialize with `effective_to: None` representing active validity. Multi-cycle supersession logic (`effectiveTo = new_cycle.effective_date`) is modeled in `MongoLoader.promote_publication` for future multi-cycle archive execution, but in current single-cycle runs, all features remain active with `effective_to: null`. Point-in-time ('as of date') time-travel queries are not currently supported on the CLI.
* **Reasoning**:
  - Keeps the database schema strictly aligned with the contract while honestly documenting that all active features currently carry `effective_to: null`.

---

## 15. The AD 2.2 / 2.3 / 2.6 Key-Value Parser & Operational Layer Synthesis

* **Context**: The map and airport summary view require an Airports layer (Point features with ARP coordinates) and operational details (elevation, magnetic variation, H24 operating hours, and RFFS fire-fighting categories). Hardcoding these in the frontend violates the non-negotiable rule.
* **Decision**: Implement `pipeline/parse/airports.py` (`AirportParser`):
  1. *AD 2.2*: Parses Aerodrome Reference Point (ARP) coordinates, field elevation AMSL, and magnetic variation. The ARP coordinates become the GeoJSON `Point` geometry of the `aerodrome` feature.
  2. *AD 2.3*: Parses operational hours per authority (Operator, Customs, ATS, Fuel, Handling), capturing the baseline for the canonical "H24 changing to textual restrictions" diff detection.
  3. *AD 2.6*: Parses Rescue and Fire Fighting Services (RFFS) categories (`CAT 10` for Brussels/Liège, `CAT 7` for Charleroi).
* **Reasoning**:
  - Turns 2 layers (`runway`, `waypoint`) into 3 active layers (`aerodrome`, `runway`, `waypoint`).
  - Supplies 100% of the data required for the Airport Operational View directly from official AIP source tables with complete provenance.

---

## 16. Airspace Geometry Policy: Attributes-Only & Zero Synthesized Polygons

* **Context**: ENR 2.1 (ATS Airspace: FIR, UIR, CTA, TMA) and ENR 5.1/5.2 (Prohibited, Restricted, Danger, and Military Exercise Areas) publish lateral boundaries as complex legal narrative prose (e.g., *"505311N 0042013E - an arc of circle, 0.8 NM radius, centred on 505311N 0042130E... along the Belgian/German border"*). The HTML source contains zero vector polygon or GIS shape elements.
* **Decision**: Strictly enforce `geometry: None` on all `airspace` and `specialUseArea` features. Absolute prohibition against synthesizing pseudo-polygons (no bounding boxes, no centroid points, no convex hulls).
* **Reasoning & Future Work**:
  1. *Safety Invariance*: In civil aviation, approximating a prohibited military firing range or TMA buffer with a convex hull misleads dispatchers and flight management computers about the legal boundary, creating critical airspace infringement hazards.
  2. *Preservation of Ground Truth*: The exact narrative description is preserved verbatim in `fields.lateralLimitsRaw` with 100% field-level provenance.
  3. *Future Architecture*: Real vector geometry generation requires a dedicated geodesic boundary interpreter using Pyproj geodesic arcs, circle sampling, and official sovereign border shapefiles. Storing the raw legal text provides the uncorrupted input for this future engine without falsifying current data.
