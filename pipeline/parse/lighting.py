"""
AD 2.14 Approach and Runway Lighting.

One table per runway direction: a single-cell caption ("RWY 01") followed by ragged
rows of key/value pairs. The section labels ("Approach lighting system", "VASIS",
"Runway threshold lights", "Touchdown zone lights", ...) open a block, and the labels
inside it ("Type:", "Length:", "Intensity:", "Colour:") mean different things in
different blocks — so the current section decides which field a label fills. Every
token, label mapping and pattern lives in `config/sections.yaml`; the Python holds no
lighting vocabulary.

Emitted as `operationalInfo` (the FeatureType enum is not extended) with the natural
key `{ICAO}/LIGHTING/{designator}` and `parentKey` `{ICAO}/RWY/{designator}`, so the
feature links to the runway direction it describes. `lightingRawText` carries the
caption and its cells verbatim, which is both the fallback and the audit trail when a
label is missing or a block is published empty.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from pipeline.config import load_crawler_config, load_sections_config, require, require_str
from pipeline.models.feature import (
    Feature, FeatureType, SourceClass, Temporality,
    FeatureField
)
from pipeline.models.validation_issue import ValidationIssue, IssueSeverity
from pipeline.utils.hash_utils import compute_content_hash

PARSER_VERSION = "1.0"


class LightingParser:
    def __init__(self, cycle_id: str, effective_date: str):
        self.cycle_id = cycle_id
        self.effective_date = effective_date

        crawler_cfg = load_crawler_config()
        self.publisher_name = require_str(
            require(crawler_cfg, "publisher", "crawler.yaml"), "name", "crawler.yaml publisher"
        )

        sections_cfg = load_sections_config()
        self.spec = next((s for s in sections_cfg.get("sections", []) if s["id"] == "AD-2.14"), {})
        cfg = require(self.spec, "lighting", "sections.yaml AD-2.14")

        self.re_caption = re.compile(require_str(cfg, "caption_pattern", "sections.yaml AD-2.14 lighting"))
        self.section_tokens = [t.lower() for t in require(cfg, "section_tokens", "sections.yaml AD-2.14 lighting")]
        self.section_value_fields: Dict[str, str] = require(cfg, "section_value_fields", "sections.yaml AD-2.14 lighting")
        self.section_label_fields: Dict[str, str] = require(cfg, "section_label_fields", "sections.yaml AD-2.14 lighting")
        self.global_label_fields: Dict[str, str] = require(cfg, "global_label_fields", "sections.yaml AD-2.14 lighting")
        self.measured_fields = set(require(cfg, "measured_fields", "sections.yaml AD-2.14 lighting"))
        self.re_length = re.compile(require_str(cfg, "length_pattern", "sections.yaml AD-2.14 lighting"))

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _normalise(text: str) -> str:
        return re.sub(r"\s+", " ", text.replace("\xa0", " ").replace("\u2009", " ")).strip()

    def _label_key(self, text: str) -> Optional[str]:
        """'MEHT :' / 'Type:' -> 'meht' / 'type'; a bare word is not a label."""
        stripped = self._normalise(text)
        if not stripped.endswith(":"):
            return None
        return stripped[:-1].strip().lower()

    def _field(self, value: Any, unit: Optional[str], raw_text: str, section: str,
               source_url: str, retrieved_at: str) -> FeatureField:
        return FeatureField(
            value=value, unit=unit, source=self.publisher_name, section=section,
            effective_date=self.effective_date, retrieved_at=retrieved_at,
            source_url=source_url, parser_version=PARSER_VERSION, raw_text=raw_text,
        )

    def _measured(self, field_name: str, raw_text: str) -> Tuple[Any, Optional[str]]:
        if field_name in self.measured_fields:
            match = self.re_length.match(self._normalise(raw_text))
            if match:
                digits = re.sub(r"\s", "", match.group(1))
                try:
                    return float(digits), match.group(2)
                except ValueError:
                    pass
        return raw_text, None

    # -- AD 2.14 ---------------------------------------------------------
    def parse_airport_lighting(
        self,
        icao: str,
        html_content: str,
        document_filename: str,
        source_url: str,
        retrieved_at: str
    ) -> Tuple[List[Feature], List[ValidationIssue]]:
        features: List[Feature] = []
        issues: List[ValidationIssue] = []
        section_label = f"{icao} AD 2.14"

        soup = BeautifulSoup(html_content, "html.parser")
        anchor = require_str(self.spec, "anchor_template", "sections.yaml AD-2.14").format(icao=icao)
        div = soup.find(id=anchor)
        if div is None:
            for h in soup.find_all(["h2", "h3", "h4", "h5"]):
                if "AD 2.14" in h.get_text():
                    div = h.find_parent("div")
                    break
        if div is None:
            issues.append(ValidationIssue(
                publication_id=self.cycle_id, severity=IssueSeverity.WARNING,
                rule_id="RULE_MISSING_SECTION", feature_key=icao, section=section_label,
                message=f"Missing section AD 2.14 Approach and Runway Lighting in {document_filename}",
                raw_snippet="",
            ))
            return features, issues

        for table in div.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            caption_cells = [c.get_text(" ", strip=True) for c in rows[0].find_all(["td", "th"])]
            caption = self._normalise(caption_cells[0]) if caption_cells else ""
            match = self.re_caption.match(caption) if caption else None
            if match is None:
                issues.append(ValidationIssue(
                    publication_id=self.cycle_id, severity=IssueSeverity.WARNING,
                    rule_id="RULE_LIGHTING_DESIGNATOR", feature_key=icao, section=section_label,
                    message=f"Unparseable lighting table caption '{caption}' in {icao} AD 2.14",
                    raw_snippet=caption,
                ))
                continue

            designator = match.group("designator")
            raw_lines = [caption]
            parsed: Dict[str, str] = {}
            section: Optional[str] = None

            for row in rows[1:]:
                cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                raw_lines.append(" | ".join(cell for cell in cells if cell != ""))
                index = 0
                while index < len(cells):
                    text = self._normalise(cells[index])
                    if text == "":
                        index += 1
                        continue

                    lowered = text.lower()
                    if lowered in self.section_tokens:
                        section = lowered
                        value_field = self.section_value_fields.get(section)
                        if value_field is not None:
                            following = self._normalise(cells[index + 1]) if index + 1 < len(cells) else ""
                            if following != "" and self._label_key(following) is None:
                                parsed.setdefault(value_field, following)
                                index += 2
                                continue
                        index += 1
                        continue

                    label = self._label_key(text)
                    if label is not None:
                        field_name = None
                        if section is not None:
                            field_name = self.section_label_fields.get(f"{section}|{label}")
                        if field_name is None:
                            field_name = self.global_label_fields.get(label)
                        if field_name is not None:
                            following = ""
                            for probe in range(index + 1, len(cells)):
                                candidate = self._normalise(cells[probe])
                                if candidate == "":
                                    continue
                                following = candidate
                                break
                            if following != "" and self._label_key(following) is None:
                                parsed.setdefault(field_name, following)
                                index += 2
                                continue
                        index += 1
                        continue

                    index += 1

            fields: Dict[str, FeatureField] = {}
            for field_name, raw_value in parsed.items():
                value, unit = self._measured(field_name, raw_value)
                fields[field_name] = self._field(value, unit, raw_value, section_label, source_url, retrieved_at)
            raw_text = "\n".join(line for line in raw_lines if line != "")
            fields["lightingRawText"] = self._field(raw_text, None, raw_text, section_label, source_url, retrieved_at)

            if len(fields) == 1:  # only the raw text fallback
                issues.append(ValidationIssue(
                    publication_id=self.cycle_id, severity=IssueSeverity.WARNING,
                    rule_id="RULE_LIGHTING_UNPARSED", feature_key=f"{icao}/LIGHTING/{designator}",
                    section=section_label,
                    message=f"No lighting key/value pairs could be read for {icao} runway {designator}",
                    raw_snippet=raw_text[:200],
                ))

            features.append(Feature(
                feature_type=FeatureType.OPERATIONAL_INFO,
                natural_key=f"{icao}/LIGHTING/{designator}",
                parent_key=f"{icao}/RWY/{designator}",
                publication_id=self.cycle_id,
                effective_from=self.effective_date,
                effective_to=None,
                source_class=SourceClass.AIP,
                temporality=Temporality.PERMANENT,
                geometry=None,
                content_hash=compute_content_hash(fields, None),
                fields=fields,
            ))

        return features, issues
