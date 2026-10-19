# Belgian & Luxembourg eAIP Ingestion Pipeline & Audit Engine

## Overview
This system ingests, validates, and diffs the official Belgium and Luxembourg Electronic Aeronautical Information Publication (eAIP) published by skeyes across consecutive 28-day AIRAC cycles. Built for flight dispatchers and operations controllers, it transforms unstructured HTML tables into structured, cryptographically fingerprinted aeronautical features with complete legal provenance. When a new cycle is published, it isolates changes down to individual field modifications (e.g. reduced runway LDA or displaced thresholds) and halts promotion if safety-critical invariants or disappearance thresholds are violated.

## Prerequisites
- **Python 3.13+**
- **uv** (Fast Python package manager)
- **MongoDB** running locally or via Docker:
  ```bash
  docker run -d --name mongo-dev -p 27017:27017 mongo:latest
  ```

## End-to-End Execution Guide
From a clean clone, run the following commands in sequence:

```bash
# 1. Check for publisher updates (probes live skeyes portal vs local raw cache)
uv run python -m pipeline.cli check-updates

# 2. Fetch a cycle (downloads raw HTML byte-for-byte with polite 1 req/s rate limit)
uv run python -m pipeline.cli ingest fetch --cycle 2026-10-01-current

# 3. Verify cryptographic integrity of cached files against manifest.jsonl SHA-256
uv run python -m pipeline.cli ingest verify --cycle 2026-10-01-current

# 4. Parse raw HTML into structured aeronautical features
uv run python -m pipeline.cli parse --cycle 2026-10-01-current

# 5. Validate extracted features against declarative safety and physical invariants
uv run python -m pipeline.cli validate --cycle 2026-10-01-current

# 6. Diff consecutive cycles by naturalKey + semantic contentHash
uv run python -m pipeline.cli diff --from 2026-10-01-current --to 2026-10-01-current

# 7. Load features, issues, and publication into MongoDB (evaluating promotion gate)
uv run python -m pipeline.cli load --cycle 2026-10-01-current --promote

# 8. Single-pass end-to-end command (verifies, parses once, validates, diffs, promotes)
uv run python -m pipeline.cli run --cycle 2026-10-01-current
```

## Configuration Architecture (`config/*.yaml`)

All domain rules, regex patterns, table locators, and endpoints live in `config/`. The Python codebase contains zero hardcoded publisher URLs, section filenames, or column indices.

| Configuration File | Control Plane Responsibility |
| :--- | :--- |
| `config/crawler.yaml` | Publisher portal base URL, cycle folder discovery paths, user-agent template, contact email, rate limit (1 req/s), timeout, retry backoff, and robots.txt policy. |
| `config/airports.yaml` | List of target aerodromes (`target_airports`: EBBR, EBCI, EBLG) and default scope. |
| `config/sections.yaml` | Single source of truth for ingested sections (GEN, ENR, AD), table header matching patterns, converter regexes, physical units, and surface mappings. |
| `config/validation.yaml` | ICAO regex, runway designator regex, absolute coordinate bounds, operational FIR bounding box, declared distance boolean rules (`require_*`), and promotion gate thresholds. |
| `config/database.yaml` | MongoDB connection URI, database name, and collection name mapping. |

## Provenance Traceability
Every extracted value is wrapped in a `FeatureField` receipt block that preserves complete auditability back to the legal source:

```json
{
  "value": "No TKOF before PSN H",
  "unit": null,
  "source": "Belgium & Luxembourg eAIP",
  "section": "EBBR AD 2.13",
  "effective_date": "2026-10-01",
  "retrieved_at": "2026-10-19T15:37:36.523447+00:00",
  "source_url": "https://ops.skeyes.be/html/belgocontrol_static/eaip/eAIP_Main/html/eAIP/EB-AD-2.EBBR-en-GB.html",
  "parser_version": "1.0",
  "raw_text": "No TKOF before PSN H"
}
```
- **`value`**: Typed scalar parsed from the cell.
- **`source`**: Official state publishing authority.
- **`section`**: Standardized ICAO chapter and section label.
- **`effective_date`**: Legal AIRAC effective date.
- **`retrieved_at`**: Exact UTC timestamp when network packet arrived.
- **`raw_text`**: Unaltered, verbatim snippet from the HTML cell for discrepancy verification.

## Read-Only JSON API (`api/`)

A small Node service exposes the loaded cycle as JSON so a map or any other client can consume it. It is strictly read-only and adds no writes.

