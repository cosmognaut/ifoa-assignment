"""
Declarative parser for Radio Navigation Aids:
- ENR 4.1 Radio Navigation Aids - En-Route (VOR, DME, TACAN, NDB)
- AD 2.19 Radio Navigation and Landing Aids (ILS LOC/GP/DME, DVOR/DME, VOR, NDB)
"""
import re
from typing import List, Tuple, Dict, Any, Optional
from bs4 import BeautifulSoup

from pipeline.models.feature import (
    Feature, FeatureType, SourceClass, Temporality,
    FeatureField, GeoJSONGeometry
)
from pipeline.models.validation_issue import ValidationIssue, IssueSeverity
from pipeline.utils.hash_utils import compute_content_hash
from pipeline.parse.converters import CONVERTER_REGISTRY
from pipeline.config import load_crawler_config, load_sections_config, require, require_str

PARSER_VERSION = "1.0"

class NavaidParser:
    def __init__(self, cycle_id: str, effective_date: str):
        self.cycle_id = cycle_id
        self.effective_date = effective_date

        crawler_cfg = load_crawler_config()
        self.publisher_name = require_str(require(crawler_cfg, "publisher", "crawler.yaml"), "name", "crawler.yaml publisher")

        sections_cfg = load_sections_config()
        self.type_tokens = sections_cfg.get("navaid_type_tokens", {})
        sections_list = sections_cfg.get("sections", [])
        self.spec_enr = next((s for s in sections_list if s["id"] == "ENR-4.1"), {})
        self.spec_ad19 = next((s for s in sections_list if s["id"] == "AD-2.19"), {})
        self.dme_unit = self.spec_enr.get("fields", {}).get("dmeElevation", {}).get("unit")
        self.dme_unit_ad19 = require(
            require(self.spec_ad19, "fields", "sections.yaml AD-2.19"),
            "dmeElevation",
            "sections.yaml AD-2.19 fields",
        )["unit"]

        # ILS sub-rows are components of the caption above them; the caption is the only
        # place the category is published, and only the LOC row carries the ILS identifier.
        self.ils_component_tokens: Tuple[str, ...] = tuple(
            require(sections_cfg, "ils_component_tokens", "sections.yaml")
        )
        self.re_ils_caption = re.compile(
            require_str(
                require(sections_cfg, "converters_regex", "sections.yaml"),
                "ils_caption",
                "sections.yaml converters_regex",
            )
        )
        # A designator is an ICAO aid identifier: 2-5 upper-case alphanumerics.
        self.re_designator = re.compile(r"^[A-Z0-9]{2,5}$")

        regex_cfg = sections_cfg.get("converters_regex", {})
        self.re_mhz = re.compile(regex_cfg.get("frequency_mhz", "(\\d+(?:\\.\\d+)?)\\s*MHZ"))
        self.re_ch = re.compile(regex_cfg.get("frequency_ch", "CH\\s*(\\d+)[XY]?"))

    def derive_navaid_type(self, text: str) -> str:
        upper = text.upper()
        for token_key, token_val in self.type_tokens.items():
            if token_key != "default" and token_key in upper:
                return token_val
        return self.type_tokens.get("default", "OTHER")

    def parse_frequency(self, raw_freq: str) -> Tuple[Optional[float], Optional[str]]:
        if not raw_freq:
            return None, None
        u = raw_freq.upper()
        m_mhz = self.re_mhz.search(u)
        if m_mhz:
            return float(m_mhz.group(1)), "MHZ"
        m_ch = self.re_ch.search(u)
        if m_ch:
            return float(m_ch.group(1)), "CH"
        return None, None

    def parse_enroute_navaids(
        self,
        html_content: str,
        document_filename: str,
        source_url: str,
        retrieved_at: str
    ) -> Tuple[List[Feature], List[ValidationIssue]]:
        features: List[Feature] = []
        issues: List[ValidationIssue] = []

        soup = BeautifulSoup(html_content, "html.parser")
        anchor = self.spec_enr.get("anchor", "ENR-4.1")
        div = soup.find(id=anchor)
        if not div:
            for h in soup.find_all(["h2", "h3", "h4", "h5"]):
                if "ENR 4.1" in h.get_text():
                    div = h.find_parent("div")
                    break

        if not div:
            issues.append(ValidationIssue(
                publication_id=self.cycle_id,
                severity=IssueSeverity.ERROR,
                rule_id="RULE_MISSING_SECTION",
                feature_key=None,
                section="ENR 4.1",
                message="Missing section ENR 4.1 Radio Navigation Aids in document",
                raw_snippet=""
            ))
            return features, issues

        table = div.find("table")
        if not table:
            issues.append(ValidationIssue(
                publication_id=self.cycle_id,
                severity=IssueSeverity.ERROR,
                rule_id="RULE_MISSING_TABLE",
                feature_key=None,
                section="ENR 4.1",
                message="Table not found in section ENR 4.1",
                raw_snippet=""
            ))
            return features, issues

        # Track features by natural key to merge collocated navaid facilities (e.g. VOR + DME on same ID)
        navaid_map: Dict[str, Feature] = {}

        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 6:
                continue

            station_name = cells[0].strip()
            nav_id = cells[1].strip()
            freq_raw = cells[2].strip()
            hours_raw = cells[3].strip()
            coord_raw = cells[4].strip()
            dme_elev_raw = cells[5].strip() if len(cells) > 5 else ""
            remarks_raw = cells[6].strip() if len(cells) > 6 else ""

            # Skip header and numbering rows (ICAO navaid IDs are 2-4 uppercase letters)
            if nav_id.upper() == "ID" or not re.match(r"^[A-Z]{2,4}$", nav_id):
                continue

            nat_key = f"NAV/{nav_id}"

            # If station already exists, merge collocated channel/elevation into it
            if nat_key in navaid_map:
                existing_feat = navaid_map[nat_key]
                num_freq, freq_unit = self.parse_frequency(freq_raw)
                if num_freq and not existing_feat.fields.get("frequency"):
                    existing_feat.fields["frequency"] = FeatureField(
                        value=num_freq, unit=freq_unit, source=self.publisher_name,
                        section="ENR 4.1", effective_date=self.effective_date,
                        retrieved_at=retrieved_at, source_url=source_url,
                        parser_version=PARSER_VERSION, raw_text=freq_raw
                    )
                if dme_elev_raw and dme_elev_raw.upper() != "NIL" and not existing_feat.fields.get("dmeElevation"):
                    dme_elev_val, _ = CONVERTER_REGISTRY["elevation"](dme_elev_raw)
                    if dme_elev_val is not None:
                        existing_feat.fields["dmeElevation"] = FeatureField(
                            value=dme_elev_val, unit=self.dme_unit, source=self.publisher_name,
                            section="ENR 4.1", effective_date=self.effective_date,
                            retrieved_at=retrieved_at, source_url=source_url,
                            parser_version=PARSER_VERSION, raw_text=dme_elev_raw
                        )
                existing_feat.content_hash = compute_content_hash(
                    existing_feat.fields,
                    existing_feat.geometry.model_dump() if existing_feat.geometry else None
                )
                continue

            parsed_coords, _ = CONVERTER_REGISTRY["coordinates"](coord_raw)
            if not parsed_coords:
                issues.append(ValidationIssue(
                    publication_id=self.cycle_id,
                    severity=IssueSeverity.ERROR,
                    rule_id="RULE_COORDINATE_FORMAT",
                    feature_key=nat_key,
                    section="ENR 4.1",
                    message=f"Failed to parse DMS coordinates '{coord_raw}' for navaid {nav_id}",
                    raw_snippet=coord_raw
                ))
                continue

            num_freq, freq_unit = self.parse_frequency(freq_raw)
            dme_elev_val, _ = CONVERTER_REGISTRY["elevation"](dme_elev_raw)
            navaid_type = self.derive_navaid_type(station_name)

            fields: Dict[str, FeatureField] = {
                "designator": FeatureField(
                    value=nav_id, unit=None, source=self.publisher_name,
                    section="ENR 4.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=nav_id
                ),
                "stationName": FeatureField(
                    value=station_name, unit=None, source=self.publisher_name,
                    section="ENR 4.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=station_name
                ),
                "frequencyRaw": FeatureField(
                    value=freq_raw, unit=None, source=self.publisher_name,
                    section="ENR 4.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=freq_raw
                ),
                "frequency": FeatureField(
                    value=num_freq, unit=freq_unit, source=self.publisher_name,
                    section="ENR 4.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=freq_raw
                ),
                "frequencyUnit": FeatureField(
                    value=freq_unit, unit=None, source=self.publisher_name,
                    section="ENR 4.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=freq_unit or ""
                ),
                "hours": FeatureField(
                    value=hours_raw, unit=None, source=self.publisher_name,
                    section="ENR 4.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=hours_raw
                ),
                "navaidType": FeatureField(
                    value=navaid_type, unit=None, source=self.publisher_name,
                    section="ENR 4.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=station_name
                )
            }
            if dme_elev_val is not None:
                fields["dmeElevation"] = FeatureField(
                    value=dme_elev_val, unit=self.dme_unit, source=self.publisher_name,
                    section="ENR 4.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=dme_elev_raw
                )
            if remarks_raw and remarks_raw.upper() != "NIL":
                fields["remarks"] = FeatureField(
                    value=remarks_raw, unit=None, source=self.publisher_name,
                    section="ENR 4.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=remarks_raw
                )

            geom = GeoJSONGeometry(type="Point", coordinates=list(parsed_coords))
            content_hash = compute_content_hash(fields, geom.model_dump())

            feat = Feature(
                feature_type=FeatureType.NAVAID,
                natural_key=nat_key,
                parent_key=None,
                publication_id=self.cycle_id,
                effective_from=self.effective_date,
                effective_to=None,
                source_class=SourceClass.AIP,
                temporality=Temporality.PERMANENT,
                geometry=geom,
                content_hash=content_hash,
                fields=fields
            )
            navaid_map[nat_key] = feat

        features = list(navaid_map.values())
        return features, issues

    # ------------------------------------------------------------------
    # AD 2.19 — airport radio navigation and landing aids
    #
    # The table mixes two row shapes: a flat aid carries the label in the first
    # cell (7 populated cells, "DVOR / DME (2°/2025) | BUB | 114.600 MHZ CH 93X |
    # …"), while an ILS sub-row is introduced by an extra empty cell and belongs
    # to the single-cell caption above it ("ILS 01 ( CAT I)"). Only the LOC row
    # publishes the ILS identifier; GP inherits it, DME repeats it.
    #
    # Natural keys: a flat aid is NAV/{ICAO}/{designator}; an ILS component adds an
    # explicit segment naming the component from config, because LOC and DME of one
    # system share the identifier and GP publishes none:
    #     NAV/EBBR/IBX/LOC   NAV/EBBR/IBX/GP   NAV/EBBR/IBX/DME
    # Nothing is merged or dropped, and no component is encoded in an opaque suffix.
    # ------------------------------------------------------------------
    def parse_airport_navaids(
        self,
        icao: str,
        html_content: str,
        document_filename: str,
        source_url: str,
        retrieved_at: str
    ) -> Tuple[List[Feature], List[ValidationIssue]]:
        features: List[Feature] = []
        issues: List[ValidationIssue] = []
        section_label = f"{icao} AD 2.19"

        anchor = require_str(self.spec_ad19, "anchor_template", "sections.yaml AD-2.19").format(icao=icao)
        soup = BeautifulSoup(html_content, "html.parser")
        div = soup.find(id=anchor)
        if not div:
            for h in soup.find_all(["h2", "h3", "h4", "h5"]):
                if "AD 2.19" in h.get_text():
                    div = h.find_parent("div")
                    break

        if not div:
            issues.append(ValidationIssue(
                publication_id=self.cycle_id,
                severity=IssueSeverity.ERROR,
                rule_id="RULE_MISSING_SECTION",
                feature_key=icao,
                section=section_label,
                message=f"Missing section AD 2.19 Radio Navigation and Landing Aids in {document_filename}",
                raw_snippet=""
            ))
            return features, issues

        table = div.find("table")
        if not table:
            issues.append(ValidationIssue(
                publication_id=self.cycle_id,
                severity=IssueSeverity.ERROR,
                rule_id="RULE_MISSING_TABLE",
                feature_key=icao,
                section=section_label,
                message=f"Table not found in section AD 2.19 of {icao}",
                raw_snippet=""
            ))
            return features, issues

        def make_field(value: Any, unit: Optional[str], raw_text: str) -> FeatureField:
            return FeatureField(
                value=value,
                unit=unit,
                source=self.publisher_name,
                section=section_label,
                effective_date=self.effective_date,
                retrieved_at=retrieved_at,
                source_url=source_url,
                parser_version=PARSER_VERSION,
                raw_text=raw_text
            )

        ils_category: Optional[str] = None
        ils_designator: Optional[str] = None
        used_keys: Dict[str, str] = {}

        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if not cells:
                continue

            # Group caption: "ILS 01 ( CAT I)" — sets the context for the rows below.
            if len(cells) == 1:
                caption = self.re_ils_caption.match(cells[0])
                ils_category = caption.group(1).strip() if caption else None
                ils_designator = None
                continue

            # ILS sub-rows carry a leading empty cell; shift it away so both shapes
            # share one column mapping.
            if len(cells) >= 8 and cells[0] == "":
                cells = cells[1:]
            if len(cells) < 7:
                continue

            label = cells[0].strip()
            designator_raw = cells[1].strip()
            freq_raw = cells[2].strip()
            hours_raw = cells[3].strip()
            coord_raw = cells[4].strip()
            dme_elev_raw = cells[5].strip()
            remarks_raw = cells[6].strip()

            # Header and column-numbering rows carry no designator and no type token.
            if label.lower().startswith("type of aid"):
                continue
            if label.isdigit():
                continue

            is_component = label.upper() in self.ils_component_tokens and ils_category is not None
            aid_type = self.derive_navaid_type(label if not is_component else label.upper())
            if designator_raw == "" and ils_designator is not None and is_component:
                designator = ils_designator  # GP publishes no identifier of its own
            elif self.re_designator.match(designator_raw.upper()):
                designator = designator_raw.upper()
            else:
                continue  # marker beacons ("dash / dash") and anything unidentifiable

            if not is_component and aid_type == self.type_tokens.get("default", "OTHER"):
                continue

            parsed_coords, _ = CONVERTER_REGISTRY["coordinates"](coord_raw)
            if not parsed_coords:
                issues.append(ValidationIssue(
                    publication_id=self.cycle_id,
                    severity=IssueSeverity.WARNING,
                    rule_id="RULE_COORDINATE_FORMAT",
                    feature_key=f"NAV/{icao}/{designator}",
                    section=section_label,
                    message=(
                        f"Failed to parse DMS coordinates '{coord_raw}' for "
                        f"{icao} aid {designator or label}"
                    ),
                    raw_snippet=coord_raw
                ))
                continue

            base_key = f"NAV/{icao}/{designator}"
            nat_key = f"{base_key}/{label.upper()}" if is_component else base_key
            if nat_key in used_keys:
                continue  # defensive: never overwrite an emitted feature
            used_keys[nat_key] = designator

            if is_component and label.upper() == "LOC":
                ils_designator = designator

            num_freq, freq_unit = self.parse_frequency(freq_raw)
            dme_elev_val, _ = CONVERTER_REGISTRY["elevation"](dme_elev_raw)

            fields: Dict[str, FeatureField] = {
                "designator": make_field(designator, None, designator_raw or designator),
                "aidType": make_field(aid_type, None, label),
                "frequencyRaw": make_field(freq_raw, None, freq_raw),
                "frequency": make_field(num_freq, freq_unit, freq_raw),
                "frequencyUnit": make_field(freq_unit, None, freq_unit or ""),
                "hours": make_field(hours_raw, None, hours_raw),
                "associatedAirport": make_field(icao, None, icao),
            }
            if dme_elev_val is not None:
                fields["dmeElevation"] = make_field(dme_elev_val, self.dme_unit_ad19, dme_elev_raw)
            if remarks_raw and remarks_raw.upper() != "NIL":
                fields["remarks"] = make_field(remarks_raw, None, remarks_raw)
            if is_component:
                fields["ilsCategory"] = make_field(ils_category, None, ils_category or "")
                fields["component"] = make_field(label.upper(), None, label)

            geom = GeoJSONGeometry(type="Point", coordinates=list(parsed_coords))
            features.append(Feature(
                feature_type=FeatureType.NAVAID,
                natural_key=nat_key,
                parent_key=icao,
                publication_id=self.cycle_id,
                effective_from=self.effective_date,
                effective_to=None,
                source_class=SourceClass.AIP,
                temporality=Temporality.PERMANENT,
                geometry=geom,
                content_hash=compute_content_hash(fields, geom.model_dump()),
                fields=fields
            ))

        return features, issues
