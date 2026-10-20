"""
Declarative parser for Aerodrome Summary & Operational Info:
- AD 2.2 Aerodrome Geographical and Administrative Data
- AD 2.3 Operational Hours
- AD 2.6 Rescue and Fire Fighting Services (RFFS)
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
from pipeline.parse.converters import resolve_converter
from pipeline.config import load_crawler_config, load_sections_config, require, require_str

PARSER_VERSION = "1.0"

class AirportParser:
    def __init__(self, cycle_id: str, effective_date: str):
        self.cycle_id = cycle_id
        self.effective_date = effective_date

        crawler_cfg = load_crawler_config()
        self.publisher_name = require_str(require(crawler_cfg, "publisher", "crawler.yaml"), "name", "crawler.yaml publisher")

        sections_cfg = load_sections_config()
        sections_list = sections_cfg.get("sections", [])
        self.sections_by_id = {s["id"]: s for s in sections_list}

        self.spec_21 = self.sections_by_id.get("AD-2.1", {})
        self.spec_22 = self.sections_by_id.get("AD-2.2", {})
        self.spec_23 = self.sections_by_id.get("AD-2.3", {})
        self.spec_26 = self.sections_by_id.get("AD-2.6", {})

    def _locate_section_div(self, soup: BeautifulSoup, icao: str, spec: Dict[str, Any]) -> Optional[Any]:
        anchor = spec.get("anchor_template", "{icao}-" + spec.get("id", "")).format(icao=icao)
        div = soup.find(id=anchor)
        if div:
            return div
        heading = spec.get("heading_pattern", "")
        if heading:
            for h in soup.find_all(["h2", "h3", "h4", "h5"]):
                if heading.lower() in h.get_text().lower():
                    return h.find_parent("div")
        return None

    def _extract_key_values(self, container: Any) -> List[Tuple[str, str]]:
        pairs = []
        if not container:
            return pairs
        for tr in container.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) == 2:
                pairs.append((cells[0].lower(), cells[1]))
            elif len(cells) >= 3:
                pairs.append((cells[1].lower(), cells[2]))
        return pairs

    def _match_field(self, label: str, pat: str) -> bool:
        """Exact match for 'ats' to prevent substring collision with 'ats reporting office', else substring."""
        if not pat:
            return False
        clean_label = label.strip()
        clean_pat = pat.strip()
        if clean_pat == "ats":
            return clean_label == "ats"
        return clean_pat in clean_label

    def parse_airport_summary(
        self,
        icao: str,
        html_content: str,
        document_filename: str,
        source_url: str,
        retrieved_at: str
    ) -> Tuple[Optional[Feature], List[ValidationIssue]]:
        issues: List[ValidationIssue] = []
        soup = BeautifulSoup(html_content, "html.parser")

        fields: Dict[str, FeatureField] = {}
        arp_coords: Optional[Tuple[float, float]] = None

        # 1. Parse AD 2.2 Geographical and Administrative Data
        div_22 = self._locate_section_div(soup, icao, self.spec_22)
        sec_22_label = f"{icao} AD 2.2"
        if div_22:
            kv_22 = self._extract_key_values(div_22)
            fields_22 = self.spec_22.get("fields", {})

            for label, raw_val in kv_22:
                matched = [(fn, fs) for fn, fs in fields_22.items() if self._match_field(label, fs.get("header_pattern", "").lower())]
                matched_names = {m[0] for m in matched}

                # Allow ICAO combined compound row: Elevation / Reference temperature
                if len(matched) > 1 and matched_names != {"elevation", "referenceTemperature"}:
                    issues.append(ValidationIssue(
                        publication_id=self.cycle_id,
                        severity=IssueSeverity.WARNING,
                        rule_id="RULE_AMBIGUOUS_FIELD_PATTERN",
                        feature_key=icao,
                        section=sec_22_label,
                        message=f"Ambiguous pattern: label '{label}' matched {[m[0] for m in matched]}",
                        raw_snippet=label
                    ))
                else:
                    for f_name, f_spec in matched:
                        if f_name not in fields:
                            converter_name, converter_fn = resolve_converter(
                                f_spec, f"sections.yaml section 'AD-2.2' field '{f_name}'"
                            )
                            parsed_val, default_unit = converter_fn(raw_val)
                            unit = f_spec.get("unit", default_unit)

                            if converter_name == "coordinates":
                                if parsed_val:
                                    arp_coords = parsed_val
                                    fields[f_name] = FeatureField(
                                        value=list(parsed_val),
                                        unit=None,
                                        source=self.publisher_name,
                                        section=sec_22_label,
                                        effective_date=self.effective_date,
                                        retrieved_at=retrieved_at,
                                        source_url=source_url,
                                        parser_version=PARSER_VERSION,
                                        raw_text=raw_val
                                    )
                                else:
                                    issues.append(ValidationIssue(
                                        publication_id=self.cycle_id,
                                        severity=IssueSeverity.ERROR,
                                        rule_id="RULE_COORDINATE_FORMAT",
                                        feature_key=icao,
                                        section=sec_22_label,
                                        message=f"Failed to parse ARP coordinates '{raw_val}' for airport {icao}",
                                        raw_snippet=raw_val
                                    ))
                            else:
                                fields[f_name] = FeatureField(
                                    value=parsed_val,
                                    unit=unit,
                                    source=self.publisher_name,
                                    section=sec_22_label,
                                    effective_date=self.effective_date,
                                    retrieved_at=retrieved_at,
                                    source_url=source_url,
                                    parser_version=PARSER_VERSION,
                                    raw_text=raw_val
                                )

        # 2. Parse AD 2.1 Aerodrome Location Indicator and Name (paragraph form)
        div_21 = self._locate_section_div(soup, icao, self.spec_21)
        sec_21_label = f"{icao} AD 2.1"
        for field_name, field_spec in self.spec_21.get("fields", {}).items():
            if field_spec.get("source") != "paragraph":
                continue

            paragraphs = div_21.find_all("p") if div_21 else []
            paragraph_text = ""
            if paragraphs:
                paragraph_text = paragraphs[-1].get_text(" ", strip=True).replace("\xa0", " ")

            pattern = field_spec.get("pattern", "")
            match = re.match(pattern, paragraph_text) if pattern else None
            if match is None:
                issues.append(ValidationIssue(
                    publication_id=self.cycle_id,
                    severity=IssueSeverity.WARNING,
                    rule_id="RULE_MISSING_AIRPORT_NAME",
                    feature_key=icao,
                    section=sec_21_label,
                    message=f"Airport name missing for {icao} in {sec_21_label}",
                    raw_snippet=paragraph_text
                ))
                continue

            value = match.group(1).strip()
            converter_name, converter_fn = resolve_converter(
                field_spec, f"sections.yaml section 'AD-2.1' field '{field_name}'"
            )
            parsed_value, _ = converter_fn(value)
            fields[field_name] = FeatureField(
                value=parsed_value,
                unit=None,
                source=self.publisher_name,
                section=sec_21_label,
                effective_date=self.effective_date,
                retrieved_at=retrieved_at,
                source_url=source_url,
                parser_version=PARSER_VERSION,
                raw_text=paragraph_text
            )

        # 3. Parse AD 2.3 Operational Hours
        div_23 = self._locate_section_div(soup, icao, self.spec_23)
        sec_23_label = f"{icao} AD 2.3"
        if div_23:
            kv_23 = self._extract_key_values(div_23)
            fields_23 = self.spec_23.get("fields", {})

            for label, raw_val in kv_23:
                matched = [(fn, fs) for fn, fs in fields_23.items() if self._match_field(label, fs.get("header_pattern", "").lower())]
                if len(matched) > 1:
                    issues.append(ValidationIssue(
                        publication_id=self.cycle_id,
                        severity=IssueSeverity.WARNING,
                        rule_id="RULE_AMBIGUOUS_FIELD_PATTERN",
                        feature_key=icao,
                        section=sec_23_label,
                        message=f"Ambiguous pattern: label '{label}' matched {[m[0] for m in matched]}",
                        raw_snippet=label
                    ))
                elif len(matched) == 1:
                    f_name, f_spec = matched[0]
                    if f_name not in fields:
                        fields[f_name] = FeatureField(
                            value=raw_val,
                            unit=None,
                            source=self.publisher_name,
                            section=sec_23_label,
                            effective_date=self.effective_date,
                            retrieved_at=retrieved_at,
                            source_url=source_url,
                            parser_version=PARSER_VERSION,
                            raw_text=raw_val
                        )

        # 4. Parse AD 2.6 Rescue and Fire Fighting Services (RFFS)
        div_26 = self._locate_section_div(soup, icao, self.spec_26)
        sec_26_label = f"{icao} AD 2.6"
        if div_26:
            kv_26 = self._extract_key_values(div_26)
            fields_26 = self.spec_26.get("fields", {})

            for label, raw_val in kv_26:
                matched = [(fn, fs) for fn, fs in fields_26.items() if self._match_field(label, fs.get("header_pattern", "").lower())]
                if len(matched) > 1:
                    issues.append(ValidationIssue(
                        publication_id=self.cycle_id,
                        severity=IssueSeverity.WARNING,
                        rule_id="RULE_AMBIGUOUS_FIELD_PATTERN",
                        feature_key=icao,
                        section=sec_26_label,
                        message=f"Ambiguous pattern: label '{label}' matched {[m[0] for m in matched]}",
                        raw_snippet=label
                    ))
                elif len(matched) == 1:
                    f_name, f_spec = matched[0]
                    if f_name not in fields:
                        fields[f_name] = FeatureField(
                            value=raw_val,
                            unit=None,
                            source=self.publisher_name,
                            section=sec_26_label,
                            effective_date=self.effective_date,
                            retrieved_at=retrieved_at,
                            source_url=source_url,
                            parser_version=PARSER_VERSION,
                            raw_text=raw_val
                        )

        if not fields:
            return None, issues

        geom = None
        if arp_coords:
            geom = GeoJSONGeometry(type="Point", coordinates=list(arp_coords))

        content_hash = compute_content_hash(fields, geom.model_dump() if geom else None)

        feat = Feature(
            feature_type=FeatureType.AERODROME,
            natural_key=icao,
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

        return feat, issues
