"""
Contains contains the primitive-level scalar parsers which are pure functions that are agnostic to airports, databases, HTML, etc. 
These are pure regex workhorses that take text strings and convert them into numbers, units and coords.
For if the regex-based selection is going to break later-- see DECISION_LOG.
This also houses CONVERTER_REGISTRY: Dict[str, Callable] which maps string names ex. "coordinates" to pure functions that parse raw HTML cell text into typed numbers and units. All regexes here are compiled from config/sections.yaml: converters_regex
"""
import re
from typing import Optional, Tuple, Any, Dict, Callable
from pipeline.config import ConfigError, load_sections_config, require, require_str

_sections_cfg = load_sections_config()
_regex_cfg = require(_sections_cfg, "converters_regex", "sections.yaml")
_surface_cfg = require(_sections_cfg, "surface_mapping", "sections.yaml")
_vlim_cfg = require(_sections_cfg, "vertical_limits", "sections.yaml")

RE_COORD_DMS = re.compile(require_str(_regex_cfg, "coordinates_dms", "sections.yaml converters_regex"))
RE_COORD_DMS_COMPACT = re.compile(require_str(_regex_cfg, "coordinates_dms_compact", "sections.yaml converters_regex"))
RE_DIMENSIONS = re.compile(require_str(_regex_cfg, "dimensions", "sections.yaml converters_regex"))
RE_BEARING = re.compile(require_str(_regex_cfg, "bearing", "sections.yaml converters_regex"))
RE_ELEVATION = re.compile(require_str(_regex_cfg, "elevation", "sections.yaml converters_regex"))
RE_ELEVATION_FALLBACK = re.compile(require_str(_regex_cfg, "elevation_fallback", "sections.yaml converters_regex"))
RE_DISTANCE = re.compile(require_str(_regex_cfg, "distance", "sections.yaml converters_regex"))
RE_LIMIT_FL = re.compile(require_str(_regex_cfg, "limit_flight_level", "sections.yaml converters_regex"))
RE_LIMIT_ALT = re.compile(require_str(_regex_cfg, "limit_altitude", "sections.yaml converters_regex"))
RE_LIMIT_BARE = re.compile(require_str(_regex_cfg, "limit_bare_number", "sections.yaml converters_regex"))

def convert_coordinates(coord_str: str) -> Tuple[Optional[Tuple[float, float]], Optional[str]]:
    """
    Parses DMS coordinates to WGS-84 Decimal Degrees [lon, lat] rounded to 6 decimal places.
    Returns ((lon, lat), None).
    """
    if not coord_str:
        return None, None

    cleaned = coord_str.replace("°", "").replace("'", "").replace('"', "").replace(" ", "").strip()
    m = RE_COORD_DMS_COMPACT.match(cleaned)
    if not m:
        m = RE_COORD_DMS.search(coord_str.strip())
        if not m:
            return None, None

    lat_d, lat_m, lat_s, lat_h, lon_d, lon_m, lon_s, lon_h = m.groups()
    lat = float(lat_d) + float(lat_m) / 60.0 + float(lat_s) / 3600.0
    if lat_h.upper() == "S":
        lat = -lat

    lon = float(lon_d) + float(lon_m) / 60.0 + float(lon_s) / 3600.0
    if lon_h.upper() == "W":
        lon = -lon

    return (round(lon, 6), round(lat, 6)), None

def convert_dimensions(dim_str: str) -> Tuple[Tuple[Optional[float], Optional[float]], Optional[str]]:
    """Parses '2 987 x 50' into ((length, width), 'M')."""
    if not dim_str:
        return (None, None), None
    m = RE_DIMENSIONS.search(dim_str)
    if not m:
        return (None, None), None
    len_str, wid_str = m.groups()
    len_clean = re.sub(r"[\s\u2009\xa0]+", "", len_str)
    wid_clean = re.sub(r"[\s\u2009\xa0]+", "", wid_str)
    try:
        return (float(len_clean), float(wid_clean)), "M"
    except ValueError:
        return (None, None), None

