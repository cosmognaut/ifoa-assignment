# questions.md — interview prep

For you, the night before and during the demo. Everything here is true of **this repo at this commit**; every claim carries the command that proves it. If you cannot reproduce a line during the interview, say you will check and move on — do not improvise a number.

You are not expected to be an aviation expert. You are expected to know the code you shipped and the reasoning behind it.

**Start the stack before anything else**

```bash
docker start mongo-dev 2>/dev/null || docker run -d --name mongo-dev -p 27017:27017 mongo:latest
cd api && PORT=3000 npm start &          # http://localhost:3000
cd web && npm run dev                    # http://localhost:5173
```

Check it is alive: `curl -s localhost:3000/api/health` → `{"ok":true,"features":652,...}`.

---

## Section 0 — the 60-second demo script

Do it in this order. Say the sentence, click, pause, say the next sentence. Nine steps, ~7 seconds each.

1. **Open `http://localhost:5173`** (Overview is the landing view).
   Say: *"This is the current cycle, `2026-10-01-current`, amendment `AMDT 010/2026`, effective 2026-10-01 — the key is the effective date, not when I downloaded it."*
2. **Point at the count row** (3 aerodrome, 59 airspace, 20 navaid, 51 route, 6 runway, 12 runwayDirection, 165 specialUseArea, 303 waypoint).
   Say: *"652 features in this cycle; these counts are computed from the data, not hardcoded."*
3. **Point at the scope line and the current-run panel.**
   Say: *"Scope is three aerodromes — EBBR, EBCI, EBLG — declared in `config/airports.yaml`. The panel is the publication record: 12 documents ingested, 652 features, 5 issues, 0 errors, 5 warnings, status `promoted`."*
4. **Click `Map`, then point at the layer panel.**
   Say: *"Six drawable layers, and underneath them the 230 features that have no geometry — 59 airspace, 165 special-use areas and 6 operational-info records — listed but not mappable."*
5. **Click any waypoint dot on the map.**
   Say: *"Click selects a feature; the inspector on the right shows the fields and, under them, the provenance — source, section, effective date, source URL and the raw text it was parsed from."*
6. **Click `Register`.**
   Say: *"This is the register. The heading still says airspace register, but it lists every feature with no geometry: 230 rows — 59 airspace, 165 special-use areas and the 6 operational-info records. The filter chips are generated from the data."*
7. **Click `Overview`, then click the `EBBR` card.**
   Say: *"Airport summary for Brussels-National: runway table with declared distances, 11 operational-hours rows, operator contacts, RFFS category. Every value keeps its section and effective date."*
8. **Back to `Map`, select `WPT/GIVOR`** (the waypoint south-east of the others, near 6.39 E / 48.66 N — scroll-zoom twice if the click misses, or, on the dev server only, run `__aeroMap.jumpTo({center:[6.391389,48.658611],zoom:11})` in the console then click the dot).
   Say: *"This waypoint feeds a STAR into Luxembourg. The validator flagged it — `RULE_OPERATIONAL_BBOX`, warning, outside the operational FIR box — and the inspector shows that issue next to the data instead of burying it in a log."*

9. **Stay in the terminal and run** `curl -s 'localhost:3000/api/changes?from=2026-10-01-current&to=2026-10-29'`.
   Say: *"And here is the AIRAC comparison. The next edition takes effect on 2026-10-29, and I have it."* The response is exactly one row: `"featureKey":"SUA/EBR05G"`, `"featureType":"specialUseArea"`, `"status":"NEW"`, `"fieldChanges":[]`.
   Say: *"One change in the whole cycle — a military UAS training zone added to ENR 5.1, 653 features against 652, everything else unchanged."* Then the caution: *"This row is `NEW`, so it carries no field-level changes. The before/after shape — `field`, `previous`, `new`, `section` — is what `MODIFIED` rows produce, and that is exercised by `tests/test_diff_and_quarantine.py`, which mutates a real parse into a second cycle and covers all four buckets."*

Stop talking. Ask what they want to see.

---

## Section 1 — must answer cold

### 1.1 Why is a cycle keyed on the effective date, not the download date or the amendment number?

