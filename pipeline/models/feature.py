"""
The canonical domain-neutral container for every aeronautical object in the system (runways, airports, VORs, runway directions, waypoints, airspaces).
"""

from enum import Enum
from typing import Optional, Dict, Any
from pydantic.alias_generators import to_camel
from pydantic import BaseModel, Field, ConfigDict

class FeatureType(str, Enum):
    AERODROME = "aerodrome"
    RUNWAY = "runway"
    RUNWAY_DIRECTION = "runwayDirection"
    NAVAID = "navaid"
    WAYPOINT = "waypoint"
    ROUTE = "route"
    AIRSPACE = "airspace"
    SPECIAL_USE_AREA = "specialUseArea"
    PROCEDURE_META = "procedureMeta"
    OPERATIONAL_INFO = "operationalInfo"

class SourceClass(str, Enum):
    AIP = "AIP"
    SUP = "SUP"
    NOTAM = "NOTAM"

class Temporality(str, Enum):
    """If the data is permanent or temporary"""
    PERMANENT = "permanent"
    TEMPORARY = "temporary"

class FeatureField(BaseModel):
    """
    Every extracted value should retain complete traceability to its original source. We save the source, the effective date, retrieved_at, the raw text, the aprsed type value, unit, etc.
    """
    value: Any = Field(description="Parsed typed value (float, int, str, dict, etc.)")
    unit: Optional[str] = Field(default=None, description="Physical unit e.g. 'M', 'FT', 'DEG'")
    source: str = Field(description="Authoritative source e.g. from crawler.yaml publisher.name")
    section: str = Field(description="Exact AIP section e.g. 'EBBR AD 2.12', 'EBBR AD 2.13'")
    effective_date: str = Field(description="AIP effective date YYYY-MM-DD")
    retrieved_at: str = Field(description="ISO timestamp YYYY-MM-DDTHH:MM:SSZ when raw document was fetched")
    source_url: str = Field(description="Canonical URL where raw source file was retrieved")
    parser_version: str = Field(default="1.0", description="Version of the parser logic")
    raw_text: str = Field(description="Exact raw text snippet from the raw HTML cell")


class GeoJSONGeometry(BaseModel):
    type: str = Field(description="'Point', 'LineString', or 'Polygon'")
    coordinates: Any = Field(description="Coordinates conforming to GeoJSON RFC 7946")

class Feature(BaseModel):
    """A typical feature, which is an aeronautical object"""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True) # convert it to camel case so that MongoDB emits camelCase JSON for my node API to ingest.
    feature_type: FeatureType
    natural_key: str = Field(description="Deterministic natural key e.g. 'EBBR/RWY/01-19' or 'EBBR/RWY/01'")
    parent_key: Optional[str] = Field(default=None, description="Parent natural key e.g. 'EBBR' or 'EBBR/RWY/01-19'")
    publication_id: str = Field(description="Cycle ID e.g. '2026-10-01-current'")
    effective_from: str = Field(description="ISO date YYYY-MM-DD")
    effective_to: Optional[str] = Field(default=None, description="ISO date YYYY-MM-DD or null if active")
    source_class: SourceClass = Field(default=SourceClass.AIP)
    temporality: Temporality = Field(default=Temporality.PERMANENT)
    geometry: Optional[GeoJSONGeometry] = None
    content_hash: str = Field(description="SHA-256 of canonical JSON of normalized values + units only")
    fields: Dict[str, FeatureField] = Field(default_factory=dict)
