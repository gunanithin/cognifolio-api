from datetime import datetime
from typing import Optional, Any, Union
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


class DocumentBase(BaseModel):
    filename: str
    original_filename: str
    file_type: str
    file_size_bytes: int
    page_count: int = 1


class DocumentCreate(DocumentBase):
    pass


class DocumentInsightResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Union[UUID, str]
    document_type: Optional[str] = "Document"
    title: Optional[str] = None
    summary: Optional[str] = None
    key_points: list[str] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)
    line_items: list[dict[str, Any]] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    risk_clauses: list[str] = Field(default_factory=list)
    charts_data: dict[str, Any] = Field(default_factory=dict)
    raw_extracted_text: Optional[str] = None
    confidence_score: float = 1.0
    created_at: datetime


class DocumentResponse(DocumentBase):
    model_config = ConfigDict(from_attributes=True)

    id: Union[UUID, str]
    user_id: Union[UUID, str]
    org_id: Optional[Union[UUID, str]] = None
    file_path: str
    status: str
    error_message: Optional[str] = None
    retention_days: int
    uploaded_at: datetime
    processed_at: Optional[datetime] = None
    insights: Optional[DocumentInsightResponse] = None


class DocumentListResponse(BaseModel):
    total: int
    items: list[DocumentResponse]
