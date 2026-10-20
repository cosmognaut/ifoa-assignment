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
* **Known limitation** — the implementation departs from the decision above in one place: `discovery.py` appends the edition role to the ISO key when two editions share an effective date (the bare `YYYY-MM-DD` stays the key otherwise). That suffix makes the key stable *within* a run but not *across* runs, because the collision is resolved by iteration order over `publisher.cycle_folders`, so which edition keeps the bare date depends on which editions exist at that moment. Today `ingest cycles` returns `2026-10-01` for the current edition and `2026-10-01-previous` for the previous one, while the committed cache, the database and `data/export/` are all keyed `2026-10-01-current`; the one stored diff therefore pairs `2026-10-01` with `2026-10-01-current` and classifies all 619 features as `NEW` — an artefact of the key, not a change record. Two candidate fixes, both making the key order-independent: always format it as `{date}-{role}`, or keep the bare effective date as the key and store the role in a separate field. Neither has been applied: renaming the key would orphan the committed `data/raw/2026-10-01-current/` cache, the loaded database and `data/export/`, and the raw bytes cannot be re-fetched to rebuild them because the publisher's robots policy refuses automated access.

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
  4. `cmd_ingest_fetch` writes `manifest.jsonl` only when the run produced at least one usable document (`fetched`, `fetched_from_zip`, `fetched_from_dir`, `not_modified`, `cached`). A run in which nothing was usable writes no manifest, names the reason, and exits non-zero; a partial run writes the manifest and prints a warning naming the failed section ids.
* **Reasoning**:
  - Azure WAF rules block requests lacking browser-standard navigation headers or using known generic scraper strings. Supplying standard HTTP headers satisfies WAF format validation without stealth evasion techniques.
  - RFC 9309 and RFC 7231 §5.5.3 compliance: If network administrators detect high bandwidth usage, the User-Agent provides an honest email address to contact the operator directly.
  - *Robots enforcement is now the binding constraint, not the WAF.* Fetched with the configured User-Agent, `https://ops.skeyes.be/robots.txt` returns `200` and the body `User-Agent: *` / `Disallow: /`. The crawler parses it and `can_fetch()` refuses every document, so a live crawl of the HTML portal returns `blocked_by_robots` with 0 bytes for all 12 documents. A live crawl is therefore refused **by design**, not broken. Refreshing a cycle goes through the official offline package — `ingest fetch --from-dir <dir>` or `--from-zip <archive>`, which exist for exactly this case — and never through the live portal.
  - *The committed snapshot predates enforcement.* `data/raw/2026-10-01-current/manifest.jsonl` records 12 `fetched` documents (5,346,418 bytes) at `2026-10-19T15:37`; the same request today is refused. That manifest is the chain of custody for the shipped dataset and is why point 4 exists: a blocked re-fetch must not overwrite it. Point 4 was added after a blocked run replaced all 12 rows and `ingest verify` then crashed on the empty `local_path` values.
  - *Unavailable is not permission.* When `robots.txt` is not `200` (for example a WAF `403`), `_init_robots` sets `allow_all` — the RFC 9309 reading of an unreachable robots file — and now prints which branch it took. That fallback is deliberately visible rather than silent, because it is the difference between "the publisher allows this" and "we could not ask".

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

* **Context**: ENR 2.1 (ATS Airspace: FIR, UIR, CTA, TMA) and ENR 5.1/5.2 (Prohibited, Restricted, Danger, and Military Exercise Areas) publish lateral boundaries as complex legal narrative prose. `SUA/EBR01` reads *"505311N 0042013E - an arc of circle, 0.8 NM radius, centred on 505311N 0042130E and traced clockwise to 505316N 0042247E - an arc of circle, 2.7 NM radius"*; other records follow national borders instead of arcs, and `SUA/EBD29` names the Belgian-German, Belgian-Luxembourg and Belgian-French borders in the same field. The HTML source contains zero vector polygon or GIS shape elements.
* **Decision**: Strictly enforce `geometry: None` on all `airspace` and `specialUseArea` features. Absolute prohibition against synthesizing pseudo-polygons (no bounding boxes, no centroid points, no convex hulls).
* **Reasoning & Future Work**:
  1. *Safety Invariance*: In civil aviation, approximating a prohibited military firing range or TMA buffer with a convex hull misleads dispatchers and flight management computers about the legal boundary, creating critical airspace infringement hazards.
  2. *Preservation of Ground Truth*: The exact narrative description is preserved verbatim in `fields.lateralLimitsRaw` with 100% field-level provenance.
  3. *Future Architecture*: Real vector geometry generation requires a dedicated geodesic boundary interpreter using Pyproj geodesic arcs, circle sampling, and official sovereign border shapefiles. Storing the raw legal text provides the uncorrupted input for this future engine without falsifying current data.

