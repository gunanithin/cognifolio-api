from typing import Optional, Any
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Vague or specific natural language search prompt")
    document_type: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=50)


class SearchResultItem(BaseModel):
    document_id: str
    filename: str
    original_filename: str
    document_type: Optional[str]
    title: Optional[str]
    summary: Optional[str]
    highlighted_points: list[str] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)
    similarity_score: float
    uploaded_at: str


class SearchResponse(BaseModel):
    query: str
    total_matches: int
    results: list[SearchResultItem]