def convert_bearing(bearing_str: str) -> Tuple[Optional[float], Optional[str]]:
    """Parses '014.43°' into (14.43, 'DEG')."""
    if not bearing_str:
        return None, None
    m = RE_BEARING.search(bearing_str)
    if m:
        try:
            return round(float(m.group(1)), 2), "DEG"
        except ValueError:
            return None, None
    return None, None

def convert_distance(dist_str: str) -> Tuple[Optional[float], Optional[str]]:
    """Parses declared distance '2987' or '2 987' into (2987.0, 'M')."""
    if not dist_str or dist_str.upper() == "NIL":
        return None, None
    m = RE_DISTANCE.search(dist_str)
    if m:
        cleaned = re.sub(r"[\s\u2009\xa0]+", "", m.group(1))
        try:
            return float(cleaned), "M"
        except ValueError:
            return None, None
    return None, None

def convert_elevation(elev_str: str) -> Tuple[Optional[float], Optional[str]]:
    """Parses 'THR 174.8 FT' into (174.8, 'FT')."""
    if not elev_str or elev_str.upper() == "NIL":
        return None, None
    m = RE_ELEVATION.search(elev_str.upper())
    if m:
        try:
            return float(m.group(1)), "FT"
        except ValueError:
            pass
    m2 = RE_ELEVATION_FALLBACK.search(elev_str)
    if m2:
        try:
            return float(m2.group(1)), "FT"
        except ValueError:
            return None, None
    return None, None

def convert_surface_strength(raw_text: str) -> Tuple[Tuple[str, str], Optional[str]]:
    """
    Parses strength and surface description into ((surface_type, raw_strength), None)
    Surface type is mapped dynamically from config/sections.yaml: surface_mapping.
    """
    if not raw_text:
        return (_surface_cfg.get("default", "OTHER"), ""), None

    upper = raw_text.upper()
    surface = _surface_cfg.get("default", "OTHER")
    if "ASPH" in upper:
        surface = _surface_cfg.get("asphalt", "ASPH")
    elif "CONC" in upper:
        surface = _surface_cfg.get("concrete", "CONC")
    elif "GRAS" in upper:
        surface = _surface_cfg.get("grass", "GRAS")

    return (surface, raw_text), None