A cycle id is the AIRAC effective date, with the edition role appended when two editions share a date — this repo's id is `2026-10-01-current`. The download date is an accident of when the crawler ran: re-fetching the same cycle tomorrow must land on the same record, or every re-crawl looks like a brand-new cycle. Amendment ids are not stable identifiers — ADR 2 records that they reset annually, and `discovery.py` documents the case where a preliminary and a final edition share an effective date, which is why the edition role gets appended on collision. The result is that the key is a fact about the publication, not about our run — though the suffix half of that rule is order-dependent, which is why today's discovery returns `2026-10-01` for the current edition while the dataset is keyed `2026-10-01-current` (see 1.8).

`prove it:` `grep -n "cycle_id = " pipeline/ingest/discovery.py` (lines ~305–310), `curl -s localhost:3000/api/publications`, `ls data/raw/`

### 1.2 What does the diff engine actually compare, and why is `contentHash` computed without provenance?

It joins two cycles on `naturalKey` and compares one field per feature: `contentHash`. That hash is a SHA-256 over *normalised values and units only* — `source`, `section`, `effective_date`, `retrieved_at`, `source_url`, `parser_version` and `raw_text` are excluded by construction. If provenance were in the hash, every re-parse would change `retrieved_at` (and often the source URL spelling), so every feature would be `MODIFIED` on every run and the diff would be worthless. Only `MODIFIED` rows expand into per-field `FieldChange` records (`field`, `previous`, `new`, `section`) — that is what makes an amendment reviewable.

`prove it:` `pipeline/utils/hash_utils.py`, `pipeline/diff/engine.py`, `GET /api/changes`

### 1.3 Why does `int` vs `float` matter for that hash, and what was the bug?

Values come out of HTML as text and get converted; the same physical value can arrive as `2987` or `2987.0` depending on the converter path. JSON hashes those differently, so the same runway would flip to `MODIFIED` without the document changing. `normalize_value` coerces exact floats to int, rounds fractional values to 6 decimal places, and collapses whitespace in strings. The proof is one line: hashing `2987` and `2987.0` gives the same digest, while `2987` and `2987.4` differ.

`prove it:` `.venv/bin/python -c "from pipeline.utils.hash_utils import compute_content_hash as h; print(h({'lda':{'value':2987,'unit':'M'}}) == h({'lda':{'value':2987.0,'unit':'M'}}))"` → `True`

### 1.4 What happens when validation fails — walk the quarantine path.

After parsing, `ValidationEngine.evaluate_promotion_gate` checks four declarative thresholds from `config/validation.yaml`: at most `0` errors, at most `100` warnings, at most `0.0 %` disappeared runways, at most `5.0 %` disappeared features. On failure the publication is written with `status: "rejected"`, and — this is the part that matters — the publication record, the features and the issues are all persisted **before** the process exits, so the failure is inspectable. Then `sys.exit(1)`; the previously promoted cycle is never touched. The reasoning is that a query must be able to explain the rejection, and a crash without persistence destroys the evidence.

`prove it:` `pipeline/cli.py` (`cmd_load`, search `status = PublicationStatus.REJECTED`), `tests/test_diff_and_quarantine.py::test_s1_quarantine_persists_rejected_publication_to_mongo`

### 1.5 What are the four change buckets and where do they surface?

`NEW`, `MODIFIED`, `DELETED`, `UNCHANGED`, defined in `ChangeStatus` (`pipeline/models/change.py`). `pipeline diff` prints the counts plus the first 20 change rows; the rows are persisted to the `changes` collection; `GET /api/changes` serves them. Two pairs are stored, and they are not the same kind of thing: the genuine `2026-10-01-current` → `2026-10-29` comparison holds one row (`SUA/EBR05G`, `NEW`, no `fieldChanges`), while an older pair of 321 rows — `2026-10-01` → `2026-10-01-current` — is two ids for the same physical edition, an artefact of the cycle-id defect in 1.8 and not a change record. `/api/changes` requires explicit `from` and `to` parameters, so you choose the pair you show. Beyond the single real change, the NEW/MODIFIED/DELETED classification is covered by `tests/test_diff_and_quarantine.py`, which mutates a real parse into a synthetic second cycle.

`prove it:` `grep -n "ChangeStatus" pipeline/models/change.py`, `curl -s "localhost:3000/api/changes?from=2026-10-01-current&to=2026-10-29"` (the endpoint 400s without `from` and `to`)

### 1.6 Why is there a config plane instead of constants in code, and what breaks if a key is missing?

Publisher URLs, section anchors, table headers, units, validation rules and thresholds all live in `config/*.yaml`; the Python carries none of them. When a key is missing, `pipeline/config.py` **raises** `ConfigError` naming the file and the key — it never substitutes a default, because a silent fallback is a second source of truth that stays quiet when the YAML is renamed. There are tests on both sides of that rule: a missing key raises, and the ingestion modules are scanned to prove the old literals have not come back.

