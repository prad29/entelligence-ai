from pydantic import BaseModel
from typing import Optional, Generic, TypeVar, List
from datetime import datetime

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class AmenityMappingCreate(BaseModel):
    amenity_keyword: str
    screen_format: str
    priority_tier: int
    circuit_name: Optional[str] = None
    na_default: Optional[str] = None
    notes: Optional[str] = None


class AmenityMappingRead(BaseModel):
    id: int
    amenity_keyword: str
    screen_format: str
    priority_tier: int
    circuit_name: Optional[str]
    na_default: Optional[str]
    status: str
    notes: Optional[str]
    updated_at: datetime
    version: int

    class Config:
        from_attributes = True


class AmenityMappingPatch(BaseModel):
    amenity_keyword: Optional[str] = None
    screen_format: Optional[str] = None
    priority_tier: Optional[int] = None
    circuit_name: Optional[str] = None
    na_default: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None


class MovieFormatMappingCreate(BaseModel):
    keyword: str
    format: str
    priority_tier: int
    notes: Optional[str] = None


class MovieFormatMappingRead(BaseModel):
    id: int
    keyword: str
    format: str
    priority_tier: int
    status: str
    notes: Optional[str]
    updated_at: datetime
    version: int

    class Config:
        from_attributes = True


class MovieFormatMappingPatch(BaseModel):
    keyword: Optional[str] = None
    format: Optional[str] = None
    priority_tier: Optional[int] = None
    notes: Optional[str] = None
    status: Optional[str] = None


class ReviewDecision(BaseModel):
    reason: Optional[str] = None



class CircuitAliasCreate(BaseModel):
    raw_or_alias: str
    canonical: str


class SettingsUpdate(BaseModel):
    bedrock_model_id: Optional[str] = None
    bedrock_region: Optional[str] = None
    ai_trigger_mode: Optional[str] = None
    ai_autoapply_confidence: Optional[float] = None
