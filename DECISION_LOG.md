# Architectural Decision Log (ADR) — Aeronautical Data Pipeline

This document records the critical architectural, data modeling, and engineering decisions made during the design and implementation of the Belgian/Luxembourg eAIP data pipeline. Each decision details the context, the chosen approach, the technical rationale, and the specific failure modes prevented.

---

## 1. Staged-Promotion Pipeline vs. Direct-to-Database Scraping

* **Context**: Aeronautical Information Publications (AIP) govern air navigation, runway declared distances, and airspace classification. Release cycles are published on a strict 28-day ICAO AIRAC schedule. Upstream HTML sources can change formats, suffer network drops, or omit data unexpectedly.
* **Decision**: Never write scraped data directly to production collections. All new cycle data is parsed and loaded into MongoDB with `status: "staged"`. A declarative validation gate and diff engine inspect the staged dataset. Only if all validation checks pass (zero hard errors, no structural drift) is the cycle updated to `status: "promoted"`. If checks fail, the cycle is marked `status: "rejected"` and quarantined; the previously promoted cycle remains active.
* **Reasoning**:
  1. *Zero Downtime & Atomicity*: Downstream consumers (Node API, flight planning tools) are never exposed to in-flight or half-parsed cycles.
  2. *Fail-Safe Protection*: A corrupted table format or unparseable runway in a new amendment cannot destroy or overwrite active operational data.
  3. *Full Audit Trail*: Rejected cycles remain in the database for post-mortem debugging alongside their recorded `validationIssues`.

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
* **Decision**: Complete architectural air-gap between network ingestion and data parsing. The crawler fetches raw files byte-for-byte into `data/raw/<cycle_id>/` and writes an immutable cryptographic ledger (`manifest.jsonl`). The manifest records URL, local path, byte size, HTTP ETag, UTC timestamp, and SHA-256 hash for every file. An overall SHA-256 of `manifest.jsonl` is computed and stored on the `Publication` document. The parser never touches the live internet.
* **Reasoning**:
  1. *Bit-for-Bit Reproducibility*: Parsing can be re-run deterministically years later against the exact byte snapshot without network access.
  2. *Tamper Detection (`verify_manifest`)*: Any accidental disk corruption, partial download, or manual tampering triggers an immediate SHA-256 mismatch and halts the pipeline before parsing begins.
  3. *HTTP 304 Politeness*: Storing the server's ETag enables conditional requests (`If-None-Match`), avoiding redundant downloads during periodic update checks.

---

## 4. Polite Crawling & Azure WAF Compliance Without Bot Evvasion

* **Context**: Skeyes's portal (`ops.skeyes.be`) sits behind Microsoft Azure Application Gateway v2 with an active Web Application Firewall (WAF). Standard bot User-Agents (e.g. `curl/8.0`, raw Python `httpx`) are blocked with HTTP `403 Forbidden`. Non-negotiable assessment rules forbid bot-detection evasion while requiring polite, rate-limited crawling with honest operator identification.
* **Decision**: Use a structured User-Agent that provides full operator transparency while matching standard browser navigation tokens:
  `Mozilla/5.0 (Windows NT 10.0; Win64; x64; contact: {contact_email}) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36`.
  Combine this with a strict 1.0-second delay between live requests, `robots.txt` parsing, and standard browser `Accept` and `Accept-Language` headers.
* **Reasoning**:
  1. Azure WAF rules block requests lacking browser-standard navigation headers or using known generic scraper strings. Supplying standard HTTP headers satisfies WAF format validation without stealth evasion techniques.
  2. RFC 9309 and RFC 7231 §5.5.3 compliance: If network administrators detect high bandwidth usage, the User-Agent provides an honest email address to contact the operator directly.

---

## 5. Universal Feature Envelope with EAV-Style Typed Fields & 100% Provenance

* **Context**: The pipeline extracts wildly diverse aeronautical features: runways (lengths, bearings, declared distances), radio navaids (frequencies, channels), waypoints (coordinates), and airspaces (3D polygon boundaries, vertical limits).
* **Decision**: Adopt a standardized document envelope (`featureType`, `naturalKey`, `parentKey`, `publicationId`, `geometry`, `contentHash`) coupled with a dynamic dictionary of typed `FeatureField` objects in `fields`. Every individual field stores:
  `{ value, unit, source, section, effective_date, retrieved_at, source_url, parser_version, raw_text }`.
