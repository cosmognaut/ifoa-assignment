# Engineering self-audit

## 1. What this document is

A self-audit of this pipeline and its two consumers, written after the work rather than during it. Every figure and claim below was re-measured against the repository at commit `f9a8b8d` (`docs: reconcile every figure with the final dataset`) before it was written, and the counts were measured again at `0f8dd3d` after the converter-contract work added six pipeline tests; each one carries the command that produces it — if a number here disagrees with your run, the run is right. Where something is not implemented, section 5 says so and states what closing it would cost.

## 2. A five-minute walkthrough

The console is a local stack: MongoDB on 27017, the API on 3000, Vite on 5173.

```bash
docker start mongo-dev 2>/dev/null || docker run -d --name mongo-dev -p 27017:27017 mongo:latest
cd api && PORT=3000 npm start &
cd web && npm run dev          # http://localhost:5173
```

1. **Overview.** The landing view names the cycle: `2026-10-01-current · AMDT 010/2026 · effective 2026-10-01`.
2. **The count row.** 3 aerodrome, 59 airspace, 47 navaid, 18 operationalInfo, 51 route, 6 runway, 12 runwayDirection, 164 specialUseArea, 303 waypoint — 663 features. These are computed from the data, not configured.
3. **The current-run panel.** The publication record: 12 documents ingested, 663 features, 5 issues, 0 errors, 5 warnings, status `promoted`.
4. **Map.** Six drawable layers, and beneath them the 241 features with no geometry — 59 airspace, 164 special-use areas, 18 operational-info records.
5. **Click a waypoint.** The inspector shows the fields and, under them, the provenance: source, section, effective date, source URL and the raw text the value was parsed from.
6. **Register.** `NON-GEOMETRIC FEATURES`, 241 rows, filter chips generated from the data (`All (241)`, `airspace (59)`, `operationalInfo (18)`, `specialUseArea (164)`). Each row has a type-aware summary: limits for airspace, activity and restriction for special-use areas, field counts and character counts for operational information.
7. **An aerodrome card.** `EBBR` opens the airport summary: runway table with declared distances, 11 operational-hours rows, operator contacts, RFFS category — every value keeping its section and effective date.
8. **`WPT/GIVOR`.** Selecting that waypoint shows the validation block: `RULE_OPERATIONAL_BBOX`, warning, outside the operational FIR bounding box — the check and its finding sit next to the data rather than in a log.
9. **The cross-cycle change.** The one genuine AIRAC comparison on record:

```bash
curl -s 'localhost:3000/api/changes?from=2026-10-01-current&to=2026-10-29'
# [{"fromCycle":"2026-10-01-current","toCycle":"2026-10-29","featureKey":"SUA/EBR05G",
#   "featureType":"specialUseArea","status":"NEW","fieldChanges":[],"source":{"to_publication_id":"2026-10-29"}}]
```

One row: the next edition adds a military UAS training zone to ENR 5.1 and changes nothing else. It is `NEW`, so it carries no field-level changes; the before/after shape (`field`, `previous`, `new`, `section`) is what `MODIFIED` rows produce.

## 3. What the pipeline does, stage by stage, and how to verify each

**Cycle identity.** A cycle id is the AIRAC effective date, with the edition role appended when two editions share a date — this dataset is keyed `2026-10-01-current`. The download date is an accident of when the crawler ran: re-fetching the same cycle later must land on the same record, or every re-crawl looks like a new cycle. Amendment ids are not stable identifiers (they reset annually, and a preliminary and a final edition can share a date).

`Verify:` `grep -n "cycle_id = " pipeline/ingest/discovery.py`; `curl -s localhost:3000/api/publications`; `ls data/raw/`

**The diff engine.** It joins two cycles on `naturalKey` and compares one value per feature: `contentHash`. That hash is SHA-256 over normalised values and units only — `source`, `section`, `effective_date`, `retrieved_at`, `source_url`, `parser_version` and `raw_text` are excluded by construction. If provenance were hashed, every re-parse would change `retrieved_at` and every feature would be `MODIFIED` on every run. Only `MODIFIED` rows expand into per-field changes, which is what makes an amendment reviewable.

