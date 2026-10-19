# Architecture

A two-page brief for the Belgian/Luxembourg eAIP ingestion pipeline and its two consumers. Long-form appendices: [`DECISION_LOG.md`](DECISION_LOG.md) (27 decisions, context and rejected alternatives) and [`ISSUES.md`](ISSUES.md) (open defects).

## What the system does

Every 28 days the Belgian AIP publisher (skeyes) reissues its aeronautical publication as HTML documents. This system fetches those bytes, extracts the facts an operations controller needs — airport data, runway declared distances, navigation aids, waypoints, upper routes, airspace — and attaches to every value the section, effective date and verbatim source text it came from. A controller can open `EBBR AD 2.13`, see `LDA 3210 M`, and check the paragraph the publisher printed. When the next cycle lands, the system reports what changed at field level and refuses to publish a cycle that fails its safety checks.

## The pipeline

Eight stages, each a module; the Python stages are individually runnable from `pipeline/cli.py`:

1. **Discover** — `pipeline/ingest/discovery.py` probes the portal for previous, current and preview editions, identifying each by AIRAC effective date.
2. **Acquire** — `pipeline/ingest/crawler.py` and `manifest.py` fetch documents byte-for-byte at one request per second and record URL, size, ETag and SHA-256 per file in an immutable `manifest.jsonl`.
3. **Parse** — `pipeline/parse/*` walks declarative table locators from `config/sections.yaml`, one parser per section family.
4. **Normalise** — `pipeline/parse/converters.py` maps raw cell text to typed values and units; `pipeline/utils/hash_utils.py` hashes normalised values and units only.
5. **Validate** — `pipeline/validate/rules.py` applies `config/validation.yaml` and emits `validationIssues` rather than exceptions.
6. **Diff** — `pipeline/diff/engine.py` joins two cycles on natural key and classifies NEW / MODIFIED / DELETED / UNCHANGED with field-level change records.
7. **Promote or quarantine** — `pipeline/load/load.py` stages the cycle, evaluates the promotion gate, then promotes it or persists it as `rejected` with its issues attached.
8. **Serve and export** — `api/server.ts` serves the loaded cycle read-only; `pipeline export` writes the same data to `data/export/<cycle>/` for database-free review.

## The data model

Everything — aerodrome, runway, waypoint, upper route, restricted area — is one `Feature` with a common envelope (`pipeline/models/feature.py`): `naturalKey`, `parentKey`, `featureType`, `publicationId`, `effectiveFrom`, `effectiveTo`, `sourceClass`, `temporality`, `geometry`, `contentHash`, `fields`.

**Natural keys** encode the hierarchy: `EBBR` → `EBBR/RWY/01-19` (a physical runway) → `EBBR/RWY/01` (a runway direction, child of the pair). En-route objects use their domain identity: `NAV/…`, `WPT/GIVOR`, `RTE/L179`, `ASP/…`, `SUA/…`. Their formats are asserted by `natural_key_formats` in `config/validation.yaml`, so a malformed key is a validation issue, not a database surprise.

**Provenance** is per field, not per document. Each `fields` entry is a receipt: `value`, `unit`, and seven metadata keys — `source`, `section`, `effective_date`, `retrieved_at`, `source_url`, `parser_version`, `raw_text`. `raw_text` is the verbatim cell content, the thing that makes a disputed value resolvable years later.

**`fields` is an EAV-style map**, not a fixed schema. Across the eight populated families the attribute sets barely overlap: runways carry TORA/TODA/ASDA/LDA, aerodromes carry hours and RFFS category, airspace a controlling unit and a class. A column-per-attribute table would need a migration for every new section parsed; a map keyed by attribute name stays stable as the specs grow, and it is the shape the API and inspector consume.

## AIRAC lifecycle

A cycle is identified by its effective date (`2026-10-01`), never by month or amendment number — October 2026 contains two AIRAC dates, and amendment numbers reset annually. The crawler is configured for three simultaneous editions (current, previous, preview), so the previous cycle is already on disk when the new one lands: that is what makes a real diff possible.

Nothing is published directly. A cycle is parsed and staged, the validation engine and promotion gate run, and only then does status move to `promoted`. A failing cycle is quarantined — status `rejected`, with its publication, features and issues persisted for inspection — while the promoted cycle stays live. The gate is declarative: `0` errors, `100` warnings, `0.0 %` disappeared runways, `5.0 %` disappeared features.

The diff is the product, not a byproduct: an amendment should arrive as a reviewable change record (`lda: 2941 → 2850`, `section: EBBR AD 2.13`), not a wholesale replacement the reviewer must re-read.

## The deliberate gap: airspace geometry

Airspace boundaries are published as legal prose. `SUA/EBR01`, for instance, reads *"505311N 0042013E - an arc of circle, 0.8 NM radius, centred on 505311N 0042130E and traced clockwise to 505316N 0042247E - an arc of circle, 2.7 NM radius …"*. The source HTML contains no polygon, no GIS shape, no vertex list.

The decision (ADR 16) is to extract the attributes, keep the prose verbatim in `fields.lateralLimitsRaw`, and set `geometry: null` for all 224 airspace and special-use-area features. This is a scope decision, not an unfinished parser: a convex hull around a prohibited firing range is not an approximation of the legal boundary, it is a different boundary. Closing the gap means writing a geodesic interpreter (pyproj arcs, circle sampling, border shapefiles) plus fixtures of published boundaries; the raw text is stored uncorrupted so that work needs no re-fetch.

## Interfaces

**API** (`api/`): eight read-only JSON endpoints — health, publications, feature-types, features (filters `publicationId`, `featureType`, `bbox`, `withGeometry`), feature detail, airports, changes, issues. Strictly a reader: no writes, `_id` projected away everywhere, connection and collection names from `config/database.yaml`.

**Console** (`web/`): one shell — 72 px top bar, docked 400 px inspector — with Overview, Map, Register and an Airport summary view. Selecting a feature anywhere populates the inspector with its fields, provenance and attached validation issues.

## Known limits

- **AD 2.19 (ILS / localiser) is declared in `config/sections.yaml` but not parsed** — no parser consumes that spec; zero fields carry AD 2.19 provenance.
- **AD 2.14 (approach lighting, runway slope) is not parsed** — no entry in the section specs.
- **ENR 3.1 (lower ATS routes) publishes no tables this cycle** — the document states `NIL`; the spec declares no fields.
- **Procedures metadata is absent** — `procedureMeta` and `operationalInfo` are enum members with no producer; the eight families present are aerodrome, runway, runwayDirection, navaid, waypoint, route, airspace, specialUseArea.
- **NOTAM and AIP SUP are modelled but not ingested** — `sourceClass` has `SUP` and `NOTAM`; no such feed is fetched.
- **`effectiveTo` is unpopulated** — all 619 features carry `null`; supersession logic exists in `MongoLoader.promote_publication` but has never had a second real cycle.
- **The diff has not run across two real cycles** — only `2026-10-01-current` is present; the engine is established by unit tests, not production history.

## Why these choices

Three rules produced most of the design. **The config plane is the only source of domain facts**: publisher URLs, table locators, units, thresholds and bounding boxes live in `config/*.yaml`, and `pipeline/config.py` raises rather than substituting a default, because a silent fallback is a second source of truth. **Provenance outranks convenience**: every value carries its section, date and raw text, which is why the API passes `fields` through untouched and the UI always shows the source string beside the parsed number. **Refuse to synthesise**: no polygons for prose boundaries, no invented values, no status colours implying a check that did not run — an empty field stays empty and a gap is documented.
