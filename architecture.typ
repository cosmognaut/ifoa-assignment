// architecture.typ
// Compile: typst compile architecture.typ architecture.pdf
//
// Skeleton only. Every TODO is a decision you make, not a placeholder to keep.
// Numbers below were measured against the repo; re-run the command in the margin
// note before you trust them.

#set page(paper: "a4", margin: (x: 2.4cm, y: 2.2cm))
#set text(font: "New Computer Modern", size: 10.5pt)
#set par(justify: true, leading: 0.7em)
#set heading(numbering: "1.")
#show heading: set text(size: 13pt, weight: "regular")
#show heading.where(level: 1): it => { v(1em); it; v(0.4em) }
#set table(stroke: none, inset: (x: 0pt, y: 4pt))
#show table: set text(size: 9.5pt)

= Overview

// TODO 2-3 paragraphs, your words.
//
// Cover: what the eAIP is, what a 28-day AIRAC cycle means operationally, and
// what you set out to build. Say plainly why the output has to be deterministic
// and why a language model cannot be in the value path.
//
// Numbers you may cite (measured):
//   663 features, 422 with geometry, 241 without
//   by type: waypoint 303, specialUseArea 164, airspace 59, route 51,
//            navaid 47, operationalInfo 18, runwayDirection 12, runway 6,
//            aerodrome 3
//   cycle 2026-10-01-current, AMDT 010/2026, effective 2026-10-01, promoted

= Pipeline

// TODO one subsection per stage. Each: what it does, why it exists, what breaks
// without it. Keep them short; the ADRs carry the depth.

== Ingestion and the manifest

// TODO: byte-for-byte fetch, polite rate limit, SHA-256 per document in
// manifest.jsonl, and why verification before parse matters.
//
// Include: 12 documents, and the fact that a live crawl is refused by robots.txt
// so the committed snapshot is the supported route.

== Parsing

// TODO: 19 section specs in config/sections.yaml, declarative table locators,
// and the provenance envelope carried per field (value, unit, source, section,
// effective_date, retrieved_at, source_url, parser_version, raw_text).

== Validation

// TODO: 8 declarative rules, thresholds in config/validation.yaml, and the
// declared-distance relations. Say why these are data rather than code.

== Diffing

// TODO: natural key as identity, contentHash over normalised values and units
// only, and the specific reason provenance is excluded from the hash.

== Promotion gate

// TODO: the thresholds, the quarantine path, and why a rejected cycle is
// persisted rather than discarded.

= Configuration as the control plane

// TODO: the rule that domain facts live in config/*.yaml, the ConfigError on a
// missing key, and what silent defaults had already cost before the rule existed.

= Defects found by verification

// TODO: the three. State symptom, cause, fix, and what it would have caused in
// production. These are the strongest paragraphs in the document.
//
// 1. content hash: 2987 vs 2987.0 hashed differently, every re-parse looked like
//    an amendment
// 2. save_features upserted only: 24 phantom rows, 71 navaids reported instead
//    of 47
// 3. natural_key_formats was the prefix ^SUA/, which cannot fail; 29 malformed
//    keys had shipped

= Interfaces

// TODO: the API (8 read-only endpoints, _id projected away) and the console
// (Overview, Map, Register, Airport). One paragraph each.

= Known limits

// TODO: pick the ones worth the reader's attention and price each one. Do not
// list all eight if four make the point.
//
// Candidates: airspace geometry null by design; cycle id order-dependent;
// runway slope and displaced thresholds absent; procedures modelled but not
// ingested; NOTAM and SUP are schema values not feeds; effectiveTo unpopulated.

= Reproducing

// TODO: the one command, what it enforces, and the fact that the dataset is
// also committed so it can be inspected with nothing running.

// Measure before citing:
//   .venv/bin/python -m pytest tests/ -q | tail -1
//   cd api && node --test 'test/*.test.ts'
//   cd web && npm test
