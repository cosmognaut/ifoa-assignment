"""
Declarative parser for ENR 4.4 Significant Points (Waypoints).
"""
import re
from typing import List, Tuple, Dict, Any
from bs4 import BeautifulSoup

from pipeline.models.feature import (
    Feature, FeatureType, SourceClass, Temporality,
    FeatureField, GeoJSONGeometry
)
from pipeline.models.validation_issue import ValidationIssue, IssueSeverity
from pipeline.utils.hash_utils import compute_content_hash
from pipeline.parse.converters import CONVERTER_REGISTRY
from pipeline.config import load_crawler_config, load_sections_config

PARSER_VERSION = "1.0"

class WaypointParser:
    def __init__(self, cycle_id: str, effective_date: str):
        self.cycle_id = cycle_id
        self.effective_date = effective_date

        crawler_cfg = load_crawler_config()
        self.publisher_name = crawler_cfg.get("publisher", {}).get("name") or crawler_cfg.get("publisher_name", "Belgium & Luxembourg eAIP")

        sections_cfg = load_sections_config()
        sections_list = sections_cfg.get("sections", [])
        self.spec = next((s for s in sections_list if s["id"] == "ENR-4.4"), {})

    def parse_waypoints(
        self,
        html_content: str,
        document_filename: str,
        source_url: str,
        retrieved_at: str
    ) -> Tuple[List[Feature], List[ValidationIssue]]:
        features: List[Feature] = []
        issues: List[ValidationIssue] = []

        soup = BeautifulSoup(html_content, "html.parser")
        anchor = self.spec.get("anchor", "ENR-4.4")
        div = soup.find(id=anchor)
        if not div:
            for h in soup.find_all(["h2", "h3", "h4", "h5"]):
                if "ENR 4.4" in h.get_text():
                    div = h.find_parent("div")
                    break

        if not div:
            issues.append(ValidationIssue(
                publication_id=self.cycle_id,
                severity=IssueSeverity.ERROR,
                rule_id="RULE_MISSING_SECTION",
                feature_key=None,
                section="ENR-4.4",
                message="Missing section ENR 4.4 Significant Points in document",
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
                section="ENR-4.4",
                message="Table not found in section ENR 4.4",
                raw_snippet=""
            ))
            return features, issues

        # Scan rows skipping headers (first 2 rows)
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            designator = cells[0].strip()
            coord_raw = cells[1].strip()

            # Skip header rows like 'Name-code designator' or numbering row '1'
            if not re.match(r"^[A-Z0-9]{3,5}$", designator):
                continue

            parsed_coords, _ = CONVERTER_REGISTRY["coordinates"](coord_raw)
            if not parsed_coords:
                issues.append(ValidationIssue(
                    publication_id=self.cycle_id,
                    severity=IssueSeverity.ERROR,
                    rule_id="RULE_COORDINATE_FORMAT",
                    feature_key=f"WPT/{designator}",
                    section="ENR-4.4",
                    message=f"Failed to parse DMS coordinates '{coord_raw}' for waypoint {designator}",
                    raw_snippet=coord_raw
                ))
                continue

            route_raw = cells[2] if len(cells) > 2 else ""
            other_route_raw = cells[3] if len(cells) > 3 else ""
            combined_routes = f"{route_raw} {other_route_raw}".strip()

            fields: Dict[str, FeatureField] = {
                "designator": FeatureField(
                    value=designator,
                    unit=None,
                    source=self.publisher_name,
                    section="ENR-4.4",
                    effective_date=self.effective_date,
                    retrieved_at=retrieved_at,
                    source_url=source_url,
                    parser_version=PARSER_VERSION,
                    raw_text=designator
                ),
                "coordinates": FeatureField(
                    value=list(parsed_coords),
                    unit=None,
                    source=self.publisher_name,
                    section="ENR-4.4",
                    effective_date=self.effective_date,
                    retrieved_at=retrieved_at,
                    source_url=source_url,
                    parser_version=PARSER_VERSION,
                    raw_text=coord_raw
                )
            }
            if combined_routes:
                fields["atsRoutes"] = FeatureField(
                    value=combined_routes,
                    unit=None,
                    source=self.publisher_name,
                    section="ENR-4.4",
                    effective_date=self.effective_date,
                    retrieved_at=retrieved_at,
                    source_url=source_url,
                    parser_version=PARSER_VERSION,
                    raw_text=combined_routes
                )

            geom = GeoJSONGeometry(type="Point", coordinates=list(parsed_coords))
            content_hash = compute_content_hash(fields, geom.model_dump())

            feat = Feature(
                feature_type=FeatureType.WAYPOINT,
                natural_key=f"WPT/{designator}",
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
            features.append(feat)

        return features, issues
