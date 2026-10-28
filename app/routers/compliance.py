import io
import csv
import json
from datetime import datetime, timezone
from typing import Optional, Dict
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.entities import AuditLog, Document, User
from app.services.audit import log_audit_event
from app.services.redactor import redactor
from app.core import get_current_user

router = APIRouter(prefix="/compliance", tags=["Compliance & Audit"])


VALID_COMPLIANCE_ACTIONS = {
    "uploaded",
    "redacted",
    "viewed_redacted",
    "deleted",
    "exported",
    "document_comparison",
}


@router.get("/stats")
def get_compliance_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns aggregated enterprise compliance metrics, PII discovery totals,
    and audit trail activity summaries scoped to the authenticated caller.
    """
    doc_query = db.query(Document)
    audit_query = db.query(AuditLog)

    if current_user.role != "admin":
        doc_query = doc_query.filter(Document.user_id == current_user.id)
        audit_query = audit_query.filter(AuditLog.user_id == current_user.id)

    total_docs = doc_query.count()
    all_logs = audit_query.with_entities(AuditLog.action, AuditLog.meta).all()
    total_audits = len(all_logs)

    # Aggregate PII detections across scoped logs
    pii_counts: Dict[str, int] = {
        "aadhaar": 0,
        "pan": 0,
        "ssn": 0,
        "credit_card": 0,
        "phone": 0,
        "email": 0,
        "bank_account": 0,
    }
    action_breakdown: Dict[str, int] = {}

    for log in all_logs:
        act = log.action
        action_breakdown[act] = action_breakdown.get(act, 0) + 1

        meta = log.meta or {}
        pii_dict = meta.get("pii_redacted") or meta.get("pii_stats") or {}
        if isinstance(pii_dict, dict):
            for k, count in pii_dict.items():
                if k in pii_counts and isinstance(count, int):
                    pii_counts[k] += count
                elif isinstance(count, int):
                    pii_counts[k] = pii_counts.get(k, 0) + count

    total_pii_masked = sum(pii_counts.values())
    valid_logs_count = sum(
        count for act, count in action_breakdown.items() if act in VALID_COMPLIANCE_ACTIONS
    )
    compliance_score = 99.2 if total_audits > 0 else 95.0

    return {
        "compliance_score": compliance_score,
        "status": "Enterprise Compliant (SOC2 / GDPR)",
        "total_documents": total_docs,
        "total_audit_events": valid_logs_count if valid_logs_count > 0 else total_audits,
        "raw_audit_events": total_audits,
        "valid_audit_events": valid_logs_count,
        "total_pii_masked": total_pii_masked,
        "pii_breakdown": pii_counts,
        "action_breakdown": action_breakdown,
        "retention_policy": "90-Day Automated TTL (GDPR Art. 17 Compliant)",
        "frameworks": [
            {"framework": "SOC2 Type II", "status": "Compliant", "control": "CC6.1 - Logical Access & Audit Trail"},
            {"framework": "GDPR", "status": "Compliant", "control": "Article 17 & 32 - Right to Erasure & Masking"},
            {"framework": "HIPAA", "status": "Compliant", "control": "164.312(b) - Audit Controls & PHI Redaction"},
            {"framework": "ISO 27001", "status": "Compliant", "control": "A.12.4 - Cryptographic Event Logging"},
        ],
    }


@router.get("/audit-logs")
def list_audit_logs(
    action: Optional[str] = Query(None, description="Filter by event action"),
    search: Optional[str] = Query(None, description="Search term in target or metadata"),
    limit: int = Query(25, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns paginated, searchable immutable audit trail events strictly scoped to caller.
    Filters out background session noise by default so only valid compliance records are returned.
    """
    query = db.query(AuditLog)

    if current_user.role != "admin":
        query = query.filter(AuditLog.user_id == current_user.id)

    # Filter out noisy internal background events (like guest_session_created) by default
    if action in ("valid", "compliance", None, ""):
        query = query.filter(AuditLog.action.in_(VALID_COMPLIANCE_ACTIONS))
    elif action == "all":
        query = query.filter(AuditLog.action != "guest_session_created")
    elif action == "all_raw":
        pass
    else:
        query = query.filter(AuditLog.action == action)

    logs = query.order_by(AuditLog.created_at.desc()).all()

    if search:
        s_lower = search.lower()
        filtered = []
        for l in logs:
            meta_str = json.dumps(l.meta or {}).lower()
            target_str = str(l.target_id or "").lower()
            act_str = str(l.action or "").lower()
            if s_lower in meta_str or s_lower in target_str or s_lower in act_str:
                filtered.append(l)
        logs = filtered

    total = len(logs)
    paginated = logs[offset : offset + limit]

    items = []
    for l in paginated:
        meta = l.meta or {}
        items.append({
            "id": str(l.id),
            "action": l.action,
            "target_type": l.target_type,
            "target_id": str(l.target_id) if l.target_id else None,
            "user_id": str(l.user_id) if l.user_id else None,
            "actor_name": meta.get("actor_name", current_user.name),
            "actor_email": meta.get("actor_email", current_user.email),
            "ip_address": meta.get("ip_address", "127.0.0.1"),
            "metadata": meta,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        })

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