`Verify:` `pipeline/utils/hash_utils.py`; `pipeline/diff/engine.py`; `curl -s 'localhost:3000/api/changes?from=2026-10-01-current&to=2026-10-29'`

**The int/float normalisation bug.** Values arrive from HTML as text, so the same physical value can be `2987` or `2987.0` depending on the converter path; JSON hashes those differently and a runway would flip to `MODIFIED` without the document changing. `normalize_value` coerces exact floats to int, rounds fractions to six decimals and collapses whitespace.

`Verify:` `.venv/bin/python -c "from pipeline.utils.hash_utils import compute_content_hash as h; print(h({'lda':{'value':2987,'unit':'M'}}) == h({'lda':{'value':2987.0,'unit':'M'}}))"` → `True`

**The quarantine path.** `ValidationEngine.evaluate_promotion_gate` checks four declarative thresholds: at most 0 errors, at most 100 warnings, at most 0.0 % disappeared runways, at most 5.0 % disappeared features. On failure the publication is written with `status: "rejected"` and — the part that matters — the publication, features and issues are persisted *before* the process exits, so the rejection can be inspected. Then `sys.exit(1)`; the previously promoted cycle is untouched.

`Verify:` `grep -n "PublicationStatus.REJECTED" pipeline/cli.py`; `.venv/bin/python -m pytest tests/test_diff_and_quarantine.py::test_s1_quarantine_persists_rejected_publication_to_mongo -q`

**The four change buckets.** `NEW`, `MODIFIED`, `DELETED`, `UNCHANGED` (`pipeline/models/change.py`). `pipeline diff` prints the counts and the first twenty rows, persists them to `changes`, and `GET /api/changes` serves them. Two pairs are stored and they are different things: `2026-10-01-current` → `2026-10-29` holds one genuine row, while `2026-10-01` → `2026-10-01-current` holds 321 rows that compare two ids of the same physical edition — an artefact of the cycle-id defect in section 5, not a change record.

`Verify:` `grep -n "ChangeStatus" pipeline/models/change.py`; `curl -s 'localhost:3000/api/changes?from=2026-10-01-current&to=2026-10-29'`

**The config plane.** Publisher URLs, section anchors, table headers, units, validation rules and thresholds live in `config/*.yaml`; the Python carries none of them. A missing key raises `ConfigError` naming the file and the key rather than substituting a default, because a silent fallback is a second source of truth that stays quiet when the YAML is renamed. There are tests on both sides: a missing key raises, and the ingestion modules are scanned to prove the old literals have not returned.

`Verify:` `.venv/bin/python -m pytest tests/test_discovery_config.py -q`

**Reproducibility.** Raw bytes are cached per cycle with a SHA-256 per file in `manifest.jsonl`, so parsing replays against an exact snapshot and `ingest verify` audits the cache first. The content hash normalises away everything that is not data. Golden-value tests pin real AIP numbers. `pipeline export` is deterministic — byte-identical on re-run.

`Verify:` `.venv/bin/python -m pipeline.cli ingest verify --cycle 2026-10-01-current` → `Verification PASSED`; `.venv/bin/python -m pipeline.cli export --cycle 2026-10-01-current --out data/export` twice, then `sha256sum -c`

## 4. Design decisions and the trade-offs behind them

**MapLibre over Leaflet.** Decision: replace Leaflet with MapLibre GL on the CARTO Dark Matter *nolabels* vector style. Reason: CARTO's raster endpoints return one byte-identical placeholder image for every tile, with or without an API key — three different z/x/y hashed to the same 2,513-byte PNG — and the key unlocks only the vector style, which Leaflet's renderer cannot draw. Cost accepted: a heavier map runtime, moved off the critical path by lazy-loading.

**No airspace polygons.** Decision: `geometry: null` for all 223 airspace and special-use-area features; attributes plus the verbatim prose in `fields.lateralLimitsRaw`. Reason: a convex hull around a prohibited area is not an approximation of the legal boundary, it is a different boundary. Cost accepted: airspace is readable, not drawable, and the console exposes it as a register.