---

## 17. Cycle Diff Engine: `contentHash` Identity, Field-Level Change Records

* **Context**: Each AIRAC cycle republishes the whole AIP. Storing cycle N+1 as a replacement destroys the reviewer's ability to see what actually changed, and re-parsing the same bytes must never be able to fabricate a change.
* **Decision**: `pipeline/diff/engine.py` joins the two cycles on `naturalKey` (ADR 7) and classifies each key as `NEW`, `MODIFIED`, `DELETED` or `UNCHANGED` by comparing `contentHash` only. `MODIFIED` rows carry a list of `FieldChange` objects (`pipeline/models/change.py`), each holding `field`, `previous`, `new` and the originating `section`. Disappearance percentages computed in the same pass feed `promotion_gate.max_disappeared_runways_pct` (`0.0`) and `max_disappeared_features_pct` (`5.0`) in `config/validation.yaml`, and are read by `pipeline/validate/rules.py`.
* **Reasoning**:
  - *No phantom diffs*: `compute_content_hash` (`pipeline/utils/hash_utils.py`) hashes normalised values and units only — `source`, `section`, `effective_date`, `retrieved_at`, `source_url`, `parser_version` and `raw_text` are excluded by construction. Re-parsing identical bytes, or a re-stamp of `retrieved_at`, cannot register as a change.
  - *Reviewable, not just detectable*: a `FieldChange` naming `lda: 2941 -> 2850` with `section: EBBR AD 2.13` is what a dispatcher can act on; a boolean "modified" is not.
  - *Evidence*: `tests/test_diff_and_quarantine.py` (synthetic two-cycle diff; gate thresholds), `pipeline diff --from … --to …`, `GET /api/changes`.
  - *Exercised once against a real preview*: the first genuine two-cycle run diffed the committed `2026-10-01-current` against the `2026-10-29` preview edition — extracted from the publisher's official offline package (12 documents via HTTP range requests into the 1.57 GB `eAIP_Package.zip`, because the HTML portal refuses automated access). Result: 664 features versus 663, `NEW` 1 (`SUA/EBR05G`, a military UAS training zone added to ENR 5.1), `MODIFIED` 0, `DELETED` 0, `UNCHANGED` 663, disappeared runways and features both `0.0 %`; the preview parsed to 0 errors and the same 5 warnings as the current cycle. One feature is a thin sample rather than a stress test of the change classification, so `tests/test_diff_and_quarantine.py` (synthetic NEW/MODIFIED/DELETED plus field-level changes) remains the coverage for the other three buckets. `cmd_diff` persisted the row to the `changes` collection; nothing was loaded or promoted.

---

## 18. Paragraph-Sourced Fields: AD 2.1 Is Not a Table

* **Context**: The parser dispatch (`config/sections.yaml`) assumed every field comes from an HTML table cell. AD 2.1 (Aerodrome Location Indicator and Name) publishes a single `<p>` element reading `EBBR — BRUSSELS / Brussels-National`, so a table locator finds nothing and the aerodrome would ship nameless.
* **Decision**: Section specs may declare `source: "paragraph"` with a `pattern`. `pipeline/parse/airports.py` honours that declaration for AD 2.1, applies the regex to the last `<p>` of the section, and stores the entire paragraph as `raw_text`. When the paragraph is missing or does not match, the parser emits `RULE_MISSING_AIRPORT_NAME` at severity `warning` and continues with the remaining sections.
* **Reasoning**:
  - *Degradation over failure*: a missing name is a cosmetic defect; aborting the aerodrome would discard coordinates, hours and RFFS data that parsed correctly.
  - *Provenance survives a non-table source*: `raw_text` holds the whole sentence, not the captured group, so the reviewer sees the same string the publisher printed.
  - *Evidence*: `config/sections.yaml` (`AD-2.1`, line 116), `pipeline/parse/airports.py` (paragraph branch), `tests/test_golden_values.py` (asserts `airportName == "BRUSSELS / Brussels-National"` and `raw_text == "EBBR — BRUSSELS / Brussels-National"`, plus the missing-name warning path).

