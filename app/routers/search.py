from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.entities import Document, DocumentInsight, User
from app.schemas.search import SearchRequest, SearchResponse, SearchResultItem
from app.services.vector_db import vector_service
from app.services.audit import log_audit_event
from app.core import get_current_user

router = APIRouter(prefix="/search", tags=["Semantic Search"])


@router.post("", response_model=SearchResponse)
def semantic_search(
    request: SearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Executes semantic vector search strictly scoped to the active user's documents.
    Prevents cross-tenant vector data leakage.
    """
    scoped_user_id = None if current_user.role == "admin" else str(current_user.id)

    raw_matches = vector_service.search(
        query=request.query,
        limit=request.limit,
        user_id=scoped_user_id,
        doc_type=request.document_type,
    )

    doc_ids = [m["document_id"] for m in raw_matches]
    score_map = {m["document_id"]: m["score"] for m in raw_matches}

    # Fetch corresponding relational documents and insights
    query = db.query(Document).filter(Document.id.in_(doc_ids))
    if current_user.role != "admin":
        query = query.filter(Document.user_id == current_user.id)

    docs = query.all() if doc_ids else []

    results = []
    doc_dict = {d.id: d for d in docs}

    for match_id in doc_ids:
        doc = doc_dict.get(match_id)
        if not doc:
            continue

        insight = doc.insights
        results.append(
            SearchResultItem(
                document_id=doc.id,
                filename=doc.filename,
                original_filename=doc.original_filename,
                document_type=insight.document_type if insight else doc.file_type,
                title=insight.title if insight else doc.original_filename,
                summary=insight.summary if insight else None,
                highlighted_points=insight.key_points[:3] if insight and insight.key_points else [],
                entities=insight.entities if insight and insight.entities else {},
                similarity_score=round(score_map.get(doc.id, 0.0), 3),
                uploaded_at=doc.uploaded_at.strftime("%Y-%m-%d %H:%M"),
            )
        )

    # Fallback keyword search strictly scoped to current user
    if not results and request.query.strip():
        kw = f"%{request.query.strip()}%"
        fallback_query = (
            db.query(Document)
            .join(DocumentInsight, isouter=True)
            .filter(
                (Document.original_filename.ilike(kw))
                | (DocumentInsight.summary.ilike(kw))
                | (DocumentInsight.raw_extracted_text.ilike(kw))
            )
        )
        if current_user.role != "admin":
            fallback_query = fallback_query.filter(Document.user_id == current_user.id)

        fallback_docs = fallback_query.limit(request.limit).all()

        for doc in fallback_docs:
            insight = doc.insights
            results.append(
                SearchResultItem(
                    document_id=doc.id,
                    filename=doc.filename,
                    original_filename=doc.original_filename,
                    document_type=insight.document_type if insight else doc.file_type,
                    title=insight.title if insight else doc.original_filename,
                    summary=insight.summary if insight else None,
                    highlighted_points=insight.key_points[:3] if insight and insight.key_points else [],
                    entities=insight.entities if insight and insight.entities else {},
                    similarity_score=0.85,
                    uploaded_at=doc.uploaded_at.strftime("%Y-%m-%d %H:%M"),
                )
            )

    # Log search audit event
    log_audit_event(
        db=db,
        action="searched",
        target_type="search_query",
        target_id=None,
        user_id=str(current_user.id),
        metadata={
            "query": request.query,
            "document_type_filter": request.document_type,
            "matches_found": len(results),
        },
    )

    return SearchResponse(
        query=request.query,
        total_matches=len(results),
        results=results,
    )
