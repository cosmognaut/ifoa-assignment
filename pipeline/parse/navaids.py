"""
Declarative parser for Radio Navigation Aids:
- ENR 4.1 Radio Navigation Aids - En-Route (VOR, DME, TACAN, NDB)
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
                            value=dme_elev_val, unit="FT", source=self.publisher_name,
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
                    value=dme_elev_val, unit="FT", source=self.publisher_name,
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