def convert_vertical_limit(raw_text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Parses vertical limit strings according to declarative vertical_limits spec in sections.yaml.
    Zero hardcoded domain string or sentinel literals.
    """
    if not raw_text or raw_text.upper() == "NIL":
        return None, None
    u = re.sub(r"[\s\u2009\xa0]+", " ", raw_text.upper().strip())

    if u in _vlim_cfg["surface_tokens"]:
        unit = _vlim_cfg["surface_unit"]
        return {
            "value": float(_vlim_cfg["surface_val"]),
            "unit": unit,
            "reference": _vlim_cfg["surface_reference"]
        }, unit

    if u in _vlim_cfg["unlimited_tokens"]:
        unit = _vlim_cfg["unlimited_unit"]
        return {
            "value": float(_vlim_cfg["unlimited_sentinel_val"]),
            "unit": unit,
            "reference": _vlim_cfg["unlimited_reference"]
        }, unit

    m_fl = RE_LIMIT_FL.match(u)
    if m_fl:
        unit = _vlim_cfg["unlimited_unit"]  # FL
        return {"value": float(m_fl.group(1)), "unit": unit, "reference": _vlim_cfg["default_altitude_reference"]}, unit

    m_ft = RE_LIMIT_ALT.match(u)
    if m_ft:
        ref = m_ft.group(3) or _vlim_cfg["default_altitude_reference"]
        unit = m_ft.group(2)
        return {"value": float(m_ft.group(1)), "unit": unit, "reference": ref}, unit

    m_bare = RE_LIMIT_BARE.match(u)
    if m_bare:
        # Bare number without unit or reference: data quality error
        return {"value": float(m_bare.group(1)), "unit": None, "reference": None}, None

    return {"value": None, "unit": None, "reference": None, "raw": raw_text}, None

def convert_text(raw_text: str) -> Tuple[Optional[str], Optional[str]]:
    """Passthrough text converter."""
    if not raw_text:
        return None, None
    return raw_text.strip(), None

# The Canonical Converter Dispatch Registry mandated by the ICAO eAIP specification
CONVERTER_REGISTRY: Dict[str, Callable[[str], Tuple[Any, Optional[str]]]] = {
    "coordinates": convert_coordinates,
    "dimensions": convert_dimensions,
    "bearing": convert_bearing,
    "distance": convert_distance,
    "elevation": convert_elevation,
    "surface_strength": convert_surface_strength,
    "vertical_limit": convert_vertical_limit,
    "text": convert_text,
}

def resolve_converter(field_spec: Dict[str, Any], context: str) -> Tuple[str, Callable[[str], Tuple[Any, Optional[str]]]]:
    """
    Return (converter_name, converter_fn) for a field spec, refusing to guess.

    The name must be declared, must be a non-empty string, and must exist in
    CONVERTER_REGISTRY. There is no text fallback: a typo has to stop the parse
    rather than quietly emit prose, because the repo rule in pipeline/config.py
    is that a missing config value raises instead of acquiring a second meaning.
    """
    name = require_str(field_spec, "converter", context)
    if name not in CONVERTER_REGISTRY:
        known = ", ".join(sorted(CONVERTER_REGISTRY))
        raise ConfigError(
            f"{context}: field declares unknown converter '{name}'. Known converters: {known}"
        )
    return name, CONVERTER_REGISTRY[name]


def validate_section_converters(sections_cfg: Dict[str, Any]) -> None:
    """
    Assert that every converter declared in config/sections.yaml resolves against
    CONVERTER_REGISTRY. A misspelled name is a config bug: it used to silently
    degrade to the text passthrough, which made the converter contract advisory.

    Raises ConfigError naming the section id, field name and offending value.
    Fields marked `converter_driven: false` are not converter-driven (their parser
    derives the value itself), so their `converter` key is inert documentation and
    is deliberately not dispatched; it is still checked for honesty.
    """
    offenders = []
    for section in require(sections_cfg, "sections", "sections.yaml"):
        section_id = require_str(section, "id", "sections.yaml sections[]")
        for field_name, field_spec in (section.get("fields") or {}).items():
            if not isinstance(field_spec, dict) or "converter" not in field_spec:
                continue
            declared = require_str(
                field_spec, "converter", f"sections.yaml section '{section_id}' field '{field_name}'"
            )
            if field_spec.get("converter_driven", True) is False:
                continue
            if declared not in CONVERTER_REGISTRY:
                offenders.append((section_id, field_name, declared))

    if offenders:
        details = "; ".join(
            f"section '{sid}' field '{fname}' declares converter '{declared}', "
            f"which is not in CONVERTER_REGISTRY"
            for sid, fname, declared in offenders
        )
        known = ", ".join(sorted(CONVERTER_REGISTRY))
        raise ConfigError(f"Unknown converter(s) in sections.yaml: {details}. Known converters: {known}")


# Fail fast: validate the declared converter names as soon as sections.yaml is first
# consumed (module import), before any parser walks a document. A typo must surface
# here, not on the third runway or, worse, never.
validate_section_converters(_sections_cfg)


# compatibility aliases, just for testing
dms_to_dd = lambda s: convert_coordinates(s)[0]
parse_runway_dimensions = lambda s: convert_dimensions(s)[0]
parse_bearing = lambda s: convert_bearing(s)[0]
parse_distance = lambda s: convert_distance(s)[0]
parse_elevation = lambda s: convert_elevation(s)[0]