`prove it:` `tests/test_discovery_config.py::test_missing_publisher_config_fails_loudly` and `::test_no_hardcoded_publisher_facts_in_ingest_layer`

### 1.7 What guarantees that a re-parse produces identical output?

Four things. The raw bytes are cached per cycle and every file's SHA-256 is recorded in `manifest.jsonl`, so parsing is replayable against an exact snapshot and `ingest verify` audits the cache before parsing begins. The content hash normalises away everything that is not data (1.2, 1.3). The golden-value tests pin real AIP numbers, so a silent shape change fails the suite rather than the diff. And `pipeline export` is deterministic: re-running it produces byte-identical files.

`prove it:` `.venv/bin/python -m pipeline.cli ingest verify --cycle 2026-10-01-current` → `Verification PASSED`; `sha256sum -c` on `data/export/2026-10-01-current/`

### 1.8 Is your cycle id stable?

Not across runs — that is a known defect in the key scheme. The rule is the AIRAC effective date plus the edition role, but the role is appended only when two editions share a date, so which edition keeps the bare date depends on iteration order over `publisher.cycle_folders` and on which editions exist at that moment. Today `ingest cycles` returns `2026-10-01` for the current edition and `2026-10-01-previous` for the previous one, while the committed snapshot, the database, the export and the API are all keyed `2026-10-01-current`; the degenerate diff it produced "compares" two ids for the same physical edition and calls all 619 features `NEW` (the genuine `2026-10-01-current` → `2026-10-29` comparison is stored beside it). The fix is to make the key order-independent — always `{date}-{role}`, or keep the bare date and carry the role in its own field — and it has not been applied because renaming would orphan the committed cache, the database and `data/export/`, and the raw bytes cannot be re-fetched through the portal to rebuild them.

`prove it:` `uv run python -m pipeline.cli ingest cycles` → `[('current', '2026-10-01'), ('previous', '2026-10-01-previous'), ('preview', '2026-10-29')]`; `.venv/bin/python -c "from pymongo import MongoClient; db=MongoClient('mongodb://localhost:27017/aero_pipeline')['aero_pipeline']; print({(c['fromCycle'], c['toCycle'], c['status']) for c in db.changes.find({}, {'_id': 0, 'fromCycle': 1, 'toCycle': 1, 'status': 1})})"` → `{('2026-10-01', '2026-10-01-current', 'NEW')}`

---

## Section 2 — should be able to reason about

Short answers; the correct answer is a trade-off, not a fact.

**2.1 Why MapLibre and not Leaflet?**
CARTO's raster endpoints return one byte-identical placeholder image for every tile, keyed or unkeyed (three different z/x/y, with and without a key, hash to the same 2,513-byte PNG). The vector style is what the key unlocks, and Leaflet's core renderer draws raster tiles and vector overlays, not vector tiles. So the map library changed — that was a factual discovery, not a taste decision.

**2.2 Why is `geometry: null` a decision rather than a bug?**
Airspace boundaries are published as legal prose — arcs and radii in `SUA/EBR01`, `along the Belgian-German border` in `SUA/EBD29`. A bounding box or convex hull around a prohibited area is not an approximation of the legal boundary — it is a *different* boundary. The attributes and the verbatim prose (`fields.lateralLimitsRaw`) are extracted; the geometry stays null and the gap is documented (ADR 16).

**2.3 Why does `/api/features` return null geometries by default, with `withGeometry` opt-in?**
Because both consumers need different halves of the same set: the map wants the 422 drawable features, the register wants the 230 that have no geometry. Defaulting to "only drawable" would make the airspace register impossible to build from the API.

**2.4 Why one shell with views, not three pages?**
The top bar, the 400 px inspector and the provenance block are identical across Overview, Map and Register. Three pages would mean three copies of the selection state and three copies of the provenance renderer.

**2.5 Why is MapLibre lazy-loaded?**
It is ~290 kB gzip of map runtime that the Overview never touches. `React.lazy` keeps the entry chunk at 77.7 kB gzip and loads the map on first visit to the Map tab.

**2.6 Why are coordinates rendered latitude-first?**
GeoJSON stores `[lon, lat]`; aviation reads lat/lon. Nobody should have to remember the storage order while reading an inspector, so the UI does the flip once and repeats the AIP's own DMS string underneath for cross-checking.