---

## 19. Read-Only GeoJSON API

* **Context**: The dataset needed a consumer that did not require a Python environment, without turning the pipeline into a service.
* **Decision**: `api/server.ts` exposes eight read endpoints — `/api/health`, `/api/publications`, `/api/feature-types`, `/api/features`, `/api/features/:naturalKey`, `/api/airports/:icao`, `/api/changes`, `/api/issues`. It is strictly read-only: no writes, no auth, no mutation surface. The stored document is already GeoJSON-shaped, so the API passes `geometry` and `properties` through untouched; keys inside `fields` keep their snake_case (`effective_date`, `raw_text`) because they are the provenance contract, not a presentation concern.
* **Reasoning**:
  - *Full set by default*: `/api/features` returns every matching feature, including the 241 with `geometry: null`; `withGeometry=true` narrows to the 422 mappable ones. Filtering geometry by default would hide the airspace register from the client that needs it.
  - *Slash-safe detail route*: natural keys contain slashes (`EBBR/RWY/25L`), so the detail route is a regex (`/^\/api\/features\/(.+)$/`) rather than a `:param` segment.
  - *No second source of truth*: connection URI, database name and all four collection names are read from `config/database.yaml` (`api/db.ts`); nothing is duplicated in TypeScript.
  - *`_id` never leaks*: every query carries an `_id: 0` projection.
  - *Evidence*: `api/test/api.test.ts`, `config/database.yaml`.

---

## 20. Validation Made Visible

* **Context**: The pipeline had persisted `validationIssues` since ADR 9, but nothing read them. A legitimate cross-border STAR fix — `WPT/GIVOR` at 48.658611° N / 6.391389° E, south of the widened operational bounding box (49.0–52.0° N, 2.0–7.0° E, plus `margin_degrees: 0.25`) — surfaced as `RULE_OPERATIONAL_BBOX`, which to a reviewer looks exactly like a parser defect.
* **Decision**: `GET /api/issues` serves the collection with `publicationId`, `featureKey` and `severity` filters. The web inspector renders a muted block under the field list showing rule id, severity, human message and the offending snippet.
* **Reasoning**:
  - *A warning is information, not noise*: the issue is real (the waypoint is outside the box the rule defines) and correct (the waypoint is a foreign fix). Hiding it would hide the rule; showing it with its severity communicates "checked, tolerated".
  - *Severity discipline*: `RULE_OPERATIONAL_BBOX` is configured as `warning` in `config/validation.yaml`, so it cannot quarantine a cycle — only `error`-severity rules and the disappearance thresholds can.
  - *Evidence*: `api/test/api.test.ts`, `web/src/test/inspector.test.tsx`, `config/validation.yaml`.

---

## 21. TypeScript With No Build Step

* **Context**: A conventional Node service would add a bundler, a `dist/` directory and a compile step between the source a reviewer reads and the artifact that runs.
* **Decision**: The API ships its own `.ts` files. Node 26 strips types natively, so `node server.ts` (`api/package.json`) is the production entry point — there is no `dist/`, no bundler and no emitted JavaScript. `tsc --noEmit` is the gate, with `strict` and `erasableSyntaxOnly` enabled.
* **Reasoning**:
  - *`erasableSyntaxOnly` as an architectural constraint*: the compiler rejects `enum`, `namespace` and parameter properties, because those emit runtime code rather than being erasable. That keeps "types disappear at runtime" literally true instead of aspirational.
  - *Queries are checked, not asserted*: domain shapes are `interface`s and Mongo queries are typed `Filter<FeatureDoc>` / `Filter<PublicationDoc>`, so a misnamed field is a compile error rather than an empty result set.
  - *Evidence*: `api/package.json` (`start`, `typecheck`), `api/tsconfig.json` (`strict`, `erasableSyntaxOnly`), `api/server.ts` (`Filter<…>` annotations), `api/test/api.test.ts` (26 tests, run with `node --test`).

