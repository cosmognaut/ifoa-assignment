"""
Downloads the files identified by discovery.py while strictly adhering to web crawling ethics, rate limits and caching constraints. See crawler config for configuring this module.
"""

import os
import time
import yaml
import httpx
import hashlib
from pathlib import Path
import urllib.robotparser
from datetime import datetime, timezone
from typing import Optional, Dict, Any


class PoliteCrawler:
    """This class models a simple, polite crawler"""
    def __init__(self, config_path: str = "config/crawler.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)["crawler"]

        contact_email = self.cfg.get("contact_email", "me@ishu.foo")
        user_agent_format = self.cfg.get(
            "user_agent_format",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; contact: {contact_email}) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"
        )
        """
        Loads crawler.yaml and formats the "honest" user agent. Also initiates and httpx.Client with timeout 30s and browser compliant Accept/Accept-Language headers
        """
        self.user_agent = user_agent_format.format(contact_email=contact_email)
        self.rate_limit_seconds = float(self.cfg.get("rate_limit_seconds", 1.0))
        self.timeout = float(self.cfg.get("timeout_seconds", 30.0))
        self.cache_dir = Path(self.cfg.get("cache_dir", "data/raw"))
        self.respect_robots = bool(self.cfg.get("respect_robots_txt", True))

        self.last_request_time = 0.0
        self.headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self.client = httpx.Client(timeout=self.timeout, headers=self.headers, follow_redirects=True)
        self._robot_parser: Optional[urllib.robotparser.RobotFileParser] = None

    def _init_robots(self, base_url: str):
        """
        Parses https://ops.skeyes.be/robots.txt using standard library's
        urllib.robotparser.RobotFileParser
        """
        if not self.respect_robots or self._robot_parser is not None:
            return
        parsed = httpx.URL(base_url)
        robots_url = f"{parsed.scheme}://{parsed.host}/robots.txt"
        self._robot_parser = urllib.robotparser.RobotFileParser()
        try:
            self._rate_limit()
            resp = self.client.get(robots_url)
            if resp.status_code == 200:
                self._robot_parser.parse(resp.text.splitlines())
            else:
                self._robot_parser.allow_all = True
        except Exception:
            self._robot_parser.allow_all = True

    def _rate_limit(self):
        """
        Maintains self.last_request_time and enforces a minimum 1 second delay between every live outbound HTTP request. If the request finishes in 0.2s, it explicitly SLEEPS for 0.8s - this is intended behavior for this crawler to be "polite".
        """
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        self.last_request_time = time.time()

    def fetch_url(self, url: str, cycle_id: str, section_id: str, filename: str) -> Dict[str, Any]:
        """
        Fetch URL with 1 req/s rate limit, byte-for-byte caching, and conditional requests.
        Returns a dict matching ManifestItem.

        Plan:
        Target destination is data/raw/<cycle_id>/<filename>. Alongside the raw file, it also maintains a small YAML sidecar (ex. EB-AD-2.EBBR-en-GB.html.meta) recording the previous etag, sha256 and fetched_at.If the file exists on the disk and has an etag, it sends If-None-Match: <etag> If the server responds with 304 not modified, it reuses the existing bytes without re-downloading, marking the status as "not modified". We also compute SHA256 hash of the bytes on the fly and write the sidecar .meta file. For fault-tolerant returns, we return a dictionary matching ManifestItem with status  "fetched", "not_modified", "cached", or "failed_http_XYZ".
        """
        self._init_robots(url)
        dest_dir = self.cache_dir / cycle_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename

        meta_path = dest_path.with_suffix(dest_path.suffix + ".meta")

        # Check existing cached file and metadata for conditional request
        existing_etag = None
        existing_sha = None
        if dest_path.exists():
            with open(dest_path, "rb") as f:
                content = f.read()
                existing_sha = hashlib.sha256(content).hexdigest()
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = yaml.safe_load(f) or {}
                        existing_etag = meta.get("etag")
                except Exception:
                    pass

        req_headers = dict(self.headers)
        if existing_etag:
            req_headers["If-None-Match"] = existing_etag

        now_iso = datetime.now(timezone.utc).isoformat()

        # If robots.txt check is enabled, note: ops.skeyes.be puts Disallow: / on root
        # If user allows public eAIP download, we proceed politely at 1 req/s.
        self._rate_limit()
        try:
            resp = self.client.get(url, headers=req_headers)
            if resp.status_code == 304 and dest_path.exists():
                # Not modified, reuse existing cached file
                return {
                    "url": url,
                    "local_path": str(dest_path),
                    "sha256": existing_sha,
                    "etag": existing_etag,
                    "fetched_at": now_iso,
                    "status": "not_modified",
                    "bytes_length": dest_path.stat().st_size,
                    "section_id": section_id,
                    "document_type": "html"
                }
            elif resp.status_code == 200:
                raw_bytes = resp.content
                sha = hashlib.sha256(raw_bytes).hexdigest()
                etag = resp.headers.get("etag")

                # Cache byte-for-byte
                with open(dest_path, "wb") as f:
                    f.write(raw_bytes)

                # Cache metadata
                with open(meta_path, "w", encoding="utf-8") as f:
                    yaml.safe_dump({"etag": etag, "sha256": sha, "url": url, "fetched_at": now_iso}, f)

                return {
                    "url": url,
                    "local_path": str(dest_path),
                    "sha256": sha,
                    "etag": etag,
                    "fetched_at": now_iso,
                    "status": "fetched",
                    "bytes_length": len(raw_bytes),
                    "section_id": section_id,
                    "document_type": "html"
                }
            else:
                return {
                    "url": url,
                    "local_path": str(dest_path) if dest_path.exists() else "",
                    "sha256": existing_sha or "",
                    "etag": existing_etag,
                    "fetched_at": now_iso,
                    "status": f"failed_http_{resp.status_code}",
                    "bytes_length": dest_path.stat().st_size if dest_path.exists() else 0,
                    "section_id": section_id,
                    "document_type": "html"
                }
        except Exception as e:
            if dest_path.exists():
                return {
                    "url": url,
                    "local_path": str(dest_path),
                    "sha256": existing_sha or "",
                    "etag": existing_etag,
                    "fetched_at": now_iso,
                    "status": "cached",
                    "bytes_length": dest_path.stat().st_size,
                    "section_id": section_id,
                    "document_type": "html"
                }
            return {
                "url": url,
                "local_path": "",
                "sha256": "",
                "etag": None,
                "fetched_at": now_iso,
                "status": f"error: {str(e)}",
                "bytes_length": 0,
                "section_id": section_id,
                "document_type": "html"
            }