**`withGeometry` is opt-in.** Decision: `/api/features` returns every matching feature including the 241 with `geometry: null`; `withGeometry=true` narrows to the 422 drawable ones. Reason: the map wants one half of the set and the register the other; defaulting to "only drawable" would make the register impossible to build from the API. Cost accepted: clients must be explicit.

**One shell, not three pages.** Decision: a 72 px top bar and a docked 400 px inspector with views swapped inside it. Reason: the inspector, provenance block and selection state are identical across Overview, Map and Register; three pages would mean three copies. Cost accepted: one more level of state in `App.tsx`.

**The map is lazy-loaded.** Decision: `React.lazy` around `MapView`. Reason: ~290 kB gzip of map runtime the Overview never touches. Cost accepted: a brief loading state on first visit to the Map tab.

**Coordinates are latitude-first.** Decision: render `50.770833° N, 5.317778° E` and repeat the AIP's own DMS string beneath. Reason: GeoJSON stores longitude first, aviation reads latitude first, and a reader should not have to remember the storage order. Cost accepted: one conversion layer in `FieldRow`, plus a test that asserts the old order is *absent*.

**The accent means interaction only.** Decision: `#C8FF00` marks active tab, selected row, focus and hover; status is neutral, amber or red, with no green status token. Reason: one colour, one meaning. Cost accepted: the `promoted` state lost its lime dot and moved into the labelled run panel.

**The API is read-only.** Decision: eight endpoints, no writes, no auth. Reason: it is a viewer over a database the pipeline owns; a second writer would need conflict handling and a promotion story. Cost accepted: no client-side annotations.

**No TypeScript build step.** Decision: `node server.ts` is the production entry point; `tsc --noEmit` is the gate, with `strict` and `erasableSyntaxOnly`. Reason: no `dist/`, no bundler, no drift between the source read and the code that runs. Cost accepted: no enums, namespaces or parameter properties — the compiler refuses anything that would emit runtime code.

**Fonts are self-hosted.** Decision: Clash Grotesk, Clash Display and IBM Plex Mono as woff2 under `web/public/fonts` (7 files). Reason: no font CDN request at runtime, and licensing becomes answerable from the repository. Cost accepted: 7 binary files in the tree, plus figures carrying a `.mono` class because Clash Grotesk has proportional digits and no `tnum` feature.

## 5. What is not implemented, and what closing it would cost

**Runway slope.** Not captured. It is published in AD 2.12's *second* table (`Slope of RWY and SWY`, e.g. `-0.78%` for EBBR 01), and `config/sections.yaml` locates only the first table by `rwy designator` + `true brg`. Closing it: a second table locator plus a per-runway field and fixtures.

**Displaced thresholds.** Not captured. There is no `displacedThreshold` field anywhere in the config; the operational effect currently appears only indirectly, as an `LDA` that exceeds `TORA` (ISSUES.md §2). Closing it: a field, a rule that reads the relevant AIP cell, and a validation decision about which relation is legal.

**SID/STAR/approach procedures.** Absent. `procedureMeta` is a `FeatureType` member with no producer; the waypoints carry the AIP's `ATS Routes` text (`STAR ELLX` on `WPT/GIVOR`) but that is a string, not a procedure model. Closing it: a leg/segment data model and new section specs — a design task, not an afternoon.

**NOTAM and AIP SUP.** Modelled, not ingested. `sourceClass` has `SUP` and `NOTAM` values; nothing fetches those feeds and `config/crawler.yaml` has no endpoint for them. Closing it: a different publication surface with its own update cadence.

**Airspace polygons.** Absent by decision (section 4). Closing it: a geodesic interpreter — arcs, circle sampling, sovereign border shapefiles — plus a fixture set of published boundaries. The raw text is stored uncorrupted so that work needs no re-fetch.

**The full four-bucket classification.** Exercised once, against the `2026-10-29` preview, and the result was `NEW 1 / MODIFIED 0 / DELETED 0 / UNCHANGED 663`. That is a thin sample of one behaviour; the other three buckets are covered only by `tests/test_diff_and_quarantine.py`, which mutates a real parse into a synthetic second cycle. Closing it: diff two published cycles that genuinely differ, which needs a second cycle the publisher is willing to hand over.