---

## 22. Map Runtime: Leaflet to MapLibre GL

* **Context**: The first map used Leaflet with CARTO raster tiles. CARTO's raster endpoints turned out to return one byte-identical placeholder image for every z/x/y, with or without an API key (three different tiles, keyed and unkeyed, hash to the same 2,513-byte PNG). The key unlocks only the vector style, and Leaflet's renderer draws raster tiles and vector overlays, not Mapbox Vector Tiles.
* **Decision**: Replace Leaflet with MapLibre GL rendering one trimmed GeoJSON source over CARTO Dark Matter *nolabels*: 422 drawable features, properties reduced to `naturalKey`, `featureType`, `parentKey`, `effectiveFrom`. Six feature layers (`route-line`, `runway-line`, `runwaydir-point`, `waypoint-point`, `navaid-point`, `aerodrome-point`) plus two selection layers. Interaction is one `queryRenderedFeatures` call over an ±8 px box around the pointer, for both click and hover-cursor, with point layers ordered before line layers so `hits[0]` prefers a point.
* **Reasoning**:
  - *No place names to fight*: the base style is 66 layers — 56 line, 9 fill, 1 background, zero symbol. Nothing in the basemap can compete with the aeronautical labels, because the basemap has none.
  - *Hit testing without hit layers*: the Leaflet build gave every drawable feature a visible layer plus an invisible 14 px hit layer (`git show 2b06bf5^:web/src/components/MapView.tsx`) — 790 layers for 395 features. MapLibre answers the same question with one rendered-feature query.
  - *No payload tax on first paint*: MapLibre is imported through `React.lazy`, so the entry chunk stays at 77.7 kB gzip and the map lives in its own 290 kB gzip chunk; the Overview never downloads it.
  - *Evidence*: `web/src/components/MapView.tsx`, `web/src/App.tsx`, `web/vite.config.ts`, `npm run build` output, `curl https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json`.

---

## 23. Frontend Shape, Type and Licensing

* **Context**: Three views needed the same chrome — the top bar, the docked inspector and the provenance block — and the type stack had to be redistributable with the repository.
* **Decision**: One shell: a 72 px top bar and a docked 400 px inspector, with views (Overview, Map, Register, Airport) swapped inside it. Type is Clash Grotesk and Clash Display for display text and IBM Plex Mono for anything numeric, all self-hosted as woff2 under `web/public/fonts` (7 files, no CDN at runtime).
* **Reasoning**:
  - *Shell, not three pages*: swapping a view inside a persistent shell keeps selection state, the inspector and provenance rendering in one place; three pages would triplicate them.
  - *Licensing is a build input*: ABC Favorit was rejected because the copy available for evaluation carried an aggregator "free for personal use" notice — not a redistribution licence. Clash Grotesk and Clash Display are Fontshare families (free for commercial use) and IBM Plex Mono is OFL 1.1; those are upstream terms, and the repository ships no separate font licence file, so they are recorded here rather than asserted by an artefact. Self-hosting also removes a runtime dependency on a font CDN.
  - *Numerals stay in the mono face*: measured in Chromium at 100 px, Clash Grotesk sets `1111111111` at 328 px, `0000000000` at 701.4 px and `8888888888` at 612.3 px — proportional digits — and `font-feature-settings: "tnum"` changes nothing (no `tnum` feature). IBM Plex Mono sets all three at 600 px. Any column of figures in Clash Grotesk would jitter, so digits and tabular values carry `.mono`.
  - *Evidence*: `web/src/styles/tokens.css`, `web/public/fonts/`, `web/src/App.tsx`. The digit-width measurement is reproducible in the running console; `ABC Favorit` appears nowhere in the repository, so the rejection is recorded here as rationale rather than as an artifact.

---

## 24. Coordinates Are Rendered for Humans

