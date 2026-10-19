"""
This module acts as the cryptographic registrar and disk auditor, as it bridges
the crawler and the parser by maintaining a manifest.jsonl file.
"""

import json
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Tuple
from pipeline.models.manifest import ManifestItem

class ManifestManager:
    """This class models a simple manifest manager"""

    @staticmethod
    def get_manifest_path(cache_dir: str, cycle_id: str) -> Path:
        """
        Resolve data/raw/<cycle_id>/manifest.jsonl
        """
        return Path(cache_dir) / cycle_id / "manifest.jsonl"

    @classmethod
    def write_manifest(cls, cache_dir: str, cycle_id: str, items: List[Dict[str, Any]]) -> str:
        """
        Writes items to data/raw/<cycle_id>/manifest.jsonl and returns the manifest SHA-256.

        Plan:
        Validate each raw dictionary first using ManifestItem(**item) and then serialize each item to a JSON line; then write that line to manifest.jsonl and re-read the generated manifest.jsonl file as raw bytes, calculate its SHA-256 hash and return it. The hash gets stored in DB as Publication.manifestSHA256.
        """
        path = cls.get_manifest_path(cache_dir, cycle_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        lines = []
        for item in items:
            model = ManifestItem(**item)
            lines.append(model.model_dump_json() + "\n")

        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    @classmethod
    def load_manifest(cls, cache_dir: str, cycle_id: str) -> List[ManifestItem]:
        """
        Reads manifest.jsonl line by line and parses each line into a typed ManifestItem object using Pydantic's model_validate_json()
        """
        path = cls.get_manifest_path(cache_dir, cycle_id)
        if not path.exists():
            return []
        items = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(ManifestItem.model_validate_json(line))
        return items

    @classmethod
    def verify_manifest(cls, cache_dir: str, cycle_id: str) -> Tuple[bool, List[str]]:
        """
        Verifies that every file in the manifest exists and matches its recorded sha256.
        Returns (success, errors).
        """
        items = cls.load_manifest(cache_dir, cycle_id)
        if not items:
            return False, ["Manifest does not exist or is empty"]

        errors = []
        for item in items:
            # `Path("")` is `Path(".")`, which exists: a blank local_path must be reported
            # as missing rather than opened, or verify_manifest raises IsADirectoryError.
            raw_path = item.local_path.strip()
            if raw_path == "":
                errors.append(
                    f"Missing file: <empty local_path> for {item.url} (expected {item.sha256})"
                )
                continue
            p = Path(raw_path)
            if not p.is_file():
                errors.append(f"Missing file: {p} (expected {item.sha256})")
                continue
            with open(p, "rb") as f:
                actual_sha = hashlib.sha256(f.read()).hexdigest()
            if actual_sha != item.sha256:
                errors.append(f"SHA-256 mismatch for {p}: got {actual_sha}, expected {item.sha256}")

        return (len(errors) == 0), errors
