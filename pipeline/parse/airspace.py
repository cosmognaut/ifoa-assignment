"""
Declarative parser for Airspace and Special-Use Airspace (ATTRIBUTES ONLY):
- ENR 2.1 Air Traffic Services Airspace (FIR, UIR, CTA, TMA, CTR)
- ENR 5.1 Prohibited, Restricted and Danger Areas
- ENR 5.2 Military Exercise and Training Areas

ABSOLUTE PROHIBITION: Geometry MUST be null for all airspace and specialUseArea features.
Zero polygon synthesis or bounding box approximation.
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
from pipeline.config import load_crawler_config, require, require_str

PARSER_VERSION = "1.0"

class AirspaceParser:
    def __init__(self, cycle_id: str, effective_date: str):
        self.cycle_id = cycle_id
        self.effective_date = effective_date

        crawler_cfg = load_crawler_config()
        self.publisher_name = require_str(require(crawler_cfg, "publisher", "crawler.yaml"), "name", "crawler.yaml publisher")

    def _split_vertical_limits(self, raw_text: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if not raw_text or raw_text.upper() == "NIL":
            return None, None
        parts = raw_text.split("/")
        if len(parts) == 2:
            upper_raw = parts[0].strip()
            lower_raw = parts[1].strip()
            u_lim, _ = CONVERTER_REGISTRY["vertical_limit"](upper_raw)
            l_lim, _ = CONVERTER_REGISTRY["vertical_limit"](lower_raw)
            return u_lim, l_lim
        elif len(parts) == 1:
            lim, _ = CONVERTER_REGISTRY["vertical_limit"](parts[0].strip())
            return lim, None
        return None, None

    def parse_airspace_enr21(
        self,
        html_content: str,
        document_filename: str,
        source_url: str,
        retrieved_at: str
    ) -> Tuple[List[Feature], List[ValidationIssue]]:
        """Parses ENR 2.1 ATS Airspace tables (FIR, UIR, CTA, TMA). Geometry is strictly null."""
        features: List[Feature] = []
        issues: List[ValidationIssue] = []

        soup = BeautifulSoup(html_content, "html.parser")
        div = soup.find(id="ENR-2.1")
        if not div:
            for h in soup.find_all(["h2", "h3", "h4", "h5"]):
                if "ENR 2.1" in h.get_text():
                    div = h.find_parent("div")
                    break

        if not div:
            return features, issues

        tables = div.find_all("table")
        for idx, table in enumerate(tables):
            cap = table.find("caption")
            raw_title = cap.get_text(" ", strip=True) if cap else f"AIRSPACE {idx+1}"
            designation = re.sub(r"\s*\(\d+\)", "", raw_title).strip()
            if not designation:
                continue

            nat_key = f"ASP/{designation}"
            lateral_limits_raw = ""
            vertical_limits_raw = ""
            airspace_class_val = ""
            controlling_unit_val = ""

            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) >= 2:
                    label = cells[0].lower()
                    if "lateral limits" in label:
                        lateral_limits_raw = cells[1]
                    elif "vertical limits" in label:
                        vertical_limits_raw = cells[1]
                    elif "airspace class" in label:
                        airspace_class_val = re.sub(r"\s*\(\d+\)", "", cells[1]).strip()
                    elif "units" in label or "control units" in label:
                        controlling_unit_val = cells[1]

            if not lateral_limits_raw:
                continue

            upper_lim, lower_lim = self._split_vertical_limits(vertical_limits_raw)

            fields: Dict[str, FeatureField] = {
                "designation": FeatureField(
                    value=designation, unit=None, source=self.publisher_name,
                    section="ENR 2.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=raw_title
                ),
                "lateralLimitsRaw": FeatureField(
                    value=lateral_limits_raw, unit=None, source=self.publisher_name,
                    section="ENR 2.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=lateral_limits_raw
                ),
            }

            if airspace_class_val:
                fields["airspaceClass"] = FeatureField(
                    value=airspace_class_val, unit=None, source=self.publisher_name,
                    section="ENR 2.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=airspace_class_val
                )
            if upper_lim:
                fields["upperLimit"] = FeatureField(
                    value=upper_lim, unit=upper_lim.get("unit"), source=self.publisher_name,
                    section="ENR 2.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=vertical_limits_raw
                )
            if lower_lim:
                fields["lowerLimit"] = FeatureField(
                    value=lower_lim, unit=lower_lim.get("unit"), source=self.publisher_name,
                    section="ENR 2.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=vertical_limits_raw
                )
            if controlling_unit_val:
                fields["controllingUnit"] = FeatureField(
                    value=controlling_unit_val, unit=None, source=self.publisher_name,
                    section="ENR 2.1", effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=controlling_unit_val
                )

            # ABSOLUTE PROHIBITION: Geometry MUST be null
            content_hash = compute_content_hash(fields, None)

            feat = Feature(
                feature_type=FeatureType.AIRSPACE,
                natural_key=nat_key,
                parent_key=None,
                publication_id=self.cycle_id,
                effective_from=self.effective_date,
                effective_to=None,
                source_class=SourceClass.AIP,
                temporality=Temporality.PERMANENT,
                geometry=None,
                content_hash=content_hash,
                fields=fields
            )
            features.append(feat)

        return features, issues

    def parse_special_use_document(
        self,
        html_content: str,
        section_id: str,
        document_filename: str,
        source_url: str,
        retrieved_at: str
    ) -> Tuple[List[Feature], List[ValidationIssue]]:
        """Parses ENR 5.1 and ENR 5.2 Special Use Airspace. Geometry is strictly null."""
        features: List[Feature] = []
        issues: List[ValidationIssue] = []

        soup = BeautifulSoup(html_content, "html.parser")
        div = soup.find(id=section_id)
        if not div:
            for h in soup.find_all(["h2", "h3", "h4", "h5"]):
                if section_id.replace("-", " ") in h.get_text():
                    div = h.find_parent("div")
                    break

        if not div:
            return features, issues

        sec_label = section_id.replace("-", " ")
        tables = div.find_all("table")
        seen_keys: set = set()

        for idx, table in enumerate(tables):
            cap = table.find("caption")
            raw_title = cap.get_text(" ", strip=True) if cap else f"SUA {idx+1}"
            clean_title = re.sub(r"\s*\(\d+\)", "", raw_title).strip()
            desig_code = clean_title.split(" - ")[0].strip().replace(" ", "_")
            nat_key = f"SUA/{desig_code}"
            if nat_key in seen_keys:
                nat_key = f"SUA/{desig_code}_{idx+1}"
            seen_keys.add(nat_key)

            lateral_raw = ""
            vertical_raw = ""
            restriction_raw = ""
            activity_raw = ""

            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) >= 4 and not any("lateral limits" in c.lower() for c in cells):
                    lateral_raw = cells[0]
                    vertical_raw = cells[1]
                    restriction_raw = cells[2]
                    activity_raw = cells[3]
                    break

            if not lateral_raw:
                continue

            upper_lim, lower_lim = self._split_vertical_limits(vertical_raw)

            fields: Dict[str, FeatureField] = {
                "designation": FeatureField(
                    value=clean_title, unit=None, source=self.publisher_name,
                    section=sec_label, effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=raw_title
                ),
                "lateralLimitsRaw": FeatureField(
                    value=lateral_raw, unit=None, source=self.publisher_name,
                    section=sec_label, effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=lateral_raw
                ),
            }

            if restriction_raw:
                fields["restrictionType"] = FeatureField(
                    value=restriction_raw, unit=None, source=self.publisher_name,
                    section=sec_label, effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=restriction_raw
                )
            if activity_raw:
                fields["timeOfActivity"] = FeatureField(
                    value=activity_raw, unit=None, source=self.publisher_name,
                    section=sec_label, effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=activity_raw
                )
            if upper_lim:
                fields["upperLimit"] = FeatureField(
                    value=upper_lim, unit=upper_lim.get("unit"), source=self.publisher_name,
                    section=sec_label, effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=vertical_raw
                )
            if lower_lim:
                fields["lowerLimit"] = FeatureField(
                    value=lower_lim, unit=lower_lim.get("unit"), source=self.publisher_name,
                    section=sec_label, effective_date=self.effective_date,
                    retrieved_at=retrieved_at, source_url=source_url,
                    parser_version=PARSER_VERSION, raw_text=vertical_raw
                )

            # ABSOLUTE PROHIBITION: Geometry MUST be null
            content_hash = compute_content_hash(fields, None)

            feat = Feature(
                feature_type=FeatureType.SPECIAL_USE_AREA,
                natural_key=nat_key,
                parent_key=None,
                publication_id=self.cycle_id,
                effective_from=self.effective_date,
                effective_to=None,
                source_class=SourceClass.AIP,
                temporality=Temporality.PERMANENT,
                geometry=None,
                content_hash=content_hash,
                fields=fields
            )
            features.append(feat)

        return features, issues
