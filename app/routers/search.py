from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.entities import Document, DocumentInsight
from app.schemas.search import SearchRequest, SearchResponse, SearchResultItem
from app.services.vector_db import vector_service

router = APIRouter(prefix="/search", tags=["Semantic Search"])


@router.post("", response_model=SearchResponse)
def semantic_search(request: SearchRequest, db: Session = Depends(get_db)):
    """
    Executes semantic vector search across all indexed document embeddings
    using natural language queries, with hybrid fallback to database metadata.
    """
    raw_matches = vector_service.search(
        query=request.query,
        limit=request.limit,
        doc_type=request.document_type,
    )

    doc_ids = [m["document_id"] for m in raw_matches]
    score_map = {m["document_id"]: m["score"] for m in raw_matches}

    # Fetch corresponding relational documents and insights
    docs = (
        db.query(Document)
        .filter(Document.id.in_(doc_ids))
        .all()
    ) if doc_ids else []

    results = []
    # Retain the ranking returned by the vector search
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

    # If vector search returned 0 (e.g. fresh DB before many vectors), try fallback keyword search
    if not results and request.query.strip():
        kw = f"%{request.query.strip()}%"
        fallback_docs = (
            db.query(Document)
            .join(DocumentInsight, isouter=True)
            .filter(
                (Document.original_filename.ilike(kw))
                | (DocumentInsight.summary.ilike(kw))
                | (DocumentInsight.raw_extracted_text.ilike(kw))
            )
            .limit(request.limit)
            .all()
        )
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

    return SearchResponse(
        query=request.query,
        total_matches=len(results),
        results=results,
    )