**2.7 Why does the accent mean interaction only?**
`#C8FF00` marks active tab, selected row, focus and hover. Status is neutral, amber or red — there is no green status token. One colour, one meaning: otherwise "lime" means "passing" and "clickable" at the same time and the operator has to decode context on every glance. That is why the `promoted` dot came off the aerodrome cards.

**2.8 Why is the API read-only?**
It is a viewer over a database the pipeline owns. A second writer would need auth, conflict handling and a promotion story; a reader needs none of that, and read-only is the property that makes it safe to expose.

**2.9 Why does the TypeScript API have no build step?**
Node 26 strips types natively, so the `.ts` files are the artifact — no `dist/`, no bundler, no source-versus-shipped drift. `tsc --noEmit` is the gate, with `strict` and `erasableSyntaxOnly`, which also forbids `enum` and `namespace` precisely because those emit runtime code.

**2.10 Why are the fonts self-hosted?**
No font CDN request at runtime, and redistribution becomes a licensing question you can answer by looking at the repo: Clash Grotesk and Clash Display (Fontshare) plus IBM Plex Mono, as woff2 under `web/public/fonts` (7 files). Clash Grotesk has proportional digits and no `tnum` feature, which is why figures carry the `.mono` class.

**2.11 How did you get the preview if the crawler is refused?**
The publisher ships an official offline package — `eAIP_Package.zip`, 1.57 GB, containing `eAIP_Main`, `eAIP_Next` and `eAIP_Previous` — and the crawler's `--from-dir` path exists precisely for it. Rather than download 1.57 GB, the 12 documents the pipeline parses were pulled out of the archive with HTTP range requests: read the central directory from the last 4 MB (3,916 entries), then for each required file fetch its local header plus deflate payload by offset and inflate it. Those 12 files went through the supported offline route, `ingest fetch --cycle 2026-10-29 --from-dir`, producing 12 manifest rows with status `fetched_from_dir` and real hashes (5,354,094 bytes, `verify` PASSED). `robots.txt` governs the HTML portal, not the distribution package, so this is the route the publisher intends.

---

## Section 3 — gaps, and the right way to answer them

For every one of these: admit it in the first sentence, then why, then what closing it costs. Never volunteer a gap; never deny one.

**3.1 "Does it parse ILS / localiser data from AD 2.19?"**
No. AD 2.19 is declared in `config/sections.yaml` with a table locator, but no parser consumes that spec — zero fields in the dataset carry AD 2.19 provenance. It was scope: the five AD sections that were parsed cover the operational summary the console shows. Closing it means a parser reading that declared spec plus golden-value fixtures taken from the real document.

**3.2 "Approach lighting, runway slope from AD 2.14?"**
Not parsed. AD 2.14 has no entry in `config/sections.yaml` at all. Same shape of decision: the console's runway table shows declared distances and surface, so lighting and slope were not on the path. Closing it is a section spec (headers, converters, units) plus a parser and fixtures.

**3.3 "SID/STAR/procedure metadata?"**
Procedures are absent; `operationalInfo` is no longer. `procedureMeta` is a `FeatureType` member with no producer — there is no SID/STAR/approach leg model, only the AIP's `ATS Routes` text on each waypoint. `operationalInfo` is now populated: AD 2.20 and AD 2.21 produce six features (`EBBR/OPS/NOISE`, `EBBR/OPS/LOCAL-REGS` and the same pair for EBCI and EBLG), which is what makes the populated families nine: aerodrome, runway, runwayDirection, navaid, waypoint, route, airspace, specialUseArea, operationalInfo. The waypoint data does carry the AIP's `ATS Routes` text (e.g. `STAR ELLX` for `WPT/GIVOR`), but that is a string on a waypoint, not a procedure model. Closing it needs a leg/segment model and new section specs — a real design task, not an afternoon.

**3.4 "NOTAM and AIP SUP?"**
Modelled, not ingested. `sourceClass` has `SUP` and `NOTAM` values; nothing fetches those feeds, and there is no endpoint for them in `config/crawler.yaml`. They are a different publication surface from the 28-day AIP, which is why they were excluded rather than half-built.

**3.5 "Where are the airspace polygons?"**
They do not exist. All 224 airspace and special-use-area features carry `geometry: null` and the legal prose is preserved verbatim. This is deliberate (ADR 16). Closing it is a geodesic interpreter — arcs, circle sampling, sovereign border shapefiles — plus a fixture set of published boundaries to validate against. The raw text is stored precisely so that work needs no re-fetch.

