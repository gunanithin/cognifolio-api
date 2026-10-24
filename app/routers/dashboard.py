from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models.entities import Document, DocumentInsight

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/stats")
def get_dashboard_stats(db: Session = Depends(get_db)):
    """
    Returns aggregated metrics and charts dataset across all indexed enterprise documents.
    """
    total_docs = db.query(func.count(Document.id)).scalar() or 0
    ready_docs = db.query(func.count(Document.id)).filter(Document.status == "ready").scalar() or 0

    # Aggregate by document type
    type_counts = (
        db.query(DocumentInsight.document_type, func.count(DocumentInsight.id))
        .group_by(DocumentInsight.document_type)
        .all()
    )
    doc_types_breakdown = {t: c for t, c in type_counts if t}

    # Aggregate recent documents
    recent_docs = (
        db.query(Document)
        .order_by(Document.uploaded_at.desc())
        .limit(5)
        .all()
    )

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

    # Auto-compile vendor/category chart data from all invoices & contracts
    all_insights = db.query(DocumentInsight).all()
    vendor_spending = {}
    for ins in all_insights:
        if ins.entities and isinstance(ins.entities, dict):
            vendor = ins.entities.get("vendor_or_issuer")
            amt_str = ins.entities.get("total_amount")
            if vendor and amt_str:
                # Basic numeric parsing
                import re
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
                "labels": list(doc_types_breakdown.keys()) or ["No Data"],
                "values": list(doc_types_breakdown.values()) or [0],
            },
            "by_vendor": {
                "labels": list(vendor_spending.keys()) or ["Sample Vendor"],
                "values": list(vendor_spending.values()) or [1000],
            },
        },
        "recent_documents": recent_items,
    }
