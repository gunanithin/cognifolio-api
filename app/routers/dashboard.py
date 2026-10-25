import re
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models.entities import Document, DocumentInsight, User
from app.core import get_current_user

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/stats")
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns aggregated metrics and charts dataset scoped strictly to the authenticated user.
    """
    doc_query = db.query(Document)
    if current_user.role != "admin":
        doc_query = doc_query.filter(Document.user_id == current_user.id)

    total_docs = doc_query.count()
    ready_docs = doc_query.filter(Document.status == "ready").count()

    # Aggregate by document type for current user
    type_query = (
        db.query(DocumentInsight.document_type, func.count(DocumentInsight.id))
        .join(Document, Document.id == DocumentInsight.document_id)
    )
    if current_user.role != "admin":
        type_query = type_query.filter(Document.user_id == current_user.id)

    type_counts = type_query.group_by(DocumentInsight.document_type).all()
    doc_types_breakdown = {t: c for t, c in type_counts if t}

    # Aggregate recent documents for current user
    recent_query = db.query(Document)
    if current_user.role != "admin":
        recent_query = recent_query.filter(Document.user_id == current_user.id)

    recent_docs = recent_query.order_by(Document.uploaded_at.desc()).limit(5).all()

    recent_items = []
    for d in recent_docs:
        insight = d.insights
        recent_items.append({
            "id": str(d.id),
            "filename": d.original_filename,
            "document_type": insight.document_type if insight else d.file_type,
            "status": d.status,
            "uploaded_at": d.uploaded_at.strftime("%Y-%m-%d %H:%M"),
            "summary": (insight.summary[:140] + "...") if (insight and insight.summary) else "Processing...",
        })

    # Auto-compile vendor/category chart data from invoices & contracts belonging to user
    insights_query = db.query(DocumentInsight).join(Document, Document.id == DocumentInsight.document_id)
    if current_user.role != "admin":
        insights_query = insights_query.filter(Document.user_id == current_user.id)

    all_insights = insights_query.all()
    vendor_spending = {}
    for ins in all_insights:
        if ins.entities and isinstance(ins.entities, dict):
            vendor = ins.entities.get("vendor_or_issuer")
            amt_str = ins.entities.get("total_amount")
            if vendor and amt_str:
                clean_num = re.sub(r"[^\d.]", "", str(amt_str))
                try:
                    val = float(clean_num)
                    vendor_spending[vendor] = vendor_spending.get(vendor, 0.0) + val
                except ValueError:
                    pass

    return {
        "overview": {
            "total_documents": total_docs,
            "processed_successfully": ready_docs,
            "total_vendors_identified": len(vendor_spending),
        },
        "charts": {
            "by_type": {
                "labels": list(doc_types_breakdown.keys()),
                "data": list(doc_types_breakdown.values()),
            },
            "vendor_breakdown": {
                "labels": list(vendor_spending.keys()),
                "data": [round(v, 2) for v in vendor_spending.values()],
            },
        },
        "recent_documents": recent_items,
    }
