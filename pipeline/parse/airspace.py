"""
Declarative parser for Airspace and Special-Use Airspace (ATTRIBUTES ONLY):
- ENR 2.1 Air Traffic Services Airspace (FIR, UIR, CTA, TMA, CTR)
- ENR 5.1 Prohibited, Restricted and Danger Areas
- ENR 5.2 Military Exercise and Training Areas

ABSOLUTE PROHIBITION: Geometry MUST be null for all airspace and specialUseArea features.
Zero polygon synthesis or bounding box approximation.

Natural keys: `ASP/{designation}` and `SUA/{designator}`, where the key text is
normalised per `natural_key_normalisation` in sections.yaml (typographic dashes and
underscores to spaces/hyphens, an interior slash to a hyphen so TRA/TSA11 stays one
segment, accents folded, upper-cased). The `designation` field always keeps the
verbatim published text — the key is a machine identifier, the field is provenance.

Tables that publish no airspace (legend keys, numbered procedure sections) are
rejected by `non_data_row_rules` before any feature is built, counted, and reported
in one line per section; they are not warned about one by one.
"""
import re
import unicodedata
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

class AirspaceParser:
    def __init__(self, cycle_id: str, effective_date: str):
        self.cycle_id = cycle_id
        self.effective_date = effective_date

        crawler_cfg = load_crawler_config()
        self.publisher_name = require_str(require(crawler_cfg, "publisher", "crawler.yaml"), "name", "crawler.yaml publisher")

        sections_cfg = load_sections_config()
        key_cfg = require(sections_cfg, "natural_key_normalisation", "sections.yaml")
        row_cfg = require(sections_cfg, "non_data_row_rules", "sections.yaml")

        self.key_replacements: Dict[str, str] = require(key_cfg, "replacements", "sections.yaml natural_key_normalisation")
        self.re_whitespace = re.compile(require_str(key_cfg, "whitespace_pattern", "sections.yaml natural_key_normalisation"))
        # A single space is a legitimate value here, so plain require (require_str
        # rejects whitespace-only strings).
        self.whitespace_replacement = str(require(key_cfg, "whitespace_replacement", "sections.yaml natural_key_normalisation"))
        self.code_separator = require_str(key_cfg, "code_separator", "sections.yaml natural_key_normalisation")
        self.re_key_charset = re.compile(require_str(key_cfg, "key_charset", "sections.yaml natural_key_normalisation"))
        self.duplicate_suffix_template = require_str(key_cfg, "duplicate_suffix_template", "sections.yaml natural_key_normalisation")

        self.re_non_data_designation = re.compile(require_str(row_cfg, "designation_pattern", "sections.yaml non_data_row_rules"))
        self.min_designation_length = int(require(row_cfg, "min_designation_length", "sections.yaml non_data_row_rules"))
        self.max_designation_length = int(require(row_cfg, "max_designation_length", "sections.yaml non_data_row_rules"))
        self.min_value_length = int(require(row_cfg, "min_value_length", "sections.yaml non_data_row_rules"))

    # -- designation -> key ----------------------------------------------
    def normalise_designation(self, text: str) -> str:
        """Typographic AIP text to a key-safe string; the field keeps the original."""
        out = text
        for source, target in self.key_replacements.items():
            out = out.replace(source, str(target))
        out = unicodedata.normalize("NFKD", out)
        out = "".join(ch for ch in out if not unicodedata.combining(ch))
        out = self.re_whitespace.sub(self.whitespace_replacement, out.upper()).strip()
        return out

    def key_code(self, normalised_designation: str) -> str:
        return normalised_designation.split(self.code_separator)[0].strip()

    def non_data_reason(self, designation: str, values: List[str]) -> Optional[str]:
        """Why this table is not an airspace, or None when it is data."""
        if designation == "":
            return "empty designation"
        if self.re_non_data_designation.match(designation):
            return "numbered section"
        if len(designation) < self.min_designation_length:
            return "designation too short"
        if len(designation) > self.max_designation_length:
            return "designation too long"
        populated = [value.strip() for value in values if value.strip() != ""]
        if populated and all(len(value) < self.min_value_length for value in populated):
            return "legend keys"
        return None

    def _emit_key(self, prefix: str, code: str, seen_keys: set) -> str:
        """Unique, charset-safe natural key for a normalised designator."""
        base = f"{prefix}/{code}"
        nat_key = base
        suffix = 2
        while nat_key in seen_keys:
            nat_key = self.duplicate_suffix_template.format(base=base, n=suffix)
            suffix += 1
        seen_keys.add(nat_key)
        return nat_key

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
        seen_keys: set = set()
        skipped: Dict[str, int] = {}

        for idx, table in enumerate(tables):
            cap = table.find("caption")
            raw_title = cap.get_text(" ", strip=True) if cap else f"AIRSPACE {idx+1}"
            designation = re.sub(r"\s*\(\d+\)", "", raw_title).strip()
            if not designation:
                continue

            normalised = self.normalise_designation(designation)
            code = self.key_code(normalised)
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

            reason = self.non_data_reason(
                normalised, [lateral_limits_raw, vertical_limits_raw, airspace_class_val]
            )
            if reason is None and not self.re_key_charset.match(code):
                reason = "unnormalisable designator"
            if reason is not None:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue

            nat_key = self._emit_key("ASP", code, seen_keys)
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

        self._report_skipped("ENR 2.1", skipped)
        if not features:
            issues.append(ValidationIssue(
                publication_id=self.cycle_id, severity=IssueSeverity.WARNING,
                rule_id="RULE_EMPTY_SECTION", feature_key=None, section="ENR 2.1",
                message="Section ENR 2.1 produced no airspace features",
                raw_snippet="",
            ))
        return features, issues

    @staticmethod
    def _report_skipped(section_label: str, skipped: Dict[str, int]) -> None:
        """One line per section instead of one warning per non-data table."""
        total = sum(skipped.values())
        if total == 0:
            return
        detail = ", ".join(f"{count} {reason}" for reason, count in sorted(skipped.items()))
        noun = "row" if total == 1 else "rows"
        print(f"[Parse] {section_label}: skipped {total} non-data {noun} ({detail})")

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
        skipped: Dict[str, int] = {}

        for idx, table in enumerate(tables):
            cap = table.find("caption")
            raw_title = cap.get_text(" ", strip=True) if cap else f"SUA {idx+1}"
            clean_title = re.sub(r"\s*\(\d+\)", "", raw_title).strip()
            normalised = self.normalise_designation(clean_title)
            code = self.key_code(normalised)

            lateral_raw = ""
            vertical_raw = ""
            restriction_raw = ""
            activity_raw = ""
            row_cells: List[str] = []

            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) >= 4 and not any("lateral limits" in c.lower() for c in cells):
                    lateral_raw = cells[0]
                    vertical_raw = cells[1]
                    restriction_raw = cells[2]
                    activity_raw = cells[3]
                    row_cells = cells
                    break

            # Reject legend / procedure tables before deciding they carry no limits:
            # a skip is a fact about the table, not about a missing value.
            reason = self.non_data_reason(normalised, row_cells)
            if reason is None and not self.re_key_charset.match(code):
                reason = "unnormalisable designator"
            if reason is not None:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue

            if not lateral_raw:
                continue

            nat_key = self._emit_key("SUA", code, seen_keys)

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

        self._report_skipped(sec_label, skipped)
        if not features:
            issues.append(ValidationIssue(
                publication_id=self.cycle_id, severity=IssueSeverity.WARNING,
                rule_id="RULE_EMPTY_SECTION", feature_key=None, section=sec_label,
                message=f"Section {sec_label} produced no special-use airspace features",
                raw_snippet="",
            ))
        return features, issues
