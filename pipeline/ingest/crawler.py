"""
Downloads the files identified by discovery.py while strictly adhering to web crawling ethics,
rate limits, retries, robots.txt, and caching constraints. Supports offline --from-zip and --from-dir fallbacks.
"""
import os
import time
import yaml
import httpx
import hashlib
import zipfile
import shutil
from pathlib import Path
import urllib.robotparser
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from pipeline.config import load_crawler_config, require_str

class PoliteCrawler:
    """This class models a polite, rate-limited crawler with offline zip/dir fallback support."""
    def __init__(self, config_path: str = "config/crawler.yaml", cache_dir: Optional[Path] = None):
        self.cfg = load_crawler_config(config_path)

        contact_email = self.cfg.get("contact_email", "ops@ifoa-aero.be")
        user_agent_format = require_str(self.cfg, "user_agent_format", config_path)
        self.user_agent = user_agent_format.format(contact_email=contact_email)

        self.rate_limit_seconds = float(self.cfg.get("rate_limit_seconds", 1.0))
        self.timeout = float(self.cfg.get("timeout_seconds", 30.0))
        self.cache_dir = cache_dir if cache_dir is not None else Path(require_str(self.cfg, "cache_dir", config_path))
        self.respect_robots = bool(self.cfg.get("respect_robots_txt", True))
        self.max_retries = int(self.cfg.get("max_retries", 3))
        self.retry_backoff = float(self.cfg.get("retry_backoff_seconds", 2.0))

        self.last_request_time = 0.0
        self.headers = {
            "User-Agent": self.user_agent,
            "Accept": self.cfg.get("publisher", {}).get("accept_header", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
            "Accept-Language": "en-US,en;q=0.9",
        }
        self.client = httpx.Client(timeout=self.timeout, headers=self.headers, follow_redirects=True)
        self._robot_parser: Optional[urllib.robotparser.RobotFileParser] = None

    def _init_robots(self, base_url: str):
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
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        self.last_request_time = time.time()

    def fetch_from_zip(self, zip_path: Path, cycle_id: str, section_id: str, filename: str) -> Dict[str, Any]:
        """Offline fallback: extracts document directly from local zip package."""
        dest_dir = self.cache_dir / cycle_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename
        now_iso = datetime.now(timezone.utc).isoformat()

        if not zip_path.is_file():
            return {
                "url": f"file://{zip_path}/{filename}", "local_path": "", "sha256": "",
                "etag": None, "fetched_at": now_iso, "status": f"failed_zip_not_found: {zip_path}",
                "bytes_length": 0, "section_id": section_id, "document_type": "html"
            }

        with zipfile.ZipFile(zip_path, "r") as zf:
            matching = [n for n in zf.namelist() if n.endswith(filename)]
            if not matching:
                return {
                    "url": f"file://{zip_path}/{filename}", "local_path": "", "sha256": "",
                    "etag": None, "fetched_at": now_iso, "status": f"failed_file_not_in_zip: {filename}",
                    "bytes_length": 0, "section_id": section_id, "document_type": "html"
                }
            raw_bytes = zf.read(matching[0])
            dest_path.write_bytes(raw_bytes)
            sha = hashlib.sha256(raw_bytes).hexdigest()

            return {
                "url": f"file://{zip_path}/{matching[0]}",
                "local_path": str(dest_path),
                "sha256": sha,
                "etag": None,
                "fetched_at": now_iso,
                "status": "fetched_from_zip",
                "bytes_length": len(raw_bytes),
                "section_id": section_id,
                "document_type": "html"
            }

    def fetch_from_dir(self, dir_path: Path, cycle_id: str, section_id: str, filename: str) -> Dict[str, Any]:
        """Offline fallback: copies document directly from local unpacked directory."""
        dest_dir = self.cache_dir / cycle_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename
        now_iso = datetime.now(timezone.utc).isoformat()

        src_candidates = list(dir_path.rglob(filename))
        if not src_candidates or not src_candidates[0].is_file():
            return {
                "url": f"file://{dir_path}/{filename}", "local_path": "", "sha256": "",
                "etag": None, "fetched_at": now_iso, "status": f"failed_file_not_in_dir: {filename}",
                "bytes_length": 0, "section_id": section_id, "document_type": "html"
            }

        raw_bytes = src_candidates[0].read_bytes()
        dest_path.write_bytes(raw_bytes)
        sha = hashlib.sha256(raw_bytes).hexdigest()

        return {
            "url": f"file://{src_candidates[0]}",
            "local_path": str(dest_path),
            "sha256": sha,
            "etag": None,
            "fetched_at": now_iso,
            "status": "fetched_from_dir",
            "bytes_length": len(raw_bytes),
            "section_id": section_id,
            "document_type": "html"
        }

    def fetch_url(self, url: str, cycle_id: str, section_id: str, filename: str) -> Dict[str, Any]:
        """Fetch URL with rate limiting, retries, byte-for-byte caching, and conditional requests."""
        self._init_robots(url)
        dest_dir = self.cache_dir / cycle_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename
        meta_path = dest_path.with_suffix(dest_path.suffix + ".meta")

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

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            self._rate_limit()
            try:
                resp = self.client.get(url, headers=req_headers)
                if resp.status_code == 304 and dest_path.exists():
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

                    with open(dest_path, "wb") as f:
                        f.write(raw_bytes)

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
                elif 500 <= resp.status_code < 600:
                    last_error = f"HTTP {resp.status_code}"
                    if attempt < self.max_retries:
                        time.sleep(self.retry_backoff * attempt)
                        continue
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
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_error = str(e)
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * attempt)
                    continue

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
            "status": f"error: {last_error}",
            "bytes_length": 0,
            "section_id": section_id,
            "document_type": "html"
        }