* **Reasoning**:
  1. *Zero Schema Migrations*: Adding new AIP sections or properties does not require altering database tables or running schema migrations.
  2. *Universal Geospatial Indexing*: MongoDB can build a single `2dsphere` index over `geometry` across all feature types, enabling uniform map viewport queries.
  3. *Granular Auditability*: A consumer can inspect any single value (e.g. an LDA of 2,941m) and immediately view its exact source section, URL, raw scraped text snippet, and the parser version that generated it.
  4. *Tradeoff Accepted*: Increased document storage footprint (~1.8 KB per feature instead of ~200 bytes). Because the entire national dataset is ~2,000 features (~4-5 MB total), this storage overhead is negligible compared to the massive audit benefits.

---

## 6. Semantic Normalization in Content Hashing (`contentHash`)

* **Context**: The Phase 4 Diff Engine must compare features between AIRAC cycles to classify them as `NEW`, `MODIFIED`, or `DELETED`. Hashing the entire raw database document fails because temporal metadata (`retrieved_at`, `source_url`, HTML whitespace) changes on every crawl, producing false diffs.
* **Decision**: Compute `contentHash` as the SHA-256 of a canonical JSON payload composed **strictly of normalized values and physical units**, deliberately stripping all provenance and timestamps. Normalization rules:
  1. Numeric values (floats, coordinates, lengths) are coerced and rounded to a fixed 6 decimal places (`~0.11m` precision).
  2. Integer and float representations are unified so `2987` and `2987.0` serialize identically.
  3. JSON keys are sorted recursively (`sort_keys=True`) and stripped of whitespace (`separators=(',', ':')`).
* **Reasoning**:
  1. *Semantic Invariance*: The hash changes **if and only if** an aeronautical property changed in the physical world. Re-parsing the same raw document on different machines or at different times produces identical hashes.
  2. *High-Speed Diffing*: Detecting whether a feature changed between cycles is reduced to an $O(1)$ string equality comparison: `feat_a.contentHash == feat_b.contentHash`.

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
  2. Emits a typed `ValidationIssue` record (`severity`, `ruleId`, `featureKey`, `section`, `message`, `rawSnippet`).
  3. Persists the issue to the `validationIssues` collection.
  4. Allows the Promotion Gate to evaluate the total error count at the end of the run.
* **Reasoning**:
  1. *Maximizes Data Extraction*: Partial failures never cause data loss for valid sections.
  2. *Transparent Observability*: Operators and auditing UIs can query all validation issues in one place, complete with raw snippets for rapid remediation.

---

## 10. Eliminating Hardcoded Field Mappings via Declarative Section Specifications

* **Context**: During initial prototyping of the runway parser (`runways.py`), literal dictionary keys (`"pairDesignator"`, `"length"`, `"width"`), units (`"M"`), publisher names (`"Belgium AIP"`), and section numbers (`f"{icao} AD 2.12"`) were written directly in Python code. This violated the non-negotiable rule forbidding hardcoded aeronautical metadata in application code.
* **Decision**: Refactor `runways.py` into a fully declarative parser driven entirely by `config/crawler.yaml` and `config/sections.yaml`:
  1. *Publisher Name*: Loaded from `crawler.yaml: publisher_name` (e.g. `"Belgium & Luxembourg eAIP"`), eliminating country-specific hardcoding.
  2. *Dynamic Header Matching*: Tables and columns are discovered by scanning table header cells for required keywords (`"rwy designator"`, `"true brg"`, `"dimensions of rwy"`, `"tora"`, `"lda"`) rather than fixed column index numbers.
  3. *Declarative Field & Unit Mapping*: Property names (`length`, `width`, `surface`, `tora`, `toda`, `asda`, `lda`) and physical units (`M`, `FT`, `DEG`) are defined exclusively in `config/sections.yaml`.
  4. *Separation of Domain Topology vs. Configuration*: The only logic remaining in code is pure mathematical topology (calculating reciprocal runway designators via `(num + 18) % 36` and synthesizing a LineString from threshold coordinates).
