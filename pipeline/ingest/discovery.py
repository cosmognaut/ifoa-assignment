"""
Inspect the publisher's live site to dynamically discover what live sites currently exist (previous, current, preview) and what documents must be downloaded for the configured airports and en-route sections.
"""

import re
import yaml
import httpx
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from typing import List, Dict, Any, Optional
from pipeline.models.publication import Publication, PublicationStatus, SourceKind

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
}


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
        Reads the config for crawler/airprts and the sections
        """
        with open(crawler_cfg_path, "r", encoding="utf-8") as f:
            self.crawler_cfg = yaml.safe_load(f)["crawler"]
        with open(airports_cfg_path, "r", encoding="utf-8") as f:
            self.airports_cfg = yaml.safe_load(f)
        with open(sections_cfg_path, "r", encoding="utf-8") as f:
            self.sections_cfg = yaml.safe_load(f)

        self.target_icaos = [item["icao"] for item in self.airports_cfg.get("target_airports", [])]
        self.base_url = self.crawler_cfg.get(
            "base_product_url",
            "https://ops.skeyes.be/html/belgocontrol_static/eaip/eAIP_Product/index.html"
        )

    def discover_cycles(self, client: Optional[httpx.Client] = None) -> List[Dict[str, Any]]:
        """
        Discovers previous, current, and preview cycles from eAIP_Product/index.html
        and their respective EB-cover-en-GB.html.

        Plan: 
        First, we fetch eAIP_Product/index.html. Then, we inspect the three cycle folders published by skeyes (eAIP_Previous, eAIP_Main, eAIP_Next). 
        For each folder we request its EB-cover-en-GB.html page to extract:
            - Ammendment ID, ex. "AMDT 010/2026"
            - Effective date, ex. "2026-10-01"
            - Publication date, ex. "2026-10-12"
            - Menu URL: constructing the path to EB-menu-en-GB.html
        We also enforce the requirement that cycle_id = effective_date (ISO). If two editions share an effective date, it appends the role to prevent collision.
        """
        headers = {
            "User-Agent": self.crawler_cfg.get("user_agent_format", "Mozilla/5.0").format(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        close_client = False
        if client is None:
            client = httpx.Client(timeout=float(self.crawler_cfg.get("timeout_seconds", 30.0)), headers=headers)
            close_client = True

        discovered = []
        try:
            resp = client.get(self.base_url)
            if resp.status_code != 200:
                return discovered

            # soup = BeautifulSoup(resp.text, "html.parser")
            # look for cycle columns: eAIP_Previous, eAIP_Main, eAIP_Next
            folder_targets = [
                ("previous", "../eAIP_Previous/html/EB-cover-en-GB.html", "eAIP_Previous"),
                ("current", "../eAIP_Main/html/EB-cover-en-GB.html", "eAIP_Main"),
                ("preview", "../eAIP_Next/html/EB-cover-en-GB.html", "eAIP_Next"),
            ]

            seen_cycle_ids = set()
            for role, rel_cover, folder in folder_targets:
                cover_url = urljoin(self.base_url, rel_cover)
                try:
                    cover_resp = client.get(cover_url)
                    if cover_resp.status_code == 200:
                        c_soup = BeautifulSoup(cover_resp.text, "html.parser")
                        text = c_soup.get_text(" ", strip=True)

                        amdt_m = re.search(r"((?:AIRAC\s+)?AMDT\s+\d{3}/\d{4})", text)
                        amdt_id = amdt_m.group(1) if amdt_m else f"AMDT-{folder}"

                        eff_m = re.search(r"Effective date:\s*([0-9]{1,2}\s+[A-Za-z]{3}\s+[0-9]{4})", text)
                        effective_date = parse_aip_date(eff_m.group(1)) if eff_m else None

                        pub_m = re.search(r"Publication date:\s*([0-9]{1,2}\s+[A-Za-z]{3}\s+[0-9]{4})", text)
                        pub_date = parse_aip_date(pub_m.group(1)) if pub_m else None

                        if effective_date:
                            cycle_id = effective_date
                            # if two cycles share identical effective date (e.g. preliminary vs final edition),
                            # keep cycle_id deterministic as ISO date, appending role only if collision occurs
                            if cycle_id in seen_cycle_ids:
                                cycle_id = f"{effective_date}-{role}"
                            seen_cycle_ids.add(cycle_id)

                            base_eaip = urljoin(cover_url, "eAIP/")
                            menu_url = urljoin(base_eaip, "EB-menu-en-GB.html")

                            discovered.append({
                                "cycle_id": cycle_id,
                                "role": role,
                                "effective_date": effective_date,
                                "amendment_id": amdt_id,
                                "publication_date": pub_date,
                                "cover_url": cover_url,
                                "menu_url": menu_url,
                                "base_eaip_url": base_eaip,
                                "source_kind": "LIVE",
                            })
                except Exception as e:
                    continue

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
        Parses EB-menu-en-GB.html for a given cycle and builds the list of target documents
        and sections matching target_airports and sections.yaml.

        Plan:
        First, we download and parse the particular cycle's table of contents (EB-menu-en-GB.html). 
        Then, we gather all HTML links and anchors across the entire AIP. Then we filter down to only the documents configured for this particular system, that is:
        - Aerodromes: for each configured ICAO in target_airports (see config), we locate EB-AD-2.<ICAO>-en-GB.html and records its internal anchors (for ex. #EBBR-AD-2.12 and so on).
        - En-Route: locate EB-ENR-2.1 (airspace), EB-ENR-3.1/3.2 (routes), EB-ENR-4.1 (navaids), EB-ENR-4.4 (waypoints) and EB-ENR-5.1/5.2 (restricted or danger areas).
        - General: locate EB-GEN-0.2 (record of ammendments) and EB-GEN-2.5 (navaid index).
    Then at the end we return a structured list of target documents ready for the crawler.
        """
        headers = {
            "User-Agent": self.crawler_cfg.get("user_agent_format", "Mozilla/5.0").format(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        close_client = False
        if client is None:
            client = httpx.Client(timeout=float(self.crawler_cfg.get("timeout_seconds", 30.0)), headers=headers)
            close_client = True

        sections_to_fetch = []
        menu_url = cycle_info["menu_url"]
        base_eaip = cycle_info["base_eaip_url"]

        try:
            resp = client.get(menu_url)
            if resp.status_code != 200:
                return sections_to_fetch

            soup = BeautifulSoup(resp.text, "html.parser")
            discovered_links: Dict[str, List[Dict[str, str]]] = {}

            # Gather all links from menu
            for a in soup.find_all("a"):
                href = a.get("href", "")
                text = a.get_text(" ", strip=True)
                if not href or href.startswith("#") or href.startswith(".."):
                    continue
                parts = href.split("#")
                doc_file = parts[0]
                anchor = parts[1] if len(parts) > 1 else ""

                if doc_file not in discovered_links:
                    discovered_links[doc_file] = []
                discovered_links[doc_file].append({"anchor": anchor, "title": text, "href": href})

            # 1. Match Aerodromes from target_icaos
            for icao in self.target_icaos:
                expected_filename = f"EB-AD-2.{icao}-en-GB.html"
                if expected_filename in discovered_links:
                    anchors = discovered_links[expected_filename]
                    doc_url = urljoin(base_eaip, expected_filename)
                    sections_to_fetch.append({
                        "document_filename": expected_filename,
                        "url": doc_url,
                        "category": "AD",
                        "icao": icao,
                        "section_id": f"AD-2.{icao}",
                        "anchors": anchors
                    })

            # 2. Match En-Route sections
            enr_specs = [
                ("ENR-2.1", "EB-ENR-2.1-en-GB.html", "Air Traffic Services Airspace"),
                ("ENR-3.1", "EB-ENR-3.1-en-GB.html", "Lower ATS Routes"),
                ("ENR-3.2", "EB-ENR-3.2-en-GB.html", "Upper ATS Routes"),
                ("ENR-4.1", "EB-ENR-4.1-en-GB.html", "Radio Navigation Aids - En-Route"),
                ("ENR-4.4", "EB-ENR-4.4-en-GB.html", "Significant Points"),
                ("ENR-5.1", "EB-ENR-5.1-en-GB.html", "Prohibited, Restricted and Danger Areas"),
                ("ENR-5.2", "EB-ENR-5.2-en-GB.html", "Military Exercise and Training Areas"),
            ]
            for sec_id, filename, name in enr_specs:
                if filename in discovered_links:
                    doc_url = urljoin(base_eaip, filename)
                    sections_to_fetch.append({
                        "document_filename": filename,
                        "url": doc_url,
                        "category": "ENR",
                        "section_id": sec_id,
                        "name": name,
                        "anchors": discovered_links[filename]
                    })

            # 3. Match GEN sections
            gen_specs = [
                ("GEN-0.2", "EB-GEN-0.2-en-GB.html", "Record of AIP Amendments"),
                ("GEN-2.5", "EB-GEN-2.5-en-GB.html", "List of Radio Navigation Aids"),
            ]
            for sec_id, filename, name in gen_specs:
                if filename in discovered_links:
                    doc_url = urljoin(base_eaip, filename)
                    sections_to_fetch.append({
                        "document_filename": filename,
                        "url": doc_url,
                        "category": "GEN",
                        "section_id": sec_id,
                        "name": name,
                        "anchors": discovered_links[filename]
                    })

        finally:
            if close_client:
                client.close()

        return sections_to_fetch

# if __name__ == "__main__":
#     let me test this real quick
#     print(parse_aip_date("01 oct 2026"))
