"""
This module contains the utility functions for computing content_hash, which is an SHA-256 fingerprint of only the NORMALIZED values and units, strictly excluding metadata.

See DECISION_LOG for how normalization is implemented and why it is int/float and whitespace invariant.
"""
import re
import json
import hashlib
from typing import Dict, Any, Optional

def normalize_value(val: Any) -> Any:
    """
    Recursively normalizes numbers, strings, and structures:
      - Integers and exact floats (2987 and 2987.0) serialize identically to int.
      - Fractional floats are rounded to 6 decimal places (~0.11m precision).
      - Strings have internal whitespace collapsed and ends stripped.
      - Dictionaries and lists are traversed recursively.
    """
    if isinstance(val, bool):
        return val
    elif isinstance(val, (int, float)):
        f = float(val)
        if f.is_integer():
            return int(f)
        return round(f, 6)
    elif isinstance(val, str):
        return re.sub(r"\s+", " ", val.strip())
    elif isinstance(val, dict):
        return {k: normalize_value(v) for k, v in sorted(val.items())}
    elif isinstance(val, (list, tuple)):
        return [normalize_value(x) for x in val]
    return val

def compute_content_hash(fields: Dict[str, Any], geometry: Optional[Dict[str, Any]] = None) -> str:
    """
    Computes SHA-256 of canonical JSON of normalized values + units only.
    Excludes all provenance: source, section, effective_date, retrieved_at,
    source_url, parser_version, and raw_text.
    Coordinates are rounded to 6 decimal places (~0.11m precision).
    """
    normalized_fields = {}
    for name, fld in sorted(fields.items()):
        if hasattr(fld, "value"):
            val = fld.value
            unit = fld.unit
        elif isinstance(fld, dict):
            val = fld.get("value")
            unit = fld.get("unit")
        else:
            val = fld
            unit = None

        normalized_fields[name] = {
            "value": normalize_value(val),
            "unit": unit
        }

    payload: Dict[str, Any] = {"fields": normalized_fields}
    if geometry:
        geom_type = geometry.get("type") if isinstance(geometry, dict) else geometry.type
        geom_coords = geometry.get("coordinates") if isinstance(geometry, dict) else geometry.coordinates
        payload["geometry"] = {
            "type": geom_type,
            "coordinates": normalize_value(geom_coords)
        }

    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