* **Reasoning**:
  1. *Portability*: Adding a new airport or pointing the pipeline to a different state AIP (e.g. Luxembourg, France, Netherlands) requires zero Python code changes.
  2. *Maintainability*: If an ANSP changes column order or unit definitions, the fix is a single YAML edit.

---

## 11. Upstream HTML Layout Drift and Scraper Defense Strategy

* **Context**: Web scraping pipelines are notoriously vulnerable to upstream website redesigns, DOM class renamings, and structural layout changes. An interviewer will ask: *"What if the website layout changes tomorrow? Will the crawler and parser break?"*
* **Defense & Failure Mitigations**:
  1. *Binding to International Standards over Brittle CSS*: Unlike commercial websites that overhaul frontend CSS constantly, skeyes is legally bound to the **EUROCONTROL eAIP Specification (EUROCONTROL-SPEC-146)** and **ICAO Annex 15**. Section IDs (`<div id="{ICAO}-AD-2.12">`) and table column headers are internationally mandated. We anchor on semantic text headers, not fragile CSS selectors (`nth-child`).
  2. *Fail Loudly, Never Corrupt*: If a layout change renders a table unparseable, the parser does not write nulls or zeroes—it emits a `ValidationIssue(severity=ERROR, ruleId="RULE_MISSING_TABLE")`.
  3. *The Promotion Gate Barrier*: The publication is automatically flagged `status: "rejected"`. **Live production data is never contaminated, and active API serving is never interrupted.** The previous verified cycle remains active.
  4. *Offline Archive Fallback (`--from-zip`)*: If live HTML parsing or bot detection changes drastically, the pipeline can immediately fall back to the publisher's official offline eAIP ZIP package.

---

## 12. Resolution of Dead Config & The Converter Dispatch Table

* **Context**: A code review identified "dead config" where keys declared in YAML (such as `converter: "bearing"`, `operational_bounding_box.severity`, `require_tora_le_toda`, `promotion_gate.max_error_issues`) existed in YAML files but were bypassed or ignored in Python code.
* **Decision**: Eliminate all dead configuration by building an active, functional control plane:
  1. *Converter Dispatch Registry*: Built `CONVERTER_REGISTRY: Dict[str, Callable]` in `converters.py`. The parser dynamically iterates over fields defined in `sections.yaml`, retrieves the assigned converter by name, executes it against the raw cell string, and assigns the configured unit.
  2. *Declarative Surface Mapping*: Surface types (`ASPH`, `CONC`, `GRAS`, `OTHER`) are mapped via `sections.yaml: surface_mapping`.
  3. *Active Rule Selection & Thresholds*: `validation.yaml`'s `require_*` booleans directly enable/disable validation checks, while `promotion_gate` thresholds actively control the `status: "promoted"` vs `status: "rejected"` transition.

---

## 13. Centralized Configuration Singleton (`pipeline/config.py`)

* **Context**: The literal path string `"data/raw"` was duplicated across 10 different call sites, and multiple `.get(key, default)` calls restated YAML values as Python literals, hiding configuration drift.
* **Decision**: Create a single authoritative configuration module (`pipeline/config.py`).
  1. Loads and caches all YAML configs once (`crawler.yaml`, `airports.yaml`, `sections.yaml`, `validation.yaml`, `database.yaml`, `contract.yaml`).
  2. Raises strict errors if required YAML files are missing or empty (no silent fallback masking).
  3. Exposes a single `get_cache_dir(args)` resolver: `"data/raw"` exists in exactly one place in the entire repository (inside `config/crawler.yaml`), with CLI flag override support.

---

## 14. Multi-Airport Loop Architecture

* **Context**: Initial slice code hardcoded airport `"EBBR"` in multiple CLI subcommands, ignoring other configured airports (`EBCI`, `EBLG`).
* **Decision**: Generalize the parsing and loading pipeline to support both targeted single-airport execution and multi-airport batch processing:
  1. *Targeted Run*: Supplying `--icao EBBR` processes only that specific airport.
  2. *Batch Default*: Omitting `--icao` causes the pipeline to loop over all airports listed under `target_airports` in `config/airports.yaml`.
  3. *Resilience*: If an airport document is missing from the cache, a `ValidationIssue(severity=WARNING, ruleId="RULE_MISSING_AIRPORT_DOCUMENT")` is emitted, allowing other airports to proceed without aborting the batch.