```bash
cd api && npm ci        # reproducible install from package-lock.json
npm test                # 23 integration tests against the local database
npm start               # http://localhost:3000  (PORT / MONGO_URI env override)
```

Configuration is read from `config/database.yaml` and `config/validation.yaml`, not duplicated in JS.

| Endpoint | Returns |
|---|---|
| `GET /api/health` | `{ok, features, collections}` |
| `GET /api/publications` | Publications, newest effective date first |
| `GET /api/feature-types` | `[{featureType, count}]` — lets a client build layers from data |
| `GET /api/features` | GeoJSON `FeatureCollection`; filters: `publicationId`, `featureType`, `bbox`, `withGeometry=true` |
| `GET /api/features/:naturalKey` | One feature, e.g. `EBBR/RWY/25L` |
| `GET /api/airports/:icao` | `{aerodrome, runwayDirections, runways}` |
| `GET /api/changes?from=&to=` | Field-level diff rows between two cycles |
| `GET /api/issues` | Validation issues; filters: `publicationId`, `featureKey`, `severity` |

Of 619 features, 395 carry geometry and 224 (`airspace`, `specialUseArea`) have `geometry: null` by design. `/api/features` therefore returns **all** matching features by default and leaves `geometry` null; map clients pass `withGeometry=true` to receive only the 395 drawable ones. Provenance keys inside `fields` are passed through unmodified (snake_case preserved, `999/UNL` sentinel untouched).

## Web console (`web/`)

`web/` is the reader for this dataset: React 19, TypeScript and Vite, talking only to the read-only API above. Nothing in the browser writes to the database.

- **Shell**: a 72px top bar, a docked 400px inspector, and three tabs — Overview, Map, Register. The Airport view is not in the tab strip; it opens when an aerodrome card on the Overview is clicked.
- **Map**: MapLibre GL (it replaced Leaflet). The module is imported lazily, so it sits in its own chunk and only loads when the Map tab is opened.
- **Type**: fonts are self-hosted woff2 files under `web/public/fonts` — Clash Grotesk, Clash Display, IBM Plex Mono — declared in `web/src/styles/tokens.css`. No font CDN is contacted at runtime.

Prerequisites: Node 20.19+ (Vite 7's floor) and the API service above running on `localhost:3000`, which itself needs MongoDB with a cycle loaded. If the API is unreachable the console still mounts, but every view resolves to its loading, empty or error state instead of data.

```bash
cd web
npm ci
npm run dev        # http://localhost:5173, /api proxied to :3000
npm run build      # tsc --noEmit && vite build
npm run preview
```

`npm test` runs the unit suite (Vitest, jsdom and Testing Library) and needs no running services — no API, no MongoDB, no network.

The basemap is the CARTO Dark Matter vector style. The CARTO API key is optional: `VITE_CARTO_API_KEY` is read from `web/.env.local` (copy `web/.env.example`) and appended to the style URL as `?api_key=…`; without it the app requests the plain style URL, which also loads. `web/.env.local` is gitignored — never commit a key.

## Known Limitations
1. **Section Coverage Scope**: Currently parses AD 2.2, AD 2.3, AD 2.6, AD 2.12, AD 2.13 for configured aerodromes (EBBR, EBCI, EBLG), ENR 4.1 (Radio Navigation Aids), ENR 4.4 (Significant Points / Waypoints), ENR 3.2 (Upper ATS Routes), ENR 2.1 (Airspace), and ENR 5.1/5.2 (Special-Use Airspace). ENR 3.1 publishes zero route tables in Belgian airspace (explicitly published by skeyes as `NIL`). GEN 0.2 and GEN 2.5 are downloaded during discovery to verify amendment schedules but are not parsed into domain feature entities. AD 2.19 (Airport ILS / localizer tables) is indexed in discovery but not yet converted to features; aerodrome navigation relies on the en-route radio aid dataset.
2. **Airspace Geometry**: Airspace boundaries and special-use areas are extracted as attributes and verbatim legal prose (`lateralLimitsRaw`), but geometry is strictly `null`. Complex geodesic arcs, circle radii, and national borders are not synthesized into polygon approximations.
3. **NOTAM and AIP Supplements (SUP)**: `source_class` values for `SUP` and `NOTAM` are modelled in schema enums but stubbed; live feeds are not ingested.
4. **Bitemporality**: `effective_to` is currently null for all ingested features. Multi-cycle supersession across historical cycles is not executed in single-cycle runs, and point-in-time ('as of date') historical queries are not supported.