**`effectiveTo`.** Unpopulated: all 663 features carry `null`. The supersession logic exists in `MongoLoader.promote_publication`, but it has never run because the preview was diffed, never loaded or promoted. The schema is bitemporal; the data is not yet.

**ENR 3.1 lower ATS routes.** Nothing to parse this cycle — the published document states `NIL` and the spec declares no fields. Only ENR 3.2 upper routes (51 `route` features) exist.

**The cycle-id defect.** `discovery.py` appends the edition role to the ISO key only when two editions share an effective date, and resolves that collision by iteration order over `publisher.cycle_folders`. The key is therefore stable within a run but not across runs whose edition set differs: `ingest cycles` today returns `2026-10-01` for the current edition while the cache, database and export are keyed `2026-10-01-current`, and the 321 stored change rows are a comparison of those two ids. Two possible fixes, both making the key order-independent: always `{date}-{role}`, or keep the bare date and carry the role in a separate field. Not applied: renaming would orphan the committed cache, the loaded database and `data/export/`, and the raw bytes cannot be re-fetched through the portal to rebuild them.

## 6. Accuracy notes

| Commonly assumed | Actually true |
|---|---|
| The map shows the airspace. | The map shows point and line features — aerodromes, navaids, waypoints, runway centrelines, routes. Airspace exists as attributes only; the register lists the 241 features with no geometry. |
| The map shows all 663 features. | 422 are drawable; 241 have no geometry by design. |
| `GET /api/changes` is the change log. | It requires explicit `from` and `to`. The `2026-10-01-current` → `2026-10-29` pair is genuine (one `NEW` row); the 321-row `2026-10-01` → `2026-10-01-current` pair is a cycle-id artefact, not a change record. |
| The test count is a round number. | 152: 101 pipeline (pytest), 26 API (`node --test` against the real MongoDB), 25 web (Vitest + jsdom, hermetic). |
| The pipeline runs on a schedule. | It does not. `check-updates` is a command a human runs; there is no cron entry and no CI configuration — `.github/` does not exist in the repository. |
| Something is deployed and serving users. | Nothing is deployed. MongoDB, the Node API and Vite run locally on 27017 / 3000 / 5173. The dataset is also committed as `data/export/2026-10-01-current/` so it can be inspected with nothing running at all. |
| The crawler can refresh the data on demand. | A live crawl is refused: `https://ops.skeyes.be/robots.txt` returns `User-Agent: *` / `Disallow: /`, and the crawler honours it — 12 documents enumerated, 0 bytes fetched. The committed snapshot predates that policy; a refresh goes through the publisher's official offline package (`ingest fetch --from-dir` / `--from-zip`). |
| The diff has seen many real cycles. | One, against a preview: `NEW 1 / MODIFIED 0 / DELETED 0 / UNCHANGED 663`. |
| Every feature is fully populated. | Two runways publish a blank approach-lighting `Type:` cell, so that field is absent rather than invented; two airports publish no aircraft-type table in AD 2.21, which is why those features carry text only and emit `RULE_NOISE_AIRCRAFT_TYPES`. |

## 7. How the work was built

An AI assistant wrote much of the code; the verification around it is the substance of this repository. That means: a SHA-256 manifest chain over every byte fetched, golden-value tests whose expected numbers were transcribed by hand from the published AIP HTML, validation rules declared in YAML rather than scattered through Python, and 152 automated tests across three suites that I run before believing anything — including this document.

