from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class DocumentCompareRequest(BaseModel):
    base_document_id: str = Field(..., description="UUID of the base document (Revision A / Original)")
    target_document_id: str = Field(..., description="UUID of the target document (Revision B / Amendment)")
    focus_aspects: Optional[List[str]] = Field(default=None, description="Optional focus areas: e.g. ['clauses', 'fees', 'liability']")


class DiffItem(BaseModel):
    id: str = Field(..., description="Unique ID for this diff item")
    type: str = Field(..., description="'added' | 'removed' | 'modified' | 'unchanged'")
    category: str = Field(..., description="'clause' | 'financial' | 'liability' | 'term' | 'entity' | 'other'")
    title: str = Field(..., description="Clause or topic title")
    doc_a_text: Optional[str] = Field(default="", description="Original text or term in Document A")
    doc_b_text: Optional[str] = Field(default="", description="Modified/New text or term in Document B")
    risk_impact: str = Field(default="neutral", description="'increased' | 'decreased' | 'neutral' | 'high' | 'critical'")
    explanation: str = Field(..., description="Plain-English legal and business explanation of the impact")
    doc_a_page: Optional[int] = Field(default=None, description="Page number in Document A")
    doc_b_page: Optional[int] = Field(default=None, description="Page number in Document B")


class CompareMetrics(BaseModel):
    added_count: int = 0
    removed_count: int = 0
    modified_count: int = 0
    financial_delta: Optional[str] = None
    similarity_score: float = Field(default=100.0, description="0.0 to 100.0% structural and semantic similarity")
    risk_delta: str = Field(default="neutral", description="'increased' | 'decreased' | 'neutral'")


class DocumentCompareResponse(BaseModel):
    base_document_id: str
    base_document_title: str
    base_document_type: str
    target_document_id: str
    target_document_title: str
    target_document_type: str
    executive_summary: str
    metrics: CompareMetrics
    diffs: List[DiffItem]
    key_recommendations: List[str]