* **Context**: GeoJSON stores positions as `[longitude, latitude]`. A reviewer reading the inspector saw `5.317778, 50.770833` and read it as a latitude — the array is correct, the presentation was not.
* **Decision**: `FieldRow` renders coordinate fields latitude first with hemisphere suffixes — `50.770833° N, 5.317778° E` — and repeats the publisher's own DMS string (`504615N 0051904E`) on the line beneath for cross-checking. Detection is by field name (`/coordinate/i`), not by a hardcoded list of keys.
* **Reasoning**:
  - *Reader order over storage order*: aviation reads lat/lon; GeoJSON writes lon/lat. The UI resolves that in one place instead of asking every consumer to remember.
  - *Two renderings, one value*: the decimal pair is what a client can compute with, the raw DMS is what the AIP printed. Showing both makes a transcription error visible rather than silent.
  - *Name-based, not enumeration-based*: a new coordinate field in `config/sections.yaml` is rendered correctly without touching TypeScript.
  - *Evidence*: `web/src/components/FieldRow.tsx`, `web/src/test/fieldRow.test.tsx` (asserts lat-first output, asserts the AIP string is present, asserts the old lon-first string is absent). The behaviour arrived in `177f9e1 fix(web): fit the map to the data, latitude-first coordinates, lazy map chunk`; the worked example above is the stored record `WPT/ANQUZ` (`data/export/2026-10-01-current/features.geojson`).

---

## 25. Accent Semantics: Interaction Only

* **Context**: In an early build the accent (`#C8FF00`) marked interaction *and* status: the overview aerodrome cards painted a lime dot for `promoted`, identical on all three cards.
* **Decision**: The accent means interaction and selection only — active tab, selected row bar, selection ring, focus. Status is neutral, amber (`--warn-*`) or red (`--err-*`); there is no green status token. The `promoted` dot was removed from the cards and their footer now states an action (`Open summary →`), which turns accent on hover.
* **Reasoning**:
  - *One colour, one meaning*: if lime can mean "passing" and "clickable", the operator has to decode context on every glance.
  - *Status lives in one place*: the promoted/staged/rejected state belongs to the run panel and the top-bar pill, where it is labelled, not to a repeated dot.
  - *Evidence*: `web/src/styles/tokens.css` (`--accent`, `--warn-*`, `--err-*`), `web/src/components/OverviewView.tsx` (no `promoted` reference remains in the card path).

---

## 26. Test Strategy, Two Layers

* **Context**: The project has two very different things to prove: that Mongo query shapes and stored values are right, and that presentation rules (which are pure functions of props) hold.
* **Decision**: Two layers, deliberately asymmetric.
  - *API layer* (`api/test/api.test.ts`, 26 tests, `node --test`): runs against the real MongoDB and asserts genuine aeronautical values — `lda.value == 3210`, `lda.unit == "M"`, `lda.section == "EBBR AD 2.13"` — plus the filter, projection and 404 behaviour of every endpoint.
  - *Web layer* (`web/src/test/`, Vitest + jsdom + Testing Library): hermetic. No network, no API, no database. It covers the presentation invariants — the `UNL` collapse, latitude-first coordinates, acronym casing in field labels, the validation block, register filtering, provenance labels.
* **Reasoning**:
  - *A mocked database proves nothing about query shapes*: the failure this API can have is a projection or a filter that silently returns the wrong set. Only the real store can falsify that.
  - *The web app cannot need a database to test a formatter*: `renderValue` and `coordinateText` are functions of one value. Making them require a backend would make the suite slower and less trustworthy.
  - *Evidence*: `api/test/api.test.ts`, `web/src/test/*.test.tsx`, `npm test` in `web/`.

---

## 27. Dataset Export

* **Context**: The assessment asks for the structured extracted dataset as a deliverable. Before this decision the data existed only in MongoDB, so a reviewer needed Mongo, a cycle load and the API running in sequence to see a single value.
* **Decision**: `pipeline export --cycle <id> --out <dir>` writes three files under `<dir>/<cycle>/`: `features.geojson` (one `FeatureCollection`, the same camelCase envelope the API serves), `publications.json` and `validation_issues.json`. Features are sorted by `naturalKey`, issues by `(ruleId, featureKey)`, publications by `effectiveDate`; serialisation is `indent=2, sort_keys=True, ensure_ascii=False` with a trailing newline.
* **Reasoning**:
  - *Deterministic artefact*: re-running the export produces byte-identical files (`sha256sum -c` passes), so a committed snapshot shows real data changes instead of timestamp churn.
  - *Nothing invented, nothing dropped*: `geometry: null` survives as an explicit null (241 of 663 features), and `_id` is projected away at every level.
  - *Fail before writing*: an unknown cycle, a cycle with no features, and an unreachable database all exit non-zero without creating a directory, so a half-written dataset can never be mistaken for a result.
  - *Pure helpers, thin shell*: the shaping functions in `pipeline/cli.py` take lists of documents and return values; only `cmd_export` touches `MongoLoader`. That is what lets `tests/test_export.py` run with no MongoDB at all.
  - *Evidence*: `pipeline/cli.py`, `tests/test_export.py`, `data/export/2026-10-01-current/`.