Verification earned its place three times. The first was the content hash: `2987` and `2987.0` produced different digests for the same runway, which would have made every re-parse look like an amendment; `normalize_value` now coerces exact floats and the one-line check in section 3 pins it. The second was `MongoLoader.save_features`, which only ever upserted — after the ILS key scheme changed, 24 old-shaped rows survived beside the 24 new ones and the API reported 71 navaids instead of 47. It now reconciles: it deletes rows for the publications it was given whose `naturalKey` is absent from the incoming set, returns the count, and the load command prints it (`• 663 features (35 stale rows removed)` on the run that cleaned up). The third was the special-use airspace family: 29 natural keys were malformed — a legend table of single-letter keys had been accepted as an airspace, and real designations carried underscores, an en dash and an interior slash through into the key — while `natural_key_formats` was a prefix check (`^SUA/`) that could not have caught any of them. Keys are now normalised from config with the verbatim designation kept in the field, non-data tables are rejected and counted (`[Parse] ENR 5.2: skipped 2 non-data rows`), and the patterns are `^SUA/[A-Z0-9][A-Z0-9 -]*$` / `^ASP/[A-Z0-9][A-Z0-9 -]*$` — which immediately failed six `ASP` keys that had been shipping accented and mixed case.

## 8. Reference figures

| Fact | Value |
|---|---|
| Features in the cycle | **663** |
| By type | waypoint 303 · specialUseArea 164 · airspace 59 · route 51 · navaid 47 · operationalInfo 18 · runwayDirection 12 · runway 6 · aerodrome 3 |
| Null geometry | **241** (59 airspace + 164 specialUseArea + 18 operationalInfo) |
| Drawable | **422** |
| Register rows | 241 — every feature with `geometry: null` |
| Documents ingested per cycle | **12** (`manifest.jsonl` lines) |
| Cycle / amendment / effective / status | `2026-10-01-current` · `AMDT 010/2026` · 2026-10-01 · `promoted` |
| Validation | 0 errors · 5 warnings (1 `RULE_OPERATIONAL_BBOX`, 2 `RULE_UNRESOLVED_WAYPOINT`, 2 `RULE_NOISE_AIRCRAFT_TYPES`) |
| Tests | **152** = 101 pipeline + 26 API + 25 web (4 files) |
| Web entry chunk | 252.81 kB · **78.05 kB gzip**; map chunk 1,061.49 kB · 290.09 kB gzip |
| API endpoints | **8** read-only (7 `app.get('/api/…')` routes + 1 regex route for slash-bearing natural keys) |
| ADRs / documented limits | 31 (`DECISION_LOG.md`) · 8 (`ARCHITECTURE.md` → Known limits) |
| Promotion gate | 0 errors · 100 warnings · 0.0 % disappeared runways · 5.0 % disappeared features |
| Preview `2026-10-29` | 12 documents (5,354,094 bytes) from the offline package · 664 features · diff vs current: NEW 1, MODIFIED 0, DELETED 0, UNCHANGED 663 |
| Live crawl | refused by `robots.txt` (`User-Agent: * / Disallow: /`); 12 documents enumerated, 0 fetched |

```bash
# features, per type, geometry split — from the committed export
.venv/bin/python -c "import json,collections;d=json.load(open('data/export/2026-10-01-current/features.geojson'));f=d['features'];print(len(f));print(collections.Counter(x['properties']['featureType'] for x in f));print('null',sum(1 for x in f if x['geometry'] is None),'drawable',sum(1 for x in f if x['geometry'] is not None))"
# live API
curl -s localhost:3000/api/health
curl -s localhost:3000/api/feature-types
curl -s localhost:3000/api/publications | head -c 200
curl -s 'localhost:3000/api/changes?from=2026-10-01-current&to=2026-10-29'
# suites
.venv/bin/python -m pytest tests/ -q | tail -2
cd api && node --test 'test/*.test.ts' | grep -E "^. (tests|pass)"
cd web && npm test 2>&1 | grep -E "Tests "
# validation
.venv/bin/python -m pipeline.cli validate --cycle 2026-10-01-current | tail -6
# bundle sizes
cd web && npm run build 2>&1 | grep -E "index-.*js|MapView-.*js"
# cache and documents
.venv/bin/python -m pipeline.cli ingest verify --cycle 2026-10-01-current
wc -l data/raw/2026-10-01-current/manifest.jsonl
# documentation counts quoted above
grep -c "^## [0-9]" DECISION_LOG.md
grep -c "^- \*\*" ARCHITECTURE.md
```
