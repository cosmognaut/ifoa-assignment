"""
Declarative parser for ATS Routes:
- ENR 3.1 Lower ATS Routes
- ENR 3.2 Upper ATS Routes
"""
import re
from typing import List, Tuple, Dict, Any, Set, Optional
from bs4 import BeautifulSoup

from pipeline.models.feature import (
    Feature, FeatureType, SourceClass, Temporality,
    FeatureField, GeoJSONGeometry
)
from pipeline.models.validation_issue import ValidationIssue, IssueSeverity
from pipeline.utils.hash_utils import compute_content_hash
from pipeline.parse.converters import CONVERTER_REGISTRY
from pipeline.config import load_crawler_config, load_sections_config, load_validation_config

PARSER_VERSION = "1.0"

class RouteParser:
    def __init__(self, cycle_id: str, effective_date: str):
        self.cycle_id = cycle_id
        self.effective_date = effective_date

        crawler_cfg = load_crawler_config()
        self.publisher_name = crawler_cfg.get("publisher", {}).get("name") or crawler_cfg.get("publisher_name", "Belgium & Luxembourg eAIP")

        sections_cfg = load_sections_config()
        sections_list = sections_cfg.get("sections", [])
        self.spec_31 = next((s for s in sections_list if s["id"] == "ENR-3.1"), {})
        self.spec_32 = next((s for s in sections_list if s["id"] == "ENR-3.2"), {})

        val_cfg = load_validation_config()
        rules_cfg = val_cfg.get("rules", {})
        self.require_resolved_waypoints = bool(rules_cfg.get("require_resolved_waypoints", True))
        self.require_limit_units = bool(rules_cfg.get("require_limit_units", True))

    def parse_routes_document(
        self,
        html_content: str,
        section_id: str,
        document_filename: str,
        source_url: str,
        retrieved_at: str,
        known_waypoint_keys: Optional[Set[str]] = None,
        known_navaid_keys: Optional[Set[str]] = None
    ) -> Tuple[List[Feature], List[ValidationIssue]]:
        features: List[Feature] = []
        issues: List[ValidationIssue] = []

        soup = BeautifulSoup(html_content, "html.parser")
        anchor = section_id
        div = soup.find(id=anchor)
        if not div:
            pattern = "ENR 3.2" if "3.2" in section_id else "ENR 3.1"
            for h in soup.find_all(["h2", "h3", "h4", "h5"]):
                if pattern in h.get_text():
                    div = h.find_parent("div")
                    break

        if not div:
            return features, issues

        # Check for NIL content
        div_text = div.get_text(" ", strip=True)
        if "NIL" in div_text and len(div.find_all("table")) == 0:
            return features, issues

        sec_label = section_id.replace("-", " ")
        route_class = "upper" if "3.2" in section_id else "lower"
        known_wpts = known_waypoint_keys or set()
        known_navs = known_navaid_keys or set()

        tables = div.find_all("table")
        for table in tables:
            first_tr = table.find("tr")
            if not first_tr or "Route designator" not in first_tr.get_text():
                continue

            # 1. Discover Route Designator & RNP Type
            route_desig = None
            rnp_type = None
            for tr in table.find_all("tr")[:15]:
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
                if cells:
                    m = re.match(r"^([UL]\s*[0-9]{3}|[A-Z]{1,2}\s*[0-9]{1,4})(?:\s*\{([^}]+)\})?", cells[0])
                    if m and not cells[0].startswith("Route"):
                        route_desig = m.group(1).replace(" ", "")
                        rnp_type = m.group(2)
                        break

            if not route_desig:
                continue

            nat_key = f"RTE/{route_desig}"
            coords_line: List[List[float]] = []
            waypoint_seq: List[str] = []
            upper_limit_parsed = None
            lower_limit_parsed = None
            controlling_unit_val = None

            # 2. Parse Points and Vertical Limits
            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if not cells:
                    continue

                # Point row marked with '∆' or significant point coordinate
                if len(cells) >= 3 and cells[0] == "∆":
                    pt_text = cells[1]
                    m_id = re.search(r"\(([A-Z]{2,4})\)", pt_text)
                    if m_id:
                        point_id = m_id.group(1)
                    else:
                        point_id = pt_text.split()[0].strip()

                    coord_raw = cells[2]
                    parsed_coords, _ = CONVERTER_REGISTRY["coordinates"](coord_raw)
                    if parsed_coords:
                        coords_line.append(list(parsed_coords))

                    # Resolve natural key against known features
                    target_wpt_key = f"WPT/{point_id}"
                    target_nav_key = f"NAV/{point_id}"

                    if target_wpt_key in known_wpts:
                        resolved_key = target_wpt_key
                    elif target_nav_key in known_navs:
                        resolved_key = target_nav_key
                    else:
                        resolved_key = target_wpt_key
                        if self.require_resolved_waypoints:
                            issues.append(ValidationIssue(
                                publication_id=self.cycle_id,
                                severity=IssueSeverity.ERROR,
                                rule_id="RULE_UNRESOLVED_WAYPOINT",
                                feature_key=nat_key,
                                section=sec_label,
                                message=f"Route {route_desig} references unresolved waypoint '{point_id}'",
                                raw_snippet=point_id
                            ))

                    waypoint_seq.append(resolved_key)

                # Segment metadata row (limits and controlling unit)
                if len(cells) >= 8 and ("FL" in " ".join(cells) or "FT" in " ".join(cells) or "UNL" in " ".join(cells)):
                    for c in cells:
                        if not upper_limit_parsed and ("FL" in c or "UNL" in c):
                            u_lim, _ = CONVERTER_REGISTRY["vertical_limit"](c)
                            if u_lim and u_lim.get("value") is not None:
                                upper_limit_parsed = u_lim
                                if self.require_limit_units and (u_lim.get("unit") is None or u_lim.get("reference") is None):
                                    issues.append(ValidationIssue(
                                        publication_id=self.cycle_id,
                                        severity=IssueSeverity.WARNING,
                                        rule_id="RULE_LIMIT_UNIT_MISSING",
                                        feature_key=nat_key,
                                        section=sec_label,
                                        message=f"Upper vertical limit '{c}' on route {route_desig} missing unit or reference",
                                        raw_snippet=c
                                    ))
                        if not lower_limit_parsed and ("FL" in c or "FT" in c or "SFC" in c):
                            l_lim, _ = CONVERTER_REGISTRY["vertical_limit"](c)
                            if l_lim and l_lim.get("value") is not None and l_lim != upper_limit_parsed:
                                lower_limit_parsed = l_lim
                                if self.require_limit_units and (l_lim.get("unit") is None or l_lim.get("reference") is None):
                                    issues.append(ValidationIssue(
                                        publication_id=self.cycle_id,
                                        severity=IssueSeverity.WARNING,
                                        rule_id="RULE_LIMIT_UNIT_MISSING",
                                        feature_key=nat_key,
                                        section=sec_label,
                                        message=f"Lower vertical limit '{c}' on route {route_desig} missing unit or reference",
                                        raw_snippet=c
                                    ))

                    # Controlling unit in cell with 'Radar' or 'ACC' or 'UAC'
                    for c in cells:
                        if any(k in c.upper() for k in ("ACC", "UAC", "RADAR", "CONTROL")):
                            controlling_unit_val = c
                            break

            # Deduplicate sequential waypoints
            clean_seq: List[str] = []
            for w in waypoint_seq:
                if not clean_seq or clean_seq[-1] != w:
                    clean_seq.append(w)

            fields: Dict[str, FeatureField] = {
                "designator": FeatureField(
                    value=route_desig, unit=None, source=self.publisher_name,
                    section=sec_label, effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=route_desig
                ),
                "routeClass": FeatureField(
                    value=route_class, unit=None, source=self.publisher_name,
                    section=sec_label, effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=section_id
                ),
                "waypointSequence": FeatureField(
                    value=clean_seq, unit=None, source=self.publisher_name,
                    section=sec_label, effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=" -> ".join(clean_seq)
                ),
            }

            if upper_limit_parsed:
                fields["upperLimit"] = FeatureField(
                    value=upper_limit_parsed, unit=upper_limit_parsed.get("unit"), source=self.publisher_name,
                    section=sec_label, effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=str(upper_limit_parsed.get("value"))
                )
            if lower_limit_parsed:
                fields["lowerLimit"] = FeatureField(
                    value=lower_limit_parsed, unit=lower_limit_parsed.get("unit"), source=self.publisher_name,
                    section=sec_label, effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=str(lower_limit_parsed.get("value"))
                )
            if rnp_type:
                fields["navigationSpecification"] = FeatureField(
                    value=rnp_type, unit=None, source=self.publisher_name,
                    section=sec_label, effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=rnp_type
                )
            if controlling_unit_val:
                fields["controllingUnit"] = FeatureField(
                    value=controlling_unit_val, unit=None, source=self.publisher_name,
                    section=sec_label, effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=controlling_unit_val
                )

            line_geom = None
            if len(coords_line) >= 2:
                line_geom = GeoJSONGeometry(type="LineString", coordinates=coords_line)

            content_hash = compute_content_hash(fields, line_geom.model_dump() if line_geom else None)

            feat = Feature(
                feature_type=FeatureType.ROUTE,
                natural_key=nat_key,
                parent_key=None,
                publication_id=self.cycle_id,
                effective_from=self.effective_date,
                effective_to=None,
                source_class=SourceClass.AIP,
                temporality=Temporality.PERMANENT,
                geometry=line_geom,
                content_hash=content_hash,
                fields=fields
            )
            features.append(feat)

        return features, issues
