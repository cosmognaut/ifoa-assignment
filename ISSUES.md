# AIP Data Quality Issues, Ambiguities & Anomalies Log

This document tracks real-world data-quality problems, semantic ambiguities, and structural anomalies encountered while parsing the official Belgium & Luxembourg electronic Aeronautical Information Publication (eAIP) published by skeyes (`ops.skeyes.be`).

---

## 1. Operational Restrictions Buried in Free-Text Remarks (`RMK`)
* **Location**: `EBBR AD 2.13` (Declared Distances), Table 0, Runway `07R`.
* **Raw Value**: `RMK: "No TKOF before PSN H"`.
* **Aeronautical Impact**: Runway 07R publishes a physical `TORA` of $2,893\text{ m}$. However, the free-text remark legally forbids aircraft from initiating takeoff before taxiway intersection H (Position H). Taking off from the physical threshold is prohibited for noise abatement and obstacle clearance. An automated system that extracts only numeric TORA/TODA floats without preserving and surfacing the `RMK` field would miscalculate available takeoff performance, creating runway excursion and obstacle infringement hazards.
* **Pipeline Mitigation**: The parser captures `rmk` as a first-class `FeatureField` with full provenance, ensuring consumer flight dispatchers and map UIs display the operational limitation alongside numerical declared distances.

---

## 2. Displaced Thresholds and "Inverted" Distance Relations (`LDA > TORA`)
* **Location**: `EBBR AD 2.13`, Runway `07R`.
* **Values**: `TORA: 2893 M`, `TODA: 2893 M`, `ASDA: 2893 M`, `LDA: 3088 M`.
* **Aeronautical Ambiguity**: To an engineer unfamiliar with aerodrome design, `LDA (3,088 m) > TORA (2,893 m)` appears to be an impossible data contradiction (landing distance exceeding takeoff run). In reality, Runway 07R has a displaced takeoff run start line to protect obstacle clearance over nearby roadways, while the full pavement length is available for landing rollouts from the opposite threshold.
* **Pipeline Mitigation**: Validation rules must **never** enforce a naive `LDA <= TORA` constraint. The pipeline only asserts fundamental physical invariants:
  - $\text{TORA} \le \text{TODA}$ (clearway cannot be negative)
  - $\text{ASDA} \ge \text{TORA}$ (stopway cannot be negative)
  - $\text{LDA} > 0$ (usable landing distance must be strictly positive)

---

## 3. Ambiguous Semantics of `NIL` vs. Blank Cells
* **Location**: `AD 2.12` Table 1 (SWY / CWY dimensions) vs. `AD 2.13` Table 0 (Declared Distances).
* **Observation**: In EUROCONTROL eAIP tables, `NIL` is used interchangeably to mean:
  1. *Zero Quantity*: In Stopway (SWY) or Clearway (CWY) dimensions, `NIL` means $0\text{ m}$ available.
  2. *Not Applicable*: In Arresting Gear or OFZ obstacles, `NIL` means the facility does not exist.
  3. *No Remarks*: In Remarks columns, `NIL` means no operational footnote applies.
* **Pipeline Mitigation**: Numeric converters (`convert_distance`) treat `"NIL"` as `None` (or 0 where mathematically appropriate) while preserving `"NIL"` verbatim in `raw_text` for provenance verification.

---

## 4. Multi-Row Table Splitting via `rowspan` without Secondary Row Identifiers
* **Location**: `EBBR AD 2.12`, Table 0 (Runway Physical Characteristics).
* **Observation**: Columns 1 through 6 for a single runway direction (e.g. Runway `01`) are split across 3 consecutive HTML `<tr>` elements:
  - `<tr> 1`: Designator (`01`), Bearing (`014.43°`), Dimensions (`2 987 x 50`), Strength (`ASPH`), Threshold Coordinates (`505314.39N 0042929.68E`), Elevation (`174.8 FT`).
  - `<tr> 2`: Runway End Coordinates (`505446.54N 0043007.27E`).
  - `<tr> 3`: Threshold Geoid Undulation (`149.2 FT`).
* **Vulnerability**: The second and third rows do **not** repeat the runway designator (`01`). They rely entirely on HTML table `rowspan` positioning. If an automated upstream transformation flattens or strips `rowspan`, the secondary coordinates become unanchored orphan cells.
* **Pipeline Mitigation**: The parser anchors strictly on rows possessing $\ge 4$ populated cells matching the compiled `runway_designator_format` regex, guaranteeing that only primary threshold rows are bound to direction features.

---

## 5. Cross-Border Sovereign FIR En-Route Waypoint Anomalies
* **Location**: `ENR 4.4` (Significant Points), Waypoint `WPT/GIVOR`.
* **Coordinates**: Latitude $48.658611^\circ\text{ N}$, Longitude $6.391389^\circ\text{ E}$.
* **Observation**: GIVOR is published in the official Belgian eAIP as an ATS reporting point, but geographically sits in the French/Luxembourg border sector slightly south of the Brussels FIR envelope ($48.75^\circ\text{ N}$ operational margin).
* **Pipeline Mitigation**: Bounding-box checks are strictly assigned `severity: "warning"` rather than fatal `error`. A hard error would cause the promotion gate to falsely reject the entire Belgian national release simply because an official cross-border transition point sits 6 miles across the border.

---

## 6. Unicode Whitespace Infiltration in Numeric Quantities
* **Location**: `EBLG AD 2.12` and `EBBR AD 2.12` (Dimensions of RWY).
* **Raw Snippet**: `"3\u2009690 x 45"` (Liège Runway 04R/22L) and `"2\u2009987 x 50"` (Brussels Runway 01/19).
* **Observation**: Numbers are formatted with Unicode Thin Space (`\u2009`) or Non-Breaking Space (`\xa0`) as thousands separators. A standard ASCII whitespace split or raw `float("3 690")` throws an immediate `ValueError`.
* **Pipeline Mitigation**: The converter regexes in `config/sections.yaml` and the `normalize_value` engine explicitly strip all non-standard Unicode spacing `[\s\u2009\xa0]+` prior to numeric parsing and hashing.

---

## 7. Cross-Border ATS Route Waypoints Missing from National Index (`ENR 3.2` vs `ENR 4.4`)
* **Location**: `EB-ENR-3.2-en-GB.html`, Routes `L179` and `Z310`.
* **Points**: `ROMIN` (Route L179), `BATAK` (Route Z310).
* **Observation**: In route table `L179`, the final segment terminates at point `ROMIN` (coordinates `511257N 0053548E`). Similarly, `Z310` references `BATAK`. However, neither `ROMIN` nor `BATAK` are published in Belgium's national Significant Points table (`ENR 4.4`). They are cross-border transition points legally published in the Netherlands (EHAM) and German (EDWW) national AIPs.
* **Pipeline Mitigation**: In `pipeline/parse/routes.py`, significant points are resolved against the cycle's known waypoints (`WPT/*`) and navaids (`NAV/*`). When a point cannot be resolved against the domestic dataset, the pipeline records the natural key in `waypointSequence` and emits a typed `RULE_UNRESOLVED_WAYPOINT` issue. This allows flight planning engines to detect foreign border transitions without inventing synthetic coordinates.