---

## 28. The Converter Contract Is Enforced, Not Advisory

* **Context**: Four dispatch sites — `pipeline/parse/airports.py` (AD 2.2/2.3 key-value fields, AD 2.4 operational hours) and `pipeline/parse/runways.py` (AD 2.12 physical characteristics, AD 2.13 declared distances) — read a field's converter in two halves, and both halves were silent. `f_spec.get("converter", "text")` invented a name the spec had not declared; `CONVERTER_REGISTRY.get(name, lambda x: (x, None))` invented a callable when the declared name did not exist. A misspelled `converter:` therefore did not fail: the cell degraded to the raw-text passthrough, so a field declared as a float shipped as prose, and no test noticed because nothing asserted that a declared name ever resolved. A second instance of the same defect was the `boolean` name on AD 2.20 `pprRequired`, which referenced a converter that has never existed and was inert only because `pipeline/parse/operational.py` derives that field from `converters_regex` patterns and never consults the key.
* **Decision**: `resolve_converter` (`pipeline/parse/converters.py`) reads the name with `require_str` and indexes `CONVERTER_REGISTRY` directly — a missing key raises `ConfigError`, an unknown name raises `ConfigError` listing the known converters. `validate_section_converters` checks every name declared in `config/sections.yaml` against the registry, and is invoked at module import, so a bad config fails before any document is opened rather than on the third runway. Registry membership is checked unconditionally: an earlier attempt added a `converter_driven: false` exemption that skipped the check for such fields, which recreated exactly the class of defect it was meant to remove — `AD-2.20/pprRequired` sat behind it naming `boolean`. That exemption was removed and `tests/test_converter_contract.py` fails if it returns. `AD-2.20/pprRequired` now declares no `converter` key at all, and the config comment states why.
* **Reasoning**:
  - *The defect was two silent defaults, not one*: fixing only the callable lookup would have left `.get("converter", "text")` free to substitute a name, so both the name and the resolution had to become strict. This is the rule already stated in `pipeline/config.py` — a missing config value raises instead of acquiring a second meaning — applied to the converter plane.
  - *Import-time, not parse-time*: a declaration error is a property of the configuration, not of the document, so it should be detected with no cycle loaded. `validate_section_converters` at import means a typo fails the test suite and the first CLI invocation, not a parse that happens to reach the affected section.
  - *The exemption proved the point against itself*: `converter_driven: false` read as "this field does not use a converter" and was implemented as "skip validation". Since no dispatch site reads the flag, a field whose value a parser derives itself never reaches the registry — the honest shape is to declare no `converter` key. Anything carrying one must name a real converter, dispatched or not.
  - *Behaviour change, deliberately accepted*: a new field spec that forgets its `converter` key now hard-fails at import instead of defaulting to text. That is intended, and it is the point of the change, but it is a change worth knowing before adding a section.
  - *Evidence*: `pipeline/parse/converters.py` (`resolve_converter`, `validate_section_converters`, the import-time call), `config/sections.yaml` (`AD-2.20`, line 473), `tests/test_converter_contract.py` (misspelled name raises; the `converter_driven: false` exemption is pinned as *not* an exemption; the shipped `pprRequired` spec carries no `converter` key). Parsed output unchanged: 663 features, validation 0 errors / 5 warnings, gate PASSED.

---

## 29. `save_features` Reconciles Instead of Only Upserting

