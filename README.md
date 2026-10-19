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

## Known Limitations
1. **Section Coverage Scope**: Currently parses AD 2.2, AD 2.3, AD 2.6, AD 2.12, AD 2.13 for configured aerodromes (EBBR, EBCI, EBLG), ENR 4.1 (Radio Navigation Aids), ENR 4.4 (Significant Points / Waypoints), ENR 3.2 (Upper ATS Routes), ENR 2.1 (Airspace), ENR 5.1/5.2 (Special-Use Airspace), and GEN 0.2 / 2.5 metadata. ENR 3.1 (Lower ATS routes) publishes zero route tables in Belgian airspace (explicitly published by skeyes as `NIL`). AD 2.19 (Airport ILS / localizer / glideslope tables) is indexed in discovery but not yet converted to features; aerodrome navigation relies on the en-route radio aid dataset.
2. **Airspace Geometry**: Airspace boundaries and special-use areas are extracted as attributes and verbatim legal prose (`lateralLimitsRaw`), but geometry is strictly `null`. Complex geodesic arcs, circle radii, and national borders are not synthesized into polygon approximations.
3. **NOTAM and AIP Supplements (SUP)**: `source_class` values for `SUP` and `NOTAM` are modelled in schema enums but stubbed; live feeds are not ingested.
4. **Point-in-Time Historical Queries**: `effective_to` is populated during promotion supersession, but retroactive "as of date" time-travel queries require dedicated publication versioning not yet exposed on the CLI.
