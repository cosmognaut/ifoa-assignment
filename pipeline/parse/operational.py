"""
AD 2.20 (Local Aerodrome Regulations) and AD 2.21 (Noise Abatement Procedures).

Both sections are prose with, at most, incidental tables. The deliverable is the
verbatim text with field-level provenance — not a structure invented from it. Only
three facts are derived and each one is guarded by a config-declared signal:

  * noise-relevant ICAO aircraft types — only when a table exists and every non-empty
    data cell is an aircraft type code;
  * contact emails — only when the section actually contains email-like strings;
  * pprRequired — only when a standalone `PPR` token *and* a "prior permission"
    phrase both appear. Absent either, the answer is false rather than a guess.

One feature per airport per section, geometry null:
  {ICAO}/OPS/NOISE       {ICAO}/OPS/LOCAL-REGS
"""
import re
from typing import List, Optional, Tuple, Dict, Any

from bs4 import BeautifulSoup

from pipeline.config import load_crawler_config, load_sections_config, require, require_str
from pipeline.models.feature import (
    Feature, FeatureType, SourceClass, Temporality,
    FeatureField
)
from pipeline.models.validation_issue import ValidationIssue, IssueSeverity
from pipeline.utils.hash_utils import compute_content_hash

PARSER_VERSION = "1.0"