* **Context**: `MongoLoader.save_features` upserted and nothing else, so the collection accumulated whatever a previous parser run had written and no longer matched the current parse. After the ILS natural-key scheme changed, 24 old-shaped rows survived beside the 24 new ones and `GET /api/features` reported 71 navaids instead of 47. The defect is not specific to that rename: any key that changes shape, and any section that stops yielding a feature at all, leaves orphans behind forever, and nothing in the pipeline reported their existence.
* **Decision**: After the bulk upsert, delete every row whose `naturalKey` is absent from the incoming set — scoped to the publication ids present in that call only, never collection-wide. The unique index is `(publicationId, naturalKey)`, so the same key legitimately exists once per cycle and a collection-wide delete on keys alone would erase other cycles. The method returns the deleted count and `cmd_load` prints it. An empty feature list is a no-op unless the caller names a publication explicitly with `publication_id=`, in which case it means "this cycle now has no features" and all of that publication's rows are removed.
* **Reasoning**:
  - *Scoped by publication, not by key*: reconciliation is driven from `by_publication`, a map of publication id to the natural keys that parse produced, so a multi-publication call can only ever delete within the cycles it is loading. That is what makes the unique index's second component load-bearing rather than incidental.
  - *An accidental empty call must not wipe a cycle*: the no-op default is defensive because the failure mode is asymmetric — a missed deletion is recovered by the next load, a mass deletion is not.
  - *Visible staleness, not invisible staleness*: the load output now reports removals (`• 663 features (35 stale rows removed)` on the run that cleaned up), so the reconciliation is observable instead of inferred from a count that looks wrong.
  - *Cost accepted — the reconcile trusts the parse completely*: a partial or buggy parse now deletes rows that should have been kept, so a parser regression surfaces as deletion rather than being masked by surviving stale rows. That trade was chosen deliberately: visible deletion is easier to detect than invisible staleness, and the previous behaviour is exactly how 24 phantom navaids shipped unnoticed.
  - *Evidence*: `pipeline/load/load.py` (`save_features` docstring states both the scope and the empty-list rule), `pipeline/cli.py` (`cmd_load` prints the returned count), `tests/` (the loader reconciliation tests).

---

## 30. Special-Use-Area Natural Keys Are Normalised From Config and Validated Strictly

* **Context**: 29 special-use-area natural keys were malformed. A legend table of single-letter keys had been accepted as airspace, and real designations carried underscores, an en dash and an interior slash straight through into the key — `ELTSA6 – DIEKIRCH NORTH` and `TRA/TSA11` being the shapes that broke it. Meanwhile `natural_key_formats` for the family was a prefix check (`^SUA/`) that could not have caught any of them, so the validator reported success on garbage. Because the natural key *is* the diff identity (ADR 17), a malformed key is not cosmetic: the key changes shape as soon as the parser is corrected, and the next cycle then shows a phantom `DELETED` beside a phantom `NEW` for one unchanged feature.
* **Decision**: Keys are normalised from `config/sections.yaml` under `natural_key_normalisation` — `replacements` (underscore, en dash, em dash, interior slash), `whitespace_pattern` / `whitespace_replacement`, `code_separator: " - "` marking where the designator ends and the place name begins, `key_charset`, and `duplicate_suffix_template: "{base}-{n}"` — with the verbatim designation kept in `fields.designation` rather than discarded. Tables inside a section that publish no airspace are rejected by `non_data_row_rules` and counted (`[Parse] ENR 5.2: skipped 2 non-data rows`). The validator patterns are `^SUA/[A-Z0-9][A-Z0-9 -]*$` and `^ASP/[A-Z0-9][A-Z0-9 -]*$`.
* **Reasoning**:
  - *Normalisation belongs in config, not in Python*: the replacement table is a statement about the publisher's typography, which is exactly the kind of thing ADR 8 keeps out of code. It is also the only place a reviewer can see the whole character policy at once.
  - *Keep the source string*: normalising the key must not destroy what the AIP printed, so the verbatim designation is stored as a field with full provenance (ADR 5). A key is an identity; the designation is evidence.
  - *A legend is not an airspace*: the single-letter keys came from a legend table that happens to sit inside an airspace section. The general fix is `non_data_row_rules` and a count, not a special case for that table — a skipped row is a reported row.
  - *Tightening the pattern is the test*: replacing the prefix check with the full charset immediately failed six `ASP` keys that had been shipping accented and mixed case, which were then corrected. That failure is the evidence the old pattern checked nothing.
  - *The property the diff depends on*: the key is now stable across cycles for the same published designation, which is what makes `DELETED`/`NEW` mean something. 164 `specialUseArea` and 59 `airspace` keys validate against the tightened patterns in the committed export.
  - *Evidence*: `config/sections.yaml` (`natural_key_normalisation`, `non_data_row_rules`), `config/validation.yaml` (`natural_key_formats`), `pipeline/parse/airspace.py` (`natural_key_normalisation` consumption, `_report_skipped`), `data/export/2026-10-01-current/features.geojson`.