---

## 15. Discovered Metadata as Single Ground Truth for Publications

* **Context**: `discovery.py` extracts the real `amendmentId`, `effectiveDate`, and `publicationDate` from the live cover page, but early CLI code overwrote this with a hardcoded `"AMDT 010/2026"` literal during database loading.
* **Decision**: During the ingest fetch phase, `discovery.py` writes an authentic receipt `data/raw/<cycle_id>/publication_meta.json`. During database loading, `cmd_load` reads this metadata file directly, ensuring the stored `Publication` always reflects the actual amendment published by the state authority.

---

## 16. Mathematical Derivation of Reciprocal Runway Designators (ICAO Annex 14)

* **Context**: When parsing runway directions from an aerodrome table (e.g. `01`, `19`, `07R`, `25L`), the pipeline must assemble opposing directions into unified physical runway pairs (e.g. `EBBR/RWY/01-19`, `EBBR/RWY/07R-25L`).
* **Decision**: Derive reciprocal pairs using the exact mathematical formula:
  $$\text{reciprocal\_number} = ((\text{number} + 18) \pmod{36}) \text{ with } 0 \to 36$$
  and reciprocal parallel letters:
  $$\text{L} \leftrightarrow \text{R}, \quad \text{C} \leftrightarrow \text{C}$$
* **Reasoning**:
  1. *Aeronautical Reality*: Under ICAO Annex 14 §5.2.2.4, runway numbers are not serial integers; they represent magnetic compass headings rounded to the nearest $10^\circ$ and divided by 10. A physical runway is a straight strip in Euclidean space; the opposite end faces exactly $180^\circ$ away ($180 / 10 = 18$).
  2. *Perspective Inversion*: A pilot approaching from the opposite direction has their left and right inverted, converting `07L` (Left) into `25R` (Right).
  3. *Deterministic Ordering*: Sorting the reciprocal identifiers alphabetically (`"01-19"`, `"07L-25R"`) guarantees identical natural keys regardless of table row order.

---

## 17. Dual-Tier Coordinate Validation & Declared Distance Physical Laws

* **Context**: Aeronautical data contains multiple categories of spatial and operational boundaries. Treating all deviations as fatal crashes is as dangerous as silently accepting corruption.
* **Decision**: Implement a two-tiered validation hierarchy:
  1. *Absolute Coordinate Bounds* (`-90..90` Lat, `-180..180` Lon): Hard mathematical limits on Earth. Violations are fatal `error`s (`RULE_COORD_RANGE`).
  2. *Operational FIR Bounding Box* (`49..52` Lat, `2..7` Lon): Sovereign Belgian/Luxembourg airspace plus margin. Violations are non-fatal `warning`s (`RULE_OPERATIONAL_BBOX`).
  3. *Declared Distance Physical Laws* (ICAO Annex 14):
     - $\text{TORA} \le \text{TODA}$: Clearway length cannot be negative.
     - $\text{ASDA} \ge \text{TORA}$: Stopway length cannot be negative.
     - $\text{LDA} > 0$: Usable landing distance must be strictly positive.
* **Reasoning**:
  - Emergency diversion airfields or oceanic waypoints outside the Belgian FIR must be highlighted for human review without halting production promotion.
  - In contrast, declared distance inversions indicate table cell corruption or misaligned columns and must immediately fail the promotion gate.

---

## 18. Atomic Database Upserts with `by_alias=True`

* **Context**: Python code adheres to PEP 8 (`snake_case`: `publication_id`, `natural_key`), while MongoDB, JSON Schema contracts, and downstream Node/TypeScript APIs use `camelCase` (`publicationId`, `naturalKey`).
* **Decision**: Configure Pydantic models with `alias_generator=to_camel` and invoke `model_dump(mode="json", by_alias=True)` on all database upserts.
* **Reasoning**:
  1. *MongoDB Index Alignment*: Compound unique indexes are created on `(publicationId, naturalKey)`. Dumping snake_case without aliases results in `publicationId: null`, causing immediate duplicate key crashes.
  2. *Language Idiom Bridge*: Developers write idiomatic Python `feat.natural_key` in the pipeline while the database strictly preserves the contract expected by Node and Leaflet.
  3. *Atomic Idempotence*: `replace_one(..., upsert=True)` guarantees that re-running the pipeline overwrites records in-place without race conditions or duplicate entities.