class OperationalParser:
    def __init__(self, cycle_id: str, effective_date: str):
        self.cycle_id = cycle_id
        self.effective_date = effective_date

        crawler_cfg = load_crawler_config()
        self.publisher_name = require_str(
            require(crawler_cfg, "publisher", "crawler.yaml"), "name", "crawler.yaml publisher"
        )

        sections_cfg = load_sections_config()
        sections_list = sections_cfg.get("sections", [])
        self.spec_noise = next((s for s in sections_list if s["id"] == "AD-2.21"), {})
        self.spec_local = next((s for s in sections_list if s["id"] == "AD-2.20"), {})

        regex_cfg = require(sections_cfg, "converters_regex", "sections.yaml")
        self.re_aircraft_type = re.compile(
            require_str(regex_cfg, "aircraft_type_code", "sections.yaml converters_regex")
        )
        self.re_email = re.compile(require_str(regex_cfg, "email", "sections.yaml converters_regex"))
        self.re_ppr_token = re.compile(
            require_str(regex_cfg, "ppr_token", "sections.yaml converters_regex")
        )
        self.re_ppr_phrase = re.compile(
            require_str(regex_cfg, "ppr_phrase", "sections.yaml converters_regex")
        )

    # -- helpers ---------------------------------------------------------
    def _section_div(self, soup: BeautifulSoup, spec: Dict[str, Any], icao: str, heading: str):
        anchor = require_str(spec, "anchor_template", "sections.yaml").format(icao=icao)
        div = soup.find(id=anchor)
        if div:
            return div
        for h in soup.find_all(["h2", "h3", "h4", "h5"]):
            if heading in h.get_text():
                return h.find_parent("div")
        return None

    def _make_field(self, value: Any, unit: Optional[str], raw_text: str, section: str,
                    source_url: str, retrieved_at: str) -> FeatureField:
        return FeatureField(
            value=value,
            unit=unit,
            source=self.publisher_name,
            section=section,
            effective_date=self.effective_date,
            retrieved_at=retrieved_at,
            source_url=source_url,
            parser_version=PARSER_VERSION,
            raw_text=raw_text,
        )

    def _feature(self, natural_key: str, parent_key: str, fields: Dict[str, FeatureField]) -> Feature:
        return Feature(
            feature_type=FeatureType.OPERATIONAL_INFO,
            natural_key=natural_key,
            parent_key=parent_key,
            publication_id=self.cycle_id,
            effective_from=self.effective_date,
            effective_to=None,
            source_class=SourceClass.AIP,
            temporality=Temporality.PERMANENT,
            geometry=None,
            content_hash=compute_content_hash(fields, None),
            fields=fields,
        )

    def _missing_section(self, icao: str, section: str, filename: str) -> ValidationIssue:
        return ValidationIssue(
            publication_id=self.cycle_id,
            severity=IssueSeverity.WARNING,
            rule_id="RULE_MISSING_SECTION",
            feature_key=icao,
            section=section,
            message=f"Missing section {section} in {filename}",
            raw_snippet="",
        )

    # -- AD 2.21 ---------------------------------------------------------
    def parse_noise(
        self,
        icao: str,
        html_content: str,
        document_filename: str,
        source_url: str,
        retrieved_at: str
    ) -> Tuple[Optional[Feature], List[ValidationIssue]]:
        issues: List[ValidationIssue] = []
        section_label = f"{icao} AD 2.21"
        soup = BeautifulSoup(html_content, "html.parser")
        div = self._section_div(soup, self.spec_noise, icao, "AD 2.21")
        if div is None:
            return None, [self._missing_section(icao, section_label, document_filename)]

        paragraphs = [p.get_text(" ", strip=True) for p in div.find_all("p")]
        text = "\n\n".join(p for p in paragraphs if p)

        fields: Dict[str, FeatureField] = {}

        table = div.find("table")
        type_codes: List[str] = []
        table_rejection: Optional[str] = None
        if table is None:
            table_rejection = "no table in the section"
        else:
            cells = [td.get_text(" ", strip=True) for td in table.find_all("td")]
            for cell in (c for c in cells if c):
                if not self.re_aircraft_type.match(cell):
                    table_rejection = f"cell '{cell}' is not an ICAO aircraft type code"
                    break
                type_codes.append(cell)
            if table_rejection is None and not type_codes:
                table_rejection = "table has no non-empty data cells"

        if table_rejection is None:
            fields["noiseRelevantAircraftTypes"] = self._make_field(
                type_codes, None, ", ".join(type_codes), section_label, source_url, retrieved_at
            )
        else:
            issues.append(ValidationIssue(
                publication_id=self.cycle_id,
                severity=IssueSeverity.WARNING,
                rule_id="RULE_NOISE_AIRCRAFT_TYPES",
                feature_key=f"{icao}/OPS/NOISE",
                section=section_label,
                message=f"Noise-relevant aircraft types not captured for {icao}: {table_rejection}",
                raw_snippet=table.get_text(" ", strip=True)[:200] if table is not None else "",
            ))

        if text:
            fields["noiseAbatementText"] = self._make_field(
                text, None, text, section_label, source_url, retrieved_at
            )

        if not fields:
            return None, issues
        return self._feature(f"{icao}/OPS/NOISE", icao, fields), issues

    # -- AD 2.20 ---------------------------------------------------------
    def parse_local_regulations(
        self,
        icao: str,
        html_content: str,
        document_filename: str,
        source_url: str,
        retrieved_at: str
    ) -> Tuple[Optional[Feature], List[ValidationIssue]]:
        issues: List[ValidationIssue] = []
        section_label = f"{icao} AD 2.20"
        soup = BeautifulSoup(html_content, "html.parser")
        div = self._section_div(soup, self.spec_local, icao, "AD 2.20")
        if div is None:
            return None, [self._missing_section(icao, section_label, document_filename)]

        blocks: List[str] = []
        for node in div.find_all(["p", "table"]):
            if node.name == "p":
                line = node.get_text(" ", strip=True)
                if line:
                    blocks.append(line)
                continue
            for row in node.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                line = " | ".join(c for c in cells if c)
                if line:
                    blocks.append(line)
        text = "\n\n".join(blocks)

        fields: Dict[str, FeatureField] = {}
        if text:
            fields["localRegulationsText"] = self._make_field(
                text, None, text, section_label, source_url, retrieved_at
            )

        emails: List[str] = []
        for match in self.re_email.finditer(text):
            value = match.group(0)
            if value not in emails:
                emails.append(value)
        if emails:
            fields["regulationContactEmails"] = self._make_field(
                emails, None, ", ".join(emails), section_label, source_url, retrieved_at
            )

        ppr_token = self.re_ppr_token.search(text)
        ppr_phrase = self.re_ppr_phrase.search(text)
        ppr_required = bool(ppr_token and ppr_phrase)
        snippets = [m.group(0) for m in (ppr_token, ppr_phrase) if m is not None]
        fields["pprRequired"] = self._make_field(
            ppr_required, None, " · ".join(snippets), section_label, source_url, retrieved_at
        )

        if not fields:
            return None, issues
        return self._feature(f"{icao}/OPS/LOCAL-REGS", icao, fields), issues