---

## 31. Containerised One-Command Bootstrap

* **Context**: The repository shipped with no container definition at all. A reviewer had to install Python 3.13, `uv`, Node 22, and MongoDB, run `pipeline ingest fetch` (which the publisher's `robots.txt` refuses), and then discover that the crawl was blocked before seeing a single feature. Every step was documented and none of it was executable in one action. Separately, the deployment runs on a VPS with 836 MB of RAM, where an uncapped `mongod` would reserve half of available memory for a cache holding 663 features.
* **Decision**: `compose.yaml` defines three services — `mongo` (capped with `--wiredTigerCacheSizeGB 0.25`, not published to the host), `api` (multi-stage Node image that builds the Vite bundle and serves it from Express), and `pipeline` (a Python image behind the `tools` profile, with `data/raw` and `config/` mounted read-only). `start.sh` waits for Mongo's own healthcheck, runs `pipeline run` once against the committed raw cache, refuses to start the API unless the pipeline exits zero **and** prints `status: promoted`, then polls `/api/health` until `features > 0`.
* **Reasoning**:
  - *Re-parse, not seed from the export*: the committed `data/export/` could be loaded into Mongo in two seconds, but that shows the output and hides the work. Running the real parse against the committed bytes exercises the SHA-256 manifest verification, the declarative validation rules, the diff, and the promotion gate — which is the entire submission. The cost is ~40 s of start-up.
  - *Both cycles are committed, so the parse needs no network*: `data/raw/2026-10-01-current` and `data/raw/2026-10-29` are in git with their manifests. `ingest verify` therefore passes on a fresh clone and the parser replays against exact bytes. This is the manifest chain doing the job it was built for, and it is why the container bootstrap works despite the crawl being refused.
  - *The API must not depend on the pipeline*: an empty database is a valid state — the console renders "Publication unavailable". Coupling them would turn a failed ingest into a dead console and hide the failure behind a 502. The dependency runs one way only, from `start.sh` to both.
  - *The pipeline service is a profile, not a service*: `docker compose up -d` must never trigger an ingest. A one-shot tool behind `profiles: ["tools"]` cannot start by accident, and `restart: "no"` prevents a crash loop from a non-zero exit.
  - *Read-only mounts, not baked images*: `data/raw` is 10 MB of publisher HTML that changes per cycle, and `config/` is tuned per deployment. Mounting them read-only makes "the pipeline never mutates the cache" a kernel guarantee rather than a convention, and keeps the API image free of documents it never reads.
  - *`.env` kept out of the build context*: the optional CARTO key is inlined into the bundle by Vite at build time, so it arrives as a build argument. Compose reads the root `.env` for that substitution, which means the file must never enter the build context; `.dockerignore` excludes it. The consequence, noted in `api/Dockerfile`, is that a basemap key is extractable from the shipped bundle — acceptable for a public tile key, not for a secret.
  - *Memory budget*: Mongo ~150 MB, API ~70 MB, against 836 MB on the target host. The cache cap is the single line that makes the rest fit; the default WiredTiger configuration would claim 256 MB minimum.
  - *Evidence*: `start.sh`, `compose.yaml`, `api/Dockerfile`, `pipeline/Dockerfile`, `.dockerignore`, `.env.example`, `tests/test_container_bootstrap.py` (13 hermetic tests asserting the API does not depend on the pipeline, that Mongo publishes no port, that `start.sh` fails when the pipeline does not promote, and that the CARTO `ARG` precedes the build step). A cold `docker compose down -v && ./start.sh` reproduces 663 features and `{"ok":true,"features":663,...}`.