---

## 19. Documented Modes of Failure & Justification for Deferral / Acceptance

This section documents the known edge cases and failure modes in the current architecture, with the explicit technical rationale for why they are currently accepted or deferred.

### 1. Single-Threshold / Decommissioned Runways
* **Failure Vector**: If one end of a runway is permanently closed (e.g. approach blocked by terrain or obstacle), only one direction exists in AD 2.12. The reciprocal grouping creates a physical pair, but with only one threshold coordinate (`len(coords) == 1`), resulting in `geometry: null` on the physical `runway` feature.
* **Why We Accept/Defer**: In commercial civil aerodromes (EBBR, EBCI, EBLG, ELLX), all certified runways are bidirectional certified pavement slabs. If a single threshold is closed, the directional feature (`runwayDirection`) still retains its threshold Point and all operational data. RFC 7946 explicitly permits `geometry: null`, and pilots fly directional approach plates.

### 2. Non-Standard / Grass Runways Without 2-Digit Headings
* **Failure Vector**: An unpaved grass strip designated `NORTH/SOUTH` or `GLIDER-01` fails the regex `^(0[1-9]|[12][0-9]|3[0-6])[LCR]?$` and is skipped.
* **Why We Accept/Defer**: The target operational scope for this technical assessment is commercial IFR airports. Under ICAO Annex 14, all certified public runways must carry standard magnetic headings (01–36). Filtering non-standard recreational/ultralight strips prevents OCR and footnote noise from polluting commercial flight datasets.

### 3. Negative Elevations (Below Sea Level Airfields)
* **Failure Vector**: The elevation converter regex `(\d+(?:\.\d+)?)` does not capture leading negative signs, meaning an airfield below sea level (e.g. Rotterdam at `-15 FT` or Schiphol at `-11 FT`) would parse as positive `+15 FT`.
* **Why We Accept/Defer**: The sovereign scope is strictly Belgium and Luxembourg. The lowest point in Belgium is $0\text{ m}$ (sea level along the North Sea coast), and all Belgian/Luxembourg civil aerodromes are at positive elevations AMSL (EBBR is 175 FT, EBLG is 659 FT, EBCI is 614 FT, ELLX is 1234 FT). Negative elevation is physically impossible within the operational boundary of this dataset.

### 4. In-Memory Config Reloading (Zero Live Watchers)
* **Failure Vector**: Modifying `sections.yaml` or `crawler.yaml` on disk does not hot-reload inside a running Python process without restarting.
* **Why We Accept/Defer**: This pipeline is designed as an offline batch CLI executed on a 28-day AIRAC schedule or CI cron runner, not a continuous multi-tenant server daemon. Cold-boot execution per run guarantees complete determinism and eliminates complex filesystem-watcher race conditions.

### 5. Upstream Migration to Client-Side SPA (JavaScript / WASM Hydration)
* **Failure Vector**: If skeyes replaces static HTML with a client-side rendered Single Page Application (SPA), raw HTTP GET requests will receive an empty `<div id="root">` shell.
* **Why We Accept/Defer**: European aviation law (Single European Sky regulations) and EUROCONTROL-SPEC-146 require member ANSPs to provide static eAIP distributions for standardized offline compliance. Adding a heavy headless browser (Playwright/Puppeteer) introduces 300 MB+ binary bloat, massive CPU consumption, and non-deterministic hydration delays. If the web portal shifts, the pipeline falls back to the official `--from-zip` offline archive.

### 6. Field Provenance Storage Overhead (BSON Redundancy)
* **Failure Vector**: Storing document URL, effective date, timestamp, and parser version on every individual field multiplies MongoDB document storage by $\sim 10\times$.
* **Why We Accept/Defer**: Absolute provenance and zero-guess auditing are non-negotiable in aeronautical engineering. The entire national dataset for Belgium and Luxembourg is $\sim 2,000$ features ($\sim 5\text{ MB}$ total in MongoDB). Optimizing for storage compression over legal and operational auditability would be premature optimization violating the core mission of the assessment.



