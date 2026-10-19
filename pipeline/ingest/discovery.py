"""
Inspect the publisher's live site to discover which cycles currently exist
(previous / current / preview) and which documents must be downloaded for the
configured airports and sections.

No publisher-specific and no section-specific facts are permitted in this
module:

  * site layout, document names and cover-page label text -> config/crawler.yaml,
    under `crawler.publisher`
  * which documents to ingest, and which section ids they contain ->
    config/sections.yaml
  * which airports to expand per-airport sections for -> config/airports.yaml

If a value from those files is missing, this module raises ConfigError rather
than falling back to a literal. See pipeline/config.py for the rationale.
"""

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from pipeline.config import (
    ConfigError,
    load_airports_config,
    load_crawler_config,
    load_sections_config,
    require,
    require_str,
)
from pipeline.models.publication import SourceKind

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
}

VALID_SCOPES = ("global", "per_airport")


def parse_aip_date(text: str) -> Optional[str]:
    """Parse text like '01 OCT 2026' into ISO '2026-10-01'"""
    if not text:
        return None
    m = re.search(r"(\d{1,2})\s+([A-Z]{3})\s+(\d{4})", text.upper())
    if not m:
        return None
    day, mon, year = m.groups()
    if mon not in MONTH_MAP:
        return None
    return f"{year}-{MONTH_MAP[mon]:02d}-{int(day):02d}"


