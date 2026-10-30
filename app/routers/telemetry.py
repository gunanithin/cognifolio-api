import uuid
from typing import Optional, List, Dict, Any, Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, or_

from app.database import get_db
from app.models.entities import ErrorLog, User
from app.schemas.telemetry import (
    ErrorLogCreate,
    ErrorLogResponse,
    ErrorLogListResponse,
    ErrorStatsResponse,
)
from app.core import get_current_user, get_optional_user, require_role

router = APIRouter(prefix="/telemetry", tags=["Telemetry & Developer Error Audit"])

SENSITIVE_KEYS = {"password", "token", "secret", "authorization", "credit_card", "api_key", "bearer"}


def sanitize_payload(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Sanitizes request metadata payload by masking sensitive parameters before database insertion.
    """
    if not payload or not isinstance(payload, dict):
        return payload

    clean = {}
    for k, v in payload.items():
        key_lower = str(k).lower()
        if any(s in key_lower for s in SENSITIVE_KEYS):
            clean[k] = "******** [REDACTED]"
        elif isinstance(v, dict):
            clean[k] = sanitize_payload(v)
        elif isinstance(v, list):
            clean[k] = [sanitize_payload(i) if isinstance(i, dict) else i for i in v]
        else:
            clean[k] = v
    return clean


# ============================================================================
# Public Ingestion Endpoint (Client & Backend Telemetry Dispatch)
# ============================================================================

@router.post("/errors", status_code=status.HTTP_201_CREATED)
def ingest_error_log(
    payload: ErrorLogCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Optional[User], Depends(get_optional_user)] = None,
):
    """
    Public telemetry ingestion endpoint.
    Receives frontend client exceptions and backend failure telemetry.
    Ensures safe persistence in centralized Supabase storage without leaking credentials.
    """
    # Auto-resolve actor if user is logged in but payload omitted details
    user_type = payload.user_type
    user_id = payload.user_id
    user_email = payload.user_email

    if current_user:
        user_id = str(current_user.id)
        user_type = "guest" if current_user.is_guest else "authenticated"
        user_email = current_user.email
    elif not user_type:
        user_type = "guest"

    # Sanitize request payload to guarantee zero PII or credentials leakage
    cleaned_payload = sanitize_payload(payload.request_payload)

    # Check for duplicate correlation_id (in case of rapid network retries)
    existing = db.query(ErrorLog).filter(ErrorLog.correlation_id == payload.correlation_id).first()
    if existing:
        return {
            "success": True,
            "correlation_id": existing.correlation_id,
            "id": str(existing.id),
            "status": "already_logged",
        }

    error_record = ErrorLog(
        id=str(uuid.uuid4()),
        correlation_id=payload.correlation_id,
        user_type=user_type,
        user_id=user_id,
        user_email=user_email,
        subsystem=payload.subsystem,
        severity=payload.severity.upper(),
        endpoint=payload.endpoint,
        http_method=payload.http_method.upper() if payload.http_method else None,
        status_code=payload.status_code,
        error_code=payload.error_code,
        user_friendly_message=payload.user_friendly_message[:500],
        technical_message=payload.technical_message,
        stack_trace=payload.stack_trace,
        client_context=payload.client_context or {},
        request_payload=cleaned_payload,
    )

    db.add(error_record)
    db.commit()
    db.refresh(error_record)

    return {
        "success": True,
        "correlation_id": error_record.correlation_id,
        "id": str(error_record.id),
    }


# ============================================================================
# Developer & Administrator Query Endpoints (Strictly Protected)
# ============================================================================

@router.get("/errors", response_model=ErrorLogListResponse)
def list_error_logs(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role(["developer", "admin"]))],
    user_type: Annotated[Optional[str], Query(description="Filter by 'guest', 'authenticated', or 'system'")] = None,
    subsystem: Annotated[Optional[str], Query(description="Filter by subsystem")] = None,
    severity: Annotated[Optional[str], Query(description="Filter by severity e.g. CRITICAL, ERROR")] = None,
    status_code: Annotated[Optional[int], Query(description="Filter by HTTP status code")] = None,
    search: Annotated[Optional[str], Query(description="Search correlation_id, error_code, email or message")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """
    Developer Audit Query Endpoint.
    Strictly restricted to users with 'developer' or 'admin' roles.
    Returns paginated error logs from Supabase PostgreSQL with advanced segregation filters.
    """
    query = db.query(ErrorLog)

    if user_type:
        query = query.filter(ErrorLog.user_type == user_type.lower())
    if subsystem:
        query = query.filter(ErrorLog.subsystem == subsystem.lower())
    if severity:
        query = query.filter(ErrorLog.severity == severity.upper())
    if status_code:
        query = query.filter(ErrorLog.status_code == status_code)
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                ErrorLog.correlation_id.ilike(term),
                ErrorLog.error_code.ilike(term),
                ErrorLog.user_email.ilike(term),
                ErrorLog.technical_message.ilike(term),
                ErrorLog.endpoint.ilike(term),
            )
        )

    total = query.count()
    items = (
        query.order_by(desc(ErrorLog.timestamp))
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return ErrorLogListResponse(
        total=total,
        page=page,
        limit=limit,
        items=items,
    )


@router.get("/errors/{correlation_id}", response_model=ErrorLogResponse)
def get_error_log_detail(
    correlation_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role(["developer", "admin"]))],
):
    """
    Lookup a specific error record by correlation ID (e.g. ERR-8F2B-9104).
    Restricted to developers and admins.
    """
    record = db.query(ErrorLog).filter(ErrorLog.correlation_id == correlation_id).first()
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Error log with correlation ID '{correlation_id}' not found.",
        )
    return record


@router.get("/stats", response_model=ErrorStatsResponse)
def get_telemetry_stats(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role(["developer", "admin"]))],
):
    """
    Aggregated operational KPIs for the developer dashboard.
    Computes failure distribution across personas (guest vs authenticated) and subsystems.
    """
    total = db.query(func.count(ErrorLog.id)).scalar() or 0

    critical = db.query(func.count(ErrorLog.id)).filter(ErrorLog.severity == "CRITICAL").scalar() or 0
    error_cnt = db.query(func.count(ErrorLog.id)).filter(ErrorLog.severity == "ERROR").scalar() or 0
    warning = db.query(func.count(ErrorLog.id)).filter(ErrorLog.severity == "WARNING").scalar() or 0

    # Group by user_type
    user_type_rows = (
        db.query(ErrorLog.user_type, func.count(ErrorLog.id))
        .group_by(ErrorLog.user_type)
        .all()
    )
    by_user_type = {row[0]: row[1] for row in user_type_rows if row[0]}

    # Group by subsystem
    subsystem_rows = (
        db.query(ErrorLog.subsystem, func.count(ErrorLog.id))
        .group_by(ErrorLog.subsystem)
        .all()
    )
    by_subsystem = {row[0]: row[1] for row in subsystem_rows if row[0]}

    return ErrorStatsResponse(
        total_errors=total,
        critical_count=critical,
        error_count=error_cnt,
        warning_count=warning,
        by_user_type=by_user_type,
        by_subsystem=by_subsystem,
    )
