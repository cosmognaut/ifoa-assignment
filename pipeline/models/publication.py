from enum import Enum
from typing import Optional
from pydantic.alias_generators import to_camel
from pydantic import BaseModel, Field, ConfigDict

class PublicationStatus(str, Enum):
    STAGED = "staged" # staged for addition into DB
    PROMOTED = "promoted" # if checks pass
    REJECTED = "rejected" # if checks fail; live API continues serving prev. cycle

class SourceKind(str, Enum):
    LIVE = "LIVE"
    ZIP = "ZIP"
    DIR = "DIR"

class PublicationStatistics(BaseModel):
    # stats related to a particular publication
    total_documents: int = 0
    parsed_features: int = 0
    issue_count: int = 0
    error_count: int = 0
    warning_count: int = 0

class Publication(BaseModel):
    """A single official release published by skeyes"""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True) # to make everything camelCase for eventual Node API ingestion.
    cycle_id: str = Field(description="AIRAC effective date in ISO format (YYYY-MM-DD)")
    effective_date: str = Field(description="Effective date in ISO format (YYYY-MM-DD)")
    amendment_id: str = Field(description="Amendment number, ex. AMDT 010/2026")
    source_kind: SourceKind = Field(default=SourceKind.LIVE)
    manifest_SHA256: str = Field(default="")
    ingested_at: str = Field(description="ISO timestamp of ingestion")
    status: PublicationStatus = Field(default=PublicationStatus.STAGED)
    stats: PublicationStatistics = Field(default_factory=PublicationStatistics)
    source_url: Optional[str] = None
    notes: Optional[str] = None