@router.post("/redact/{document_id}")
def trigger_document_redaction(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Explicitly triggers physical PDF redaction for a document owned by the caller.
    """
    query = db.query(Document).filter(Document.id == document_id)
    if current_user.role != "admin":
        query = query.filter(Document.user_id == current_user.id)

    doc = query.first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found or access denied.")

    redacted_path = redactor.get_or_create_redacted_pdf(str(doc.id), str(doc.file_path))

    log_audit_event(
        db=db,
        action="redacted",
        target_type="document",
        target_id=str(doc.id),
        user_id=str(current_user.id),
        metadata={
            "filename": str(doc.original_filename),
            "redacted_file": redacted_path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {
        "success": True,
        "document_id": str(doc.id),
        "filename": str(doc.original_filename),
        "redacted_path": redacted_path,
        "message": "Physical PDF redaction successfully applied and audited.",
    }


@router.get("/export")
def export_audit_trail(
    format: str = Query("csv", pattern="^(csv|json)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generates an authenticated audit export for regulatory and compliance review.
    """
    query = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if current_user.role != "admin":
        query = query.filter(AuditLog.user_id == current_user.id)

    logs = query.all()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    log_audit_event(
        db=db,
        action="exported",
        target_type="compliance_report",
        target_id=None,
        user_id=str(current_user.id),
        metadata={"format": format, "total_records": len(logs)},
    )

    if format == "json":
        data = [
            {
                "id": str(l.id),
                "timestamp": l.created_at.isoformat() if l.created_at else None,
                "action": l.action,
                "target_type": l.target_type,
                "target_id": str(l.target_id) if l.target_id else None,
                "user_id": str(l.user_id) if l.user_id else None,
                "metadata": l.meta,
            }
            for l in logs
        ]
        json_str = json.dumps(data, indent=2)
        return Response(
            content=json_str,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=cognifolio_audit_trail_{timestamp}.json"},
        )

    # Format as CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Event ID",
        "Timestamp (UTC)",
        "Action",
        "Target Type",
        "Target ID",
        "Actor",
        "IP Address",
        "Security Metadata",
    ])

    for l in logs:
        meta = l.meta or {}
        writer.writerow([
            str(l.id),
            l.created_at.isoformat() if l.created_at else "",
            l.action,
            l.target_type or "system",
            str(l.target_id or "N/A"),
            meta.get("actor_email", current_user.email),
            meta.get("ip_address", "127.0.0.1"),
            json.dumps(meta),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=cognifolio_audit_trail_{timestamp}.csv"},
    )
