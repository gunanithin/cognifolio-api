from datetime import datetime
from typing import Optional, Dict, Any, List, Union
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


class ErrorLogCreate(BaseModel):
    """
    Schema for ingesting client or backend errors into centralized Supabase storage.
    """
    correlation_id: str = Field(..., description="Unique client or backend tracking ID e.g. ERR-8F2B-9104")
    user_type: str = Field(default="guest", description="'guest' | 'authenticated' | 'system'")
    user_id: Optional[Union[UUID, str]] = None
    user_email: Optional[str] = None
    subsystem: str = Field(default="general", description="'ingestion' | 'extraction' | 'ai_analysis' | 'storage' | 'vector_db' | 'auth' | 'client_runtime'")
    severity: str = Field(default="ERROR", description="'CRITICAL' | 'ERROR' | 'WARNING' | 'INFO'")
    endpoint: Optional[str] = None
    http_method: Optional[str] = None
    status_code: Optional[int] = None
    error_code: str = Field(default="UNKNOWN_ERROR")
    user_friendly_message: str = Field(..., max_length=500)
    technical_message: str
    stack_trace: Optional[str] = None
    client_context: Dict[str, Any] = Field(default_factory=dict)
    request_payload: Optional[Dict[str, Any]] = None


class ErrorLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Union[UUID, str]
    correlation_id: str
    timestamp: datetime
    user_type: str
    user_id: Optional[Union[UUID, str]] = None
    user_email: Optional[str] = None
    subsystem: str
    severity: str
    endpoint: Optional[str] = None
    http_method: Optional[str] = None
    status_code: Optional[int] = None
    error_code: str
    user_friendly_message: str
    technical_message: str
    stack_trace: Optional[str] = None
    client_context: Dict[str, Any] = Field(default_factory=dict)
    request_payload: Optional[Dict[str, Any]] = None



class ErrorLogListResponse(BaseModel):
    total: int
    page: int
    limit: int
    items: List[ErrorLogResponse]


class ErrorStatsResponse(BaseModel):
    total_errors: int
    critical_count: int
    error_count: int
    warning_count: int
    by_user_type: Dict[str, int]
    by_subsystem: Dict[str, int]