**3.6 "Has the diff run against two real cycles?"**
Yes — once, against the `2026-10-29` preview, and the answer is small. The preview came from the publisher's official offline package: the HTML portal is refused by `robots.txt`, so 12 documents were pulled out of the 1.57 GB `eAIP_Package.zip` with HTTP range requests and ingested through `ingest fetch --cycle 2026-10-29 --from-dir` (12 rows, `fetched_from_dir`, verify PASSED). `diff --from 2026-10-01-current --to 2026-10-29` then reported 653 features versus 652: **NEW 1, MODIFIED 0, DELETED 0, UNCHANGED 652**, 0.0 % disappeared — the single change is `SUA/EBR05G`, a military UAS training zone added to ENR 5.1. The preview parsed to 0 errors and the same 5 warnings as the current cycle. Caveat worth saying yourself: one new feature is a thin sample, so the NEW/MODIFIED/DELETED classification beyond it rests on `tests/test_diff_and_quarantine.py`, and a preview may still be amended before it takes effect.

**3.7 "Is `effectiveTo` populated?"**
No — all 652 features are `null`. The supersession logic exists (`MongoLoader.promote_publication` back-fills `effectiveTo` on the previously promoted cycle), but it has never run: the 2026-10-29 preview was diffed against the current cycle, never loaded or promoted. Do not claim bitemporal queries work; the schema is bitemporal, the data is not yet.

**3.8 "Lower ATS routes, ENR 3.1?"**
Nothing to parse in this cycle: the published document states `NIL`, and the spec declares no fields. Only upper routes (ENR 3.2, 51 `route` features) exist in the dataset.

**3.9 "So you only parse upper routes?"**
Correct. ENR 3.2 is parsed into 51 route features; ENR 3.1 is `NIL` this cycle. If a future cycle publishes lower routes, the section spec needs fields and the route parser needs a second spec — the dispatch is per section, not hardcoded.

**3.10 "Does the crawler work right now?"**
It runs, and it refuses. `ingest cycles` resolves all three published editions (`2026-10-01`, `2026-10-01-previous`, `2026-10-29`), `ingest fetch` enumerates that cycle's 12 documents — and then every `fetch_url` returns `blocked_by_robots` with 0 bytes, because `https://ops.skeyes.be/robots.txt` is `User-Agent: *` / `Disallow: /` when requested with this project's User-Agent. That is the crawler working correctly: the polite path is the point (ADR 4). The committed snapshot in `data/raw/2026-10-01-current/` was captured before that policy took effect — its manifest still says 12 × `fetched` — and a blocked re-fetch now writes nothing and exits 1 rather than overwriting it. Refreshing needs the publisher's official offline package, `ingest fetch --from-dir` or `--from-zip`; the live portal is not ours to override.

---

## Section 4 — traps

Say the left thing and you lose the room; say the right thing and you look like you own it.

| Do not say | Say instead |
|---|---|
| "The map shows the airspace." | "The map shows point and line features — aerodromes, navaids, waypoints, runway centrelines, routes. Airspace exists as attributes only; the register lists the 230 features that have no geometry." |
| "Here's `/api/changes`" — unfiltered, or with the old `2026-10-01` → `2026-10-01-current` pair. | Show the real pair: `curl 'localhost:3000/api/changes?from=2026-10-01-current&to=2026-10-29'` → one `NEW` row for `SUA/EBR05G`. Never show the 321-row `2026-10-01` → `2026-10-01-current` pair: that is the cycle-id defect in 1.8, not a change record. |
| "There are about 70 tests." | "107: 63 pipeline (pytest), 26 API (`node --test` against the real MongoDB), 18 web (Vitest + jsdom, hermetic)." |
| "I downloaded the 1.57 GB package." | "No — I pulled 12 documents out of it with HTTP range requests (5.35 MB total) and ingested them with `--from-dir`. Downloading the whole archive to parse twelve files would be the wrong trade." |
| "Let me re-fetch it live so you can see." | "A live fetch is refused — `robots.txt` disallows automated access — so there is nothing to watch. The committed snapshot in `data/raw/2026-10-01-current/` is what the API and the console are serving." |
| "The pipeline runs on a schedule." | "It does not. `check-updates` is a command a human runs; there is no cron entry and no CI config — `.github/` does not exist in the repo." |
| "The system is live for users." | "Nothing is live: MongoDB, the Node API and Vite all run locally on 27017 / 3000 / 5173, and nothing starts them for you. The dataset is also committed as `data/export/2026-10-01-current/` so it can be inspected with nothing running at all." |
| "I wrote every line." | "I directed an LLM to write most of the code and I built the verification around it: the SHA-256 manifest chain, the golden-value tests transcribed from the real AIP HTML, the declarative validation rules, and 107 tests I can run in one command. Where my prose and the code disagreed, I fixed the prose — every number in the docs carries the command that produced it." |
| "The frontend has 652 features on the map." | "422 are drawable; 230 have no geometry by design." |
| "The diff shows what changed since last cycle." | "It does — I have a real change record for `2026-10-01-current` → `2026-10-29`: one `NEW` feature, everything else `UNCHANGED`, because that preview is the only second edition available. The full four-bucket shape is covered by `tests/test_diff_and_quarantine.py`." |

