import re
from typing import Any
from fastapi import APIRouter, Depends
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.entities import Document, DocumentInsight, User
from app.schemas.search import SearchRequest, SearchResponse, SearchResultItem
from app.services.vector_db import vector_service
from app.services.audit import log_audit_event
from app.core import get_current_user

router = APIRouter(prefix="/search", tags=["Semantic Search"])


def _format_search_item(doc: Document, score: float) -> SearchResultItem:
    """
    Safely converts SQLAlchemy Document & DocumentInsight attributes to SearchResultItem,
    preventing InstrumentedAttribute type mismatches with static type checkers.
    """
    insight = doc.insights
    doc_type: str = str(insight.document_type) if (insight and insight.document_type) else str(doc.file_type)
    title: str = str(insight.title) if (insight and insight.title) else str(doc.original_filename)
    summary: str | None = str(insight.summary) if (insight and insight.summary) else None
    points: list[str] = list(insight.key_points[:3]) if (insight and insight.key_points) else []
    entities: dict[str, Any] = dict(insight.entities) if (insight and insight.entities) else {}
    uploaded: str = doc.uploaded_at.strftime("%Y-%m-%d %H:%M") if doc.uploaded_at else "Recently"

    return SearchResultItem(
        document_id=str(doc.id),
        filename=str(doc.filename),
        original_filename=str(doc.original_filename),
        document_type=doc_type,
        title=title,
        summary=summary,
        highlighted_points=points,
        entities=entities,
        similarity_score=round(score, 3),
        uploaded_at=uploaded,
    )


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

    doc_ids = [str(m["document_id"]) for m in raw_matches]
    score_map = {str(m["document_id"]): m["score"] for m in raw_matches}

    # Fetch corresponding relational documents and insights
    query = db.query(Document).filter(Document.id.in_(doc_ids))
    if current_user.role != "admin":
        query = query.filter(Document.user_id == current_user.id)

    docs = query.all() if doc_ids else []

    results = []
    # Key dictionary by str(d.id) to prevent UUID vs str dictionary lookup mismatch
    doc_dict = {str(d.id): d for d in docs}

    for match_id in doc_ids:
        doc = doc_dict.get(str(match_id))
        if not doc:
            continue

        score = float(score_map.get(str(doc.id), 0.85))
        results.append(_format_search_item(doc, score))

    # Fallback keyword search strictly scoped to current user
    if not results and request.query.strip():
        clean_q = request.query.strip()
        kw = f"%{clean_q}%"
        fallback_query = (
            db.query(Document)
            .join(DocumentInsight, isouter=True)
            .filter(
                (Document.original_filename.ilike(kw))
                | (DocumentInsight.title.ilike(kw))
                | (DocumentInsight.summary.ilike(kw))
                | (DocumentInsight.raw_extracted_text.ilike(kw))
            )
        )
        if current_user.role != "admin":
            fallback_query = fallback_query.filter(Document.user_id == current_user.id)

        fallback_docs = fallback_query.limit(request.limit).all()

        # If full phrase ILIKE yielded no matches, tokenize significant keywords
        if not fallback_docs:
            stop_words = {
                "pull", "fetch", "find", "search", "get", "show", "me", "my", "the", "a", "an",
                "of", "in", "for", "with", "doc", "docs", "document", "documents", "file", "files",
                "that", "this", "please", "about", "which", "where", "what", "from"
            }
            tokens = [re.sub(r"\W+", "", w.lower()) for w in clean_q.split()]
            tokens = [w for w in tokens if len(w) >= 3 and w not in stop_words]

            if tokens:
                token_filters = []
                for tok in tokens[:6]:
                    token_kw = f"%{tok}%"
                    token_filters.append(
                        (Document.original_filename.ilike(token_kw))
                        | (DocumentInsight.title.ilike(token_kw))
                        | (DocumentInsight.summary.ilike(token_kw))
                        | (DocumentInsight.raw_extracted_text.ilike(token_kw))
                    )
                token_query = (
                    db.query(Document)
                    .join(DocumentInsight, isouter=True)
                    .filter(or_(*token_filters))
                )
                if current_user.role != "admin":
                    token_query = token_query.filter(Document.user_id == current_user.id)
                fallback_docs = token_query.limit(request.limit).all()

        for doc in fallback_docs:
            results.append(_format_search_item(doc, 0.85))

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
