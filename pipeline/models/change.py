from enum import Enum
from typing import Optional, List, Any, Dict
from pydantic.alias_generators import to_camel
from pydantic import BaseModel, Field, ConfigDict
from pipeline.models.feature import FeatureType

class ChangeStatus(str, Enum):
    NEW = "NEW"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
    UNCHANGED = "UNCHANGED"

class FieldChange(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    field: str
    previous: Any
    new: Any
    section: Optional[str] = None

class Change(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    from_cycle: str
    to_cycle: str
    feature_key: str
    feature_type: FeatureType
    status: ChangeStatus
    field_changes: List[FieldChange] = Field(default_factory=list)
    source: Dict[str, str] = Field(default_factory=dict)