For the LLM answer, have one concrete example ready: the coordinate display. The inspector used to print `5.317778, 50.770833` (longitude first, as stored) and a reviewer read it as a latitude. The fix was latitude-first with hemisphere suffixes plus the AIP's raw DMS string, and a test that asserts the old order is *absent* (`web/src/test/fieldRow.test.tsx`). Long-form reasoning: `ARCHITECTURE.md` "Why these choices", plus `DECISION_LOG.md` ADRs 6, 9, 10, 16, 26.

---

## Section 5 — numbers worth knowing

| Fact | Value |
|---|---|
| Features in the cycle | **652** |
| By type | waypoint 303 · specialUseArea 165 · airspace 59 · route 51 · navaid 47 · runwayDirection 12 · runway 6 · operationalInfo 6 · aerodrome 3 |
| Null geometry | **230** (59 airspace + 165 specialUseArea + 6 operationalInfo) |
| Drawable (mappable) | **422** |
| Documents ingested per cycle | **12** (12 lines in `manifest.jsonl`) |
| Cycle id / amendment / effective | `2026-10-01-current` · `AMDT 010/2026` · 2026-10-01 · status `promoted` |
| Validation issues | **5** — 1 `RULE_OPERATIONAL_BBOX` (WPT/GIVOR), 2 `RULE_UNRESOLVED_WAYPOINT` (RTE/L179, RTE/Z310), 2 `RULE_NOISE_AIRCRAFT_TYPES` (EBCI, EBLG: no aircraft-type table); 0 errors |
| Tests | **107** = 63 pipeline + 26 API + 18 web (4 files) |
| Web entry chunk | 252.11 kB, **77.69 kB gzip**; map chunk 1,061.45 kB (290.08 kB gzip) |
| API endpoints | **8** read-only (7 `app.get('/api/…')` routes + 1 regex route for slash-bearing natural keys) |
| ADRs | **27** (`DECISION_LOG.md`) |
| Known limits documented | **9** (`ARCHITECTURE.md` → Known limits) |
| Register rows | 230 (airspace 59 + specialUseArea 165 + operationalInfo 6 — every feature with `geometry: null`) |
| Promotion gate | 0 errors · 100 warnings · 0.0 % disappeared runways · 5.0 % disappeared features |
| Preview cycle `2026-10-29` | 12 documents (5,354,094 bytes) from the offline package · 653 features · `diff` vs current: NEW 1, MODIFIED 0, DELETED 0, UNCHANGED 652 |
| Live crawl | **refused**: `robots.txt` is `User-Agent: *` / `Disallow: /`; 12 documents enumerated, 0 bytes fetched |

Commands that produce the above, if asked to show your work:

```bash
.venv/bin/python -c "import json,collections;d=json.load(open('data/export/2026-10-01-current/features.geojson'));print(len(d['features']));print(collections.Counter(f['properties']['featureType'] for f in d['features']));print('nulls',sum(1 for f in d['features'] if f['geometry'] is None))"
curl -s localhost:3000/api/health
curl -s localhost:3000/api/publications | head -c 300
.venv/bin/python -m pytest tests/ -q | tail -2
cd api && node --test 'test/*.test.ts' | grep -E "^. (tests|pass)"
cd web && npm test 2>&1 | grep -E "Tests "
wc -l data/raw/2026-10-01-current/manifest.jsonl
du -sh data/raw/2026-10-29
curl -s 'localhost:3000/api/changes?from=2026-10-01-current&to=2026-10-29'
grep -c "^## [0-9]" DECISION_LOG.md
grep -c "^- \*\*" ARCHITECTURE.md
```
