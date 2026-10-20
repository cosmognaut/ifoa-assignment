"""
structural domain parser that navigates the BeautifulSoup DOM, locates semantic sections and handles multi-table splits and rowspans, coordinates converters, links runway directions into physical pairs, attaches provenance (source) and computes the content hash.
"""
import re
from typing import List, Dict, Any, Tuple, Optional
from bs4 import BeautifulSoup

from pipeline.models.feature import (
    Feature, FeatureType, SourceClass, Temporality,
    FeatureField, GeoJSONGeometry
)
from pipeline.models.validation_issue import ValidationIssue, IssueSeverity
from pipeline.utils.hash_utils import compute_content_hash
from pipeline.parse.converters import resolve_converter
from pipeline.config import (
    load_crawler_config, load_sections_config, load_validation_config, require, require_str)

PARSER_VERSION = "1.0"

class RunwayParser:
    """
    This class models a simple runway parser.
    
    Plan:
    First locate <div id="{icao}-AD-2.12"> and <div id="{icao}"-AD-2.13> in the HTML and the discover columns by scanning table <th> and <td> text against required_headers defined in sections.yaml. Then, we dispatch each cell to CONVERTER_REGISTRY to create runwayDirection features. We also calculate reciprocal runway designators and then compute content hash.
    """
    def __init__(self, cycle_id: str, effective_date: str):
        """Initialise the parser using a cycle ID and an effective date, along with the crawler config"""
        self.cycle_id = cycle_id
        self.effective_date = effective_date

        self.crawler_cfg = load_crawler_config()
        self.publisher_name = require_str(require(self.crawler_cfg, "publisher", "crawler.yaml"), "name", "crawler.yaml publisher")

        sections_cfg = load_sections_config()
        sections_list = sections_cfg.get("sections", [])
        self.sections_by_id = {s["id"]: s for s in sections_list}

        self.spec_12 = self.sections_by_id["AD-2.12"]
        self.spec_13 = self.sections_by_id.get("AD-2.13", {})

        val_cfg = load_validation_config()
        self.rwy_regex = re.compile(val_cfg["runway_designator_format"])

    @staticmethod
    def get_reciprocal_designator(desig: str) -> Optional[str]:
        """Computes reciprocal runway designator using aerodynamic runway orientation math."""
        m = re.match(r"^(\d{2})([LCR]?)$", desig.upper().strip())
        if not m:
            return None
        num_str, suffix = m.groups()
        num = int(num_str)
        recip_num = (num + 18) % 36
        if recip_num == 0:
            recip_num = 36
        recip_suffix = {"L": "R", "R": "L", "C": "C"}.get(suffix, "")
        return f"{recip_num:02d}{recip_suffix}"

    @staticmethod
    def get_physical_pair_key(desig: str) -> str:
        """Returns sorted physical pair designator, e.g. '01-19' or '07R-25L'."""
        recip = RunwayParser.get_reciprocal_designator(desig)
        if recip:
            return "-".join(sorted([desig, recip]))
        return desig

    def _locate_section_div(self, soup: BeautifulSoup, icao: str, spec: Dict[str, Any]) -> Optional[Any]:
        anchor_id = spec["anchor_template"].format(icao=icao)
        div = soup.find(id=anchor_id)
        if div:
            return div

        heading_pattern = spec.get("heading_pattern", "")
        if heading_pattern:
            for h in soup.find_all(["h2", "h3", "h4", "h5"]):
                if heading_pattern.lower() in h.get_text().lower():
                    return h.find_parent("div")
        return None

    def _locate_table(self, container: Any, spec: Dict[str, Any]) -> Tuple[Optional[Any], Dict[str, int]]:
        locator_cfg = spec.get("table_locator", {})
        required_headers = [h.lower() for h in locator_cfg.get("required_headers", [])]

        for table in container.find_all("table"):
            col_map: Dict[str, int] = {}
            for tr in table.find_all("tr"):
                cells = tr.find_all(["th", "td"])
                for idx, cell in enumerate(cells):
                    text = cell.get_text(" ", strip=True).lower()
                    for req in required_headers:
                        if req in text and req not in col_map:
                            col_map[req] = idx

            if len(col_map) >= len(required_headers):
                field_col_map: Dict[str, int] = {}
                fields_cfg = spec.get("fields", {})
                for tr in table.find_all("tr"):
                    cells = tr.find_all(["th", "td"])
                    for idx, cell in enumerate(cells):
                        text = cell.get_text(" ", strip=True).lower()
                        for fld_name, fld_spec in fields_cfg.items():
                            hdr_pat = fld_spec.get("header_pattern", "").lower()
                            if hdr_pat and hdr_pat in text and fld_name not in field_col_map:
                                field_col_map[fld_name] = idx
                return table, field_col_map

        return None, {}

    def parse_airport_runways(
        self,
        icao: str,
        html_content: str,
        document_filename: str,
        source_url: str,
        retrieved_at: str
    ) -> Tuple[List[Feature], List[ValidationIssue]]:
        features: List[Feature] = []
        issues: List[ValidationIssue] = []

        soup = BeautifulSoup(html_content, "html.parser")
        sec_12_label = f"{icao} {self.spec_12['id'].replace('-', ' ')}"

        # 1. Locate AD 2.12 Runway Physical Characteristics
        div_12 = self._locate_section_div(soup, icao, self.spec_12)
        if not div_12:
            issues.append(ValidationIssue(
                publication_id=self.cycle_id,
                severity=IssueSeverity.ERROR,
                rule_id="RULE_MISSING_SECTION",
                feature_key=f"{icao}/RWY",
                section=sec_12_label,
                message=f"Missing section {self.spec_12['name']} for airport {icao}",
                raw_snippet=""
            ))
            return features, issues

        table_12, col_map_12 = self._locate_table(div_12, self.spec_12)
        if not table_12:
            issues.append(ValidationIssue(
                publication_id=self.cycle_id,
                severity=IssueSeverity.ERROR,
                rule_id="RULE_MISSING_TABLE",
                feature_key=f"{icao}/RWY",
                section=sec_12_label,
                message=f"Table with required headers not found in {self.spec_12['id']} for {icao}",
                raw_snippet=""
            ))
            return features, issues

        # 2. Locate AD 2.13 Declared Distances (optional per aerodrome)
        div_13 = self._locate_section_div(soup, icao, self.spec_13) if self.spec_13 else None
        table_13, col_map_13 = (None, {})
        if div_13 and self.spec_13:
            table_13, col_map_13 = self._locate_table(div_13, self.spec_13)

        # 3. Parse AD 2.13 Declared Distances via CONVERTER_REGISTRY
        declared_distances: Dict[str, Dict[str, FeatureField]] = {}
        if table_13 and self.spec_13:
            fields_13_cfg = self.spec_13.get("fields", {})
            sec_13_label = f"{icao} {self.spec_13['id'].replace('-', ' ')}"

            # Locate designator column dynamically by header text
            desig_col_idx = 0
            for header_tr in table_13.find_all("tr")[:2]:
                header_cells = header_tr.find_all(["th", "td"])
                for idx, cell in enumerate(header_cells):
                    txt = cell.get_text().lower()
                    if "designator" in txt or "rwy" in txt:
                        desig_col_idx = idx
                        break

            for tr in table_13.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) <= desig_col_idx:
                    continue
                desig_val = cells[desig_col_idx]
                if not self.rwy_regex.match(desig_val):
                    continue

                rwy_dd_fields: Dict[str, FeatureField] = {}
                for fld_name, fld_spec in fields_13_cfg.items():
                    col_idx = col_map_13.get(fld_name)
                    if col_idx is not None and col_idx < len(cells):
                        raw_val = cells[col_idx]
                        converter_name, converter_fn = resolve_converter(
                            fld_spec, f"sections.yaml section 'AD-2.13' field '{fld_name}'"
                        )
                        parsed_val, default_unit = converter_fn(raw_val)
                        unit = fld_spec.get("unit", default_unit)

                        rwy_dd_fields[fld_name] = FeatureField(
                            value=parsed_val,
                            unit=unit,
                            source=self.publisher_name,
                            section=sec_13_label,
                            effective_date=self.effective_date,
                            retrieved_at=retrieved_at,
                            source_url=source_url,
                            parser_version=PARSER_VERSION,
                            raw_text=raw_val
                        )
                declared_distances[desig_val] = rwy_dd_fields

        # 4. Parse AD 2.12 Physical characteristics rows via CONVERTER_REGISTRY
        fields_12_cfg = self.spec_12.get("fields", {})
        rwy_dir_features: Dict[str, Feature] = {}
        rwy_dir_raw: Dict[str, Dict[str, Any]] = {}

        for tr in table_12.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 4:
                continue

            desig_idx = col_map_12.get("designator", 0)
            if desig_idx >= len(cells):
                continue
            desig = cells[desig_idx]
            if not self.rwy_regex.match(desig):
                continue

            fields: Dict[str, FeatureField] = {
                "designator": FeatureField(
                    value=desig,
                    unit=None,
                    source=self.publisher_name,
                    section=sec_12_label,
                    effective_date=self.effective_date,
                    retrieved_at=retrieved_at,
                    source_url=source_url,
                    parser_version=PARSER_VERSION,
                    raw_text=desig
                )
            }

            coords_dd = None
            dims_raw, surface_raw = "", ""
            length_val, width_val = None, None
            surface_val = "OTHER"

            # Execute converters dynamically based on YAML field specs
            for fld_name, fld_spec in fields_12_cfg.items():
                if fld_name == "designator":
                    continue
                col_idx = col_map_12.get(fld_name)
                if col_idx is None or col_idx >= len(cells):
                    continue
                raw_cell = cells[col_idx]
                converter_name, converter_fn = resolve_converter(
                    fld_spec, f"sections.yaml section 'AD-2.12' field '{fld_name}'"
                )
                parsed_val, auto_unit = converter_fn(raw_cell)
                configured_unit = fld_spec.get("unit", auto_unit)

                # Handle specialized compound types defined by converters
                if converter_name == "dimensions":
                    dims_raw = raw_cell
                    if isinstance(parsed_val, tuple):
                        length_val, width_val = parsed_val
                        subfields = fld_spec.get("subfields", {})
                        len_u = subfields.get("length", {}).get("unit", "M")
                        wid_u = subfields.get("width", {}).get("unit", "M")
                        fields["length"] = FeatureField(
                            value=length_val, unit=len_u, source=self.publisher_name,
                            section=sec_12_label, effective_date=self.effective_date,
                            retrieved_at=retrieved_at, source_url=source_url,
                            parser_version=PARSER_VERSION, raw_text=raw_cell
                        )
                        fields["width"] = FeatureField(
                            value=width_val, unit=wid_u, source=self.publisher_name,
                            section=sec_12_label, effective_date=self.effective_date,
                            retrieved_at=retrieved_at, source_url=source_url,
                            parser_version=PARSER_VERSION, raw_text=raw_cell
                        )
                elif converter_name == "surface_strength":
                    surface_raw = raw_cell
                    if isinstance(parsed_val, tuple):
                        surface_val, strength_val = parsed_val
                        fields["surface"] = FeatureField(
                            value=surface_val, unit=None, source=self.publisher_name,
                            section=sec_12_label, effective_date=self.effective_date,
                            retrieved_at=retrieved_at, source_url=source_url,
                            parser_version=PARSER_VERSION, raw_text=raw_cell
                        )
                        fields["strength"] = FeatureField(
                            value=strength_val, unit=None, source=self.publisher_name,
                            section=sec_12_label, effective_date=self.effective_date,
                            retrieved_at=retrieved_at, source_url=source_url,
                            parser_version=PARSER_VERSION, raw_text=raw_cell
                        )
                elif converter_name == "coordinates":
                    if parsed_val:
                        coords_dd = parsed_val
                    else:
                        issues.append(ValidationIssue(
                            publication_id=self.cycle_id,
                            severity=IssueSeverity.ERROR,
                            rule_id="RULE_COORDINATE_FORMAT",
                            feature_key=f"{icao}/RWY/{desig}",
                            section=sec_12_label,
                            message=f"Failed to parse DMS coordinates '{raw_cell}' for runway {desig}",
                            raw_snippet=raw_cell
                        ))
                else:
                    fields[fld_name] = FeatureField(
                        value=parsed_val,
                        unit=configured_unit,
                        source=self.publisher_name,
                        section=sec_12_label,
                        effective_date=self.effective_date,
                        retrieved_at=retrieved_at,
                        source_url=source_url,
                        parser_version=PARSER_VERSION,
                        raw_text=raw_cell
                    )

            # Merge declared distances
            if desig in declared_distances:
                fields.update(declared_distances[desig])

            geom = None
            if coords_dd:
                geom = GeoJSONGeometry(type="Point", coordinates=list(coords_dd))

            content_hash = compute_content_hash(fields, geom.model_dump() if geom else None)
            pair_key = self.get_physical_pair_key(desig)
            nat_key = f"{icao}/RWY/{desig}"
            parent_key = f"{icao}/RWY/{pair_key}"

            feat = Feature(
                feature_type=FeatureType.RUNWAY_DIRECTION,
                natural_key=nat_key,
                parent_key=parent_key,
                publication_id=self.cycle_id,
                effective_from=self.effective_date,
                effective_to=None,
                source_class=SourceClass.AIP,
                temporality=Temporality.PERMANENT,
                geometry=geom,
                content_hash=content_hash,
                fields=fields
            )
            rwy_dir_features[desig] = feat
            rwy_dir_raw[desig] = {
                "length_val": length_val, "width_val": width_val, "dims_raw": dims_raw,
                "surface_val": surface_val, "surface_raw": surface_raw
            }
            features.append(feat)

        # 5. Build physical pair 'runway' features from topology
        grouped_pairs: Dict[str, List[str]] = {}
        for desig in rwy_dir_features.keys():
            pair_key = self.get_physical_pair_key(desig)
            if pair_key not in grouped_pairs:
                grouped_pairs[pair_key] = []
            grouped_pairs[pair_key].append(desig)

        pair_fields_cfg = self.spec_12.get("pair_fields", {})
        for pair_key, desigs in grouped_pairs.items():
            nat_key = f"{icao}/RWY/{pair_key}"
            parent_key = icao

            coords_line = []
            ordered_desigs = sorted(desigs)
            for d in ordered_desigs:
                feat = rwy_dir_features.get(d)
                if feat and feat.geometry and feat.geometry.coordinates:
                    coords_line.append(feat.geometry.coordinates)

            line_geom = None
            if len(coords_line) == 2:
                line_geom = GeoJSONGeometry(type="LineString", coordinates=coords_line)

            sample_raw = rwy_dir_raw.get(ordered_desigs[0], {})

            pair_fields: Dict[str, FeatureField] = {
                "pairDesignator": FeatureField(
                    value=pair_key,
                    unit=pair_fields_cfg.get("pairDesignator", {}).get("unit"),
                    source=self.publisher_name,
                    section=sec_12_label,
                    effective_date=self.effective_date,
                    retrieved_at=retrieved_at,
                    source_url=source_url,
                    parser_version=PARSER_VERSION,
                    raw_text=pair_key
                ),
                "length": FeatureField(
                    value=sample_raw.get("length_val"),
                    unit=pair_fields_cfg.get("length", {}).get("unit", "M"),
                    source=self.publisher_name,
                    section=sec_12_label,
                    effective_date=self.effective_date,
                    retrieved_at=retrieved_at,
                    source_url=source_url,
                    parser_version=PARSER_VERSION,
                    raw_text=sample_raw.get("dims_raw", "")
                ),
                "width": FeatureField(
                    value=sample_raw.get("width_val"),
                    unit=pair_fields_cfg.get("width", {}).get("unit", "M"),
                    source=self.publisher_name,
                    section=sec_12_label,
                    effective_date=self.effective_date,
                    retrieved_at=retrieved_at,
                    source_url=source_url,
                    parser_version=PARSER_VERSION,
                    raw_text=sample_raw.get("dims_raw", "")
                ),
                "surface": FeatureField(
                    value=sample_raw.get("surface_val"),
                    unit=pair_fields_cfg.get("surface", {}).get("unit"),
                    source=self.publisher_name,
                    section=sec_12_label,
                    effective_date=self.effective_date,
                    retrieved_at=retrieved_at,
                    source_url=source_url,
                    parser_version=PARSER_VERSION,
                    raw_text=sample_raw.get("surface_raw", "")
                )
            }

            content_hash = compute_content_hash(pair_fields, line_geom.model_dump() if line_geom else None)

            runway_feat = Feature(
                feature_type=FeatureType.RUNWAY,
                natural_key=nat_key,
                parent_key=parent_key,
                publication_id=self.cycle_id,
                effective_from=self.effective_date,
                effective_to=None,
                source_class=SourceClass.AIP,
                temporality=Temporality.PERMANENT,
                geometry=line_geom,
                content_hash=content_hash,
                fields=pair_fields
            )
            features.append(runway_feat)

        return features, issues

# Static function aliases (zero import-time config I/O)
get_reciprocal_designator = RunwayParser.get_reciprocal_designator
get_physical_pair_key = RunwayParser.get_physical_pair_key