class AIPDiscovery:
    """This class models the discovery workflow for a particular AIP (info publication)"""

    def __init__(
        self,
        crawler_cfg_path: str = "config/crawler.yaml",
        airports_cfg_path: str = "config/airports.yaml",
        sections_cfg_path: str = "config/sections.yaml"
    ):
        """
        Reads the config for crawler/airports and the sections.
        """
        self.crawler_cfg = load_crawler_config(crawler_cfg_path)
        self.airports_cfg = load_airports_config(airports_cfg_path)
        sections_doc = load_sections_config(sections_cfg_path)

        self.publisher = require(self.crawler_cfg, "publisher", crawler_cfg_path)
        self.base_url = require_str(self.crawler_cfg, "base_product_url", crawler_cfg_path)
        self.user_agent = require_str(self.crawler_cfg, "user_agent_format", crawler_cfg_path)

        self.target_icaos = self._load_target_icaos(airports_cfg_path)

        raw_sections = sections_doc.get("sections")
        if not isinstance(raw_sections, list) or not raw_sections:
            raise ConfigError(f"{sections_cfg_path}: 'sections' must be a non-empty list")
        self.section_defs: List[Dict[str, Any]] = raw_sections
        self._validate_sections(sections_cfg_path)

        if not self.target_icaos and any(s.get("scope") == "per_airport" for s in self.section_defs):
            raise ConfigError(
                f"{airports_cfg_path}: 'target_airports' is empty but {sections_cfg_path} "
                f"declares per_airport sections"
            )

    # ------------------------------------------------------------------ config

    def _load_target_icaos(self, context: str) -> List[str]:
        targets = self.airports_cfg.get("target_airports")
        if not isinstance(targets, list):
            raise ConfigError(f"{context}: 'target_airports' must be a list")
        icaos: List[str] = []
        for item in targets:
            icao = require_str(item, "icao", f"{context} target_airports[]")
            if icao in icaos:
                raise ConfigError(f"{context}: duplicate target airport '{icao}'")
            icaos.append(icao)
        return icaos

    def _validate_sections(self, context: str) -> None:
        """Fail loudly on sections.yaml that discovery cannot honour."""
        seen_ids: set = set()
        for section in self.section_defs:
            if not isinstance(section, dict):
                raise ConfigError(f"{context}: each entry under 'sections' must be a mapping")
            section_id = require_str(section, "id", f"{context} sections[]")
            if section_id in seen_ids:
                raise ConfigError(f"{context}: duplicate section id '{section_id}'")
            seen_ids.add(section_id)
            require_str(section, "name", f"{context} sections[{section_id}]")
            require_str(section, "category", f"{context} sections[{section_id}]")

            scope = section.get("scope")
            if scope not in VALID_SCOPES:
                raise ConfigError(
                    f"{context} sections[{section_id}]: 'scope' must be one of {VALID_SCOPES}, got {scope!r}"
                )

            self._filename_template(section, context)
            self._document_id_template(section, context)

            if scope == "per_airport":
                template = self._filename_template(section, context)
                if "{icao}" not in template:
                    raise ConfigError(
                        f"{context} sections[{section_id}]: scope is 'per_airport' so its "
                        f"filename template must contain {{icao}}, got {template!r}"
                    )
            else:
                if "{icao}" in self._filename_template(section, context):
                    raise ConfigError(
                        f"{context} sections[{section_id}]: scope is 'global' but its filename "
                        f"template contains {{icao}}"
                    )

    @staticmethod
    def _filename_template(section: Dict[str, Any], context: str) -> str:
        """A section's filename declaration, from either supported key."""
        template = section.get("file_template") or section.get("file_pattern")
        if not isinstance(template, str) or not template.strip():
            raise ConfigError(
                f"{context} sections[{section.get('id')}]: needs 'file_template' "
                f"(per-airport) or 'file_pattern' (global)"
            )
        return template

    @staticmethod
    def _document_id_template(section: Dict[str, Any], context: str) -> str:
        """Identity of the file a section lives in. Defaults to the section id."""
        document_id = section.get("document_id")
        if document_id is None:
            return require_str(section, "id", context)
        if not isinstance(document_id, str) or not document_id.strip():
            raise ConfigError(
                f"{context} sections[{section.get('id')}]: 'document_id' must be a non-empty string"
            )
        return document_id

    def resolve_documents(self) -> List[Dict[str, Any]]:
        """
        Expand sections.yaml into the concrete list of documents to fetch.

        Pure function of configuration: no network, no HTML. Per-airport
        sections are expanded once per entry in target_airports; sections that
        share a `document_id` collapse into a single document, because they are
        slices of one file on disk.

        Returns records of the shape:
            {
              "document_id":       "<document id, e.g. AD-2.{icao} expanded>",
              "document_filename": "<file name from file_template/file_pattern>",
              "category":        "AD",
              "name":            "Aerodrome Location Indicator and Name",
              "icao":            "EBBR" | None,
              "section_ids":     ["AD-2.1", "AD-2.2", ...],
            }
        """
        documents: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []

        for section in self.section_defs:
            scope = section["scope"]
            icaos: List[Optional[str]] = list(self.target_icaos) if scope == "per_airport" else [None]

            for icao in icaos:
                fmt = {"icao": icao} if icao else {}
                try:
                    filename = self._filename_template(section, "sections.yaml").format(**fmt)
                    document_id = self._document_id_template(section, "sections.yaml").format(**fmt)
                except KeyError as exc:
                    raise ConfigError(
                        f"sections.yaml sections[{section['id']}]: unresolvable placeholder {exc}"
                    ) from exc

                if document_id not in documents:
                    documents[document_id] = {
                        "document_id": document_id,
                        "document_filename": filename,
                        "category": section["category"],
                        "name": section["name"],
                        "icao": icao,
                        "section_ids": [],
                    }
                    order.append(document_id)
                else:
                    existing = documents[document_id]["document_filename"]
                    if existing != filename:
                        raise ConfigError(
                            f"sections.yaml: document_id '{document_id}' is claimed by both "
                            f"'{existing}' and '{filename}'"
                        )

                documents[document_id]["section_ids"].append(section["id"])

        return [documents[key] for key in order]

    # ---------------------------------------------------------------- fetching

    def _build_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": require_str(self.publisher, "accept_header", "crawler.yaml publisher"),
        }

    def _open_client(self, client: Optional[httpx.Client]) -> Tuple[httpx.Client, bool]:
        if client is not None:
            return client, False
        timeout = float(require(self.crawler_cfg, "timeout_seconds", "crawler.yaml"))
        return httpx.Client(timeout=timeout, headers=self._build_headers()), True

    def discover_cycles(self, client: Optional[httpx.Client] = None) -> List[Dict[str, Any]]:
        """
        Discovers previous, current, and preview cycles from the product index page
        and their respective cover pages.

        Plan:
        First, we fetch the product index. Then, for each folder declared in
        crawler.publisher.cycle_folders, we request its cover document and extract:
            - Amendment ID    (crawler.publisher.labels.amendment_id_pattern)
            - Effective date  (crawler.publisher.labels.effective_date_pattern)
            - Publication date(crawler.publisher.labels.publication_date_pattern)
            - Menu URL: constructed from eaip_subdir + menu_document
        We also enforce the requirement that cycle_id = effective_date (ISO). If two
        editions share an effective date, we append the role to prevent collision.
        """
        cover_template = require_str(self.publisher, "cycle_cover_path_template", "crawler.yaml publisher")
        html_subdir = require_str(self.publisher, "html_subdir", "crawler.yaml publisher")
        cover_document = require_str(self.publisher, "cover_document", "crawler.yaml publisher")
        menu_document = require_str(self.publisher, "menu_document", "crawler.yaml publisher")
        eaip_subdir = require_str(self.publisher, "eaip_subdir", "crawler.yaml publisher")
        fallback_template = require_str(
            self.publisher, "amendment_id_fallback_template", "crawler.yaml publisher"
        )

        labels = require(self.publisher, "labels", "crawler.yaml publisher")
        amdt_pattern = require_str(labels, "amendment_id_pattern", "crawler.yaml publisher.labels")
        eff_pattern = require_str(labels, "effective_date_pattern", "crawler.yaml publisher.labels")
        pub_pattern = require_str(labels, "publication_date_pattern", "crawler.yaml publisher.labels")

        cycle_folders = require(self.publisher, "cycle_folders", "crawler.yaml publisher")
        if not isinstance(cycle_folders, dict) or not cycle_folders:
            raise ConfigError("crawler.yaml publisher: 'cycle_folders' must be a non-empty mapping")

        client, close_client = self._open_client(client)
        discovered: List[Dict[str, Any]] = []
        try:
            resp = client.get(self.base_url)
            if resp.status_code != 200:
                return discovered

            seen_cycle_ids: set = set()
            for role, folder in cycle_folders.items():
                rel_cover = cover_template.format(
                    folder=folder,
                    html_subdir=html_subdir,
                    cover_document=cover_document,
                )
                cover_url = urljoin(self.base_url, rel_cover)
                try:
                    cover_resp = client.get(cover_url)
                except httpx.HTTPError:
                    continue
                if cover_resp.status_code != 200:
                    continue

                text = BeautifulSoup(cover_resp.text, "html.parser").get_text(" ", strip=True)

                amdt_match = re.search(amdt_pattern, text)
                amdt_id = amdt_match.group(1) if amdt_match else fallback_template.format(folder=folder)

                eff_match = re.search(eff_pattern, text)
                effective_date = parse_aip_date(eff_match.group(1)) if eff_match else None

                pub_match = re.search(pub_pattern, text)
                pub_date = parse_aip_date(pub_match.group(1)) if pub_match else None

                if not effective_date:
                    continue

                cycle_id = effective_date
                # if two cycles share identical effective date (e.g. preliminary vs
                # final edition), keep cycle_id deterministic as ISO date, appending
                # the role only if a collision occurs
                if cycle_id in seen_cycle_ids:
                    cycle_id = f"{effective_date}-{role}"
                seen_cycle_ids.add(cycle_id)

                base_eaip = urljoin(cover_url, f"{eaip_subdir}/")
                menu_url = urljoin(base_eaip, menu_document)

                discovered.append({
                    "cycle_id": cycle_id,
                    "role": role,
                    "effective_date": effective_date,
                    "amendment_id": amdt_id,
                    "publication_date": pub_date,
                    "cover_url": cover_url,
                    "menu_url": menu_url,
                    "base_eaip_url": base_eaip,
                    "source_kind": SourceKind.LIVE.value,
                })
        finally:
            if close_client:
                client.close()

        return discovered

    def discover_sections_for_cycle(
        self,
        cycle_info: Dict[str, Any],
        client: Optional[httpx.Client] = None
    ) -> List[Dict[str, Any]]:
        """
        Parses the cycle's menu document and returns the list of target documents
        that both match sections.yaml and are actually published in this cycle.

        Plan:
        First, we download and parse the cycle's table of contents. Then we gather
        every HTML link and anchor across the whole AIP. Then we intersect that
        against resolve_documents(), which is derived purely from config. A section
        whose document is absent from the menu is not fetched.
        """
        client, close_client = self._open_client(client)
        sections_to_fetch: List[Dict[str, Any]] = []
        menu_url = cycle_info["menu_url"]
        base_eaip = cycle_info["base_eaip_url"]

        try:
            resp = client.get(menu_url)
            if resp.status_code != 200:
                return sections_to_fetch

            soup = BeautifulSoup(resp.text, "html.parser")
            discovered_links: Dict[str, List[Dict[str, str]]] = {}

            for a in soup.find_all("a"):
                href = a.get("href", "")
                text = a.get_text(" ", strip=True)
                if not href or href.startswith("#") or href.startswith(".."):
                    continue
                parts = href.split("#")
                doc_file = parts[0]
                anchor = parts[1] if len(parts) > 1 else ""
                discovered_links.setdefault(doc_file, []).append(
                    {"anchor": anchor, "title": text, "href": href}
                )

            for document in self.resolve_documents():
                filename = document["document_filename"]
                if filename not in discovered_links:
                    continue

                record = {
                    "document_filename": filename,
                    "url": urljoin(base_eaip, filename),
                    "category": document["category"],
                    "section_id": document["document_id"],
                    "section_ids": document["section_ids"],
                    "name": document["name"],
                    "anchors": discovered_links[filename],
                }
                if document["icao"]:
                    record["icao"] = document["icao"]
                sections_to_fetch.append(record)

        finally:
            if close_client:
                client.close()

        return sections_to_fetch
