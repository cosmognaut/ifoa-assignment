"""
Schema for recording data corruption, physical contraditctions or schema drift.
"""

from enum import Enum
from typing import Optional
from pydantic.alias_generators import to_camel
from pydantic import BaseModel, Field, ConfigDict

class IssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

class ValidationIssue(BaseModel):
    """
    This class models a typical validation issue.

    The strategy here is to map a validation issue back to the feature key and the affected feature, along with a human readable description
    (with severity) and the exact snippet that's the cause for concern.
    """
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True) # again, camelCase for Node API
    publication_id: str = Field(description="Cycle ID e.g. '2026-10-01-current'")
    severity: IssueSeverity = Field(description="'error', 'warning', or 'info'")
    rule_id: str = Field(description="Identifier of rule violated e.g. 'RULE_DECLARED_DIST_TORA_LE_TODA'")
    feature_key: Optional[str] = Field(default=None, description="Natural key of affected feature if applicable")
    section: str = Field(description="AIP section e.g. 'AD-2.12'")
    message: str = Field(description="Human-readable description of the issue")
    raw_snippet: str = Field(description="Exact snippet or value causing the violation")
