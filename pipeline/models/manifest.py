from typing import Optional
from pydantic import BaseModel

class ManifestItem(BaseModel):
    """The formal data contract for a single downloaded file"""
    url: str
    local_path: str
    sha256: str
    etag: Optional[str] # HTTP cache tag, optional
    fetched_at: str
    status: str # fetched, cached, not_modified or failed
    bytes_length: int # file size in bytes
    section_id: str # logical AIP section
    document_type: str # default is html, can be pdf or zip
