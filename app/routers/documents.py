import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.entities import Document, DocumentInsight, User
from app.schemas.document import DocumentResponse, DocumentListResponse
from app.schemas.chat import DocumentChatRequest, DocumentChatResponse
from app.schemas.comparison import DocumentCompareRequest, DocumentCompareResponse
from app.services.pdf_extractor import extractor
from app.services.redactor import redactor
from app.services.gemini import gemini_analyzer
from app.services.vector_db import vector_service
from app.services.audit import log_audit_event
from app.core import get_current_user, get_optional_user, decode_access_token

router = APIRouter(prefix="/documents", tags=["Documents & Multimodal Intelligence"])


def resolve_file_user(
    token: Optional[str],
    current_user: Optional[User],
    db: Session,
) -> User:
    """
    Enterprise helper for file streaming endpoints.
    Resolves caller via Bearer header or query token (essential for browser PDF iframe/window plugins).
    """
    if current_user:
        return current_user
    if token:
        try:
            payload = decode_access_token(token)
            user_id = payload.get("sub")
            if user_id:
                user = db.query(User).filter(User.id == user_id).first()
                if user:
                    return user
        except Exception:
            pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required to view document file.",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    retention_days: int = Form(default=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Secure document ingestion strictly scoped to the authenticated caller.
    Extracts text, redacts PII, runs Gemini 3.5 Flash analysis, and indexes in Qdrant with tenant payload.
    """
    safe_filename = file.filename or "uploaded_document"
    ext = Path(safe_filename).suffix.lower().replace(".", "")
    if ext not in settings.allowed_extensions_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format '{ext}'. Allowed: {settings.ALLOWED_EXTENSIONS}",
        )

    doc_id = str(uuid.uuid4())
    stored_filename = f"{doc_id}_{safe_filename}"
    file_path = os.path.join(settings.UPLOAD_DIR, stored_filename)

    # Save physical file
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    file_size = os.path.getsize(file_path)

    # Scoped strictly to current_user
    doc = Document(
        id=doc_id,
        user_id=current_user.id,
        filename=stored_filename,
        original_filename=file.filename,
        file_path=file_path,
        file_size_bytes=file_size,
        file_type=ext,
        mime_type=file.content_type,
        page_count=1,
        status="processing",
        retention_days=retention_days,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # 1. Extraction
    extraction_res = extractor.extract(file_path)
    raw_text = extraction_res["text"]
    page_count = extraction_res["page_count"]
    is_scanned = extraction_res["is_scanned"]

    # 2. PII Redaction
    sanitized_text, pii_stats = redactor.redact(raw_text)

    # Pre-generate physically redacted file (PDF, Image, or Text)
    try:
        redactor.get_or_create_redacted_file(doc.id, file_path)
    except Exception:
        pass

    # 3. AI Analysis (Gemini 3.5 Flash)
    ai_insights = await gemini_analyzer.analyze_document(
        sanitized_text=sanitized_text,
        filename=safe_filename,
        file_path=file_path,
        is_scanned=is_scanned,
    )

    # If image transcription was performed by Gemini, capture it in sanitized_text & index
    if ai_insights.get("raw_extracted_text") and (not raw_text or raw_text.startswith("[Scanned Image")):
        gemini_text = ai_insights["raw_extracted_text"]
        sanitized_text, extra_pii = redactor.redact(gemini_text)
        for k, v in extra_pii.items():
            pii_stats[k] = pii_stats.get(k, 0) + v

    # 4. Save Insights in DB
    insight = DocumentInsight(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        document_type=ai_insights["document_type"],
        title=ai_insights["title"],
        summary=ai_insights["summary"],
        key_points=ai_insights["key_points"],
        entities=ai_insights["entities"],
        line_items=ai_insights["line_items"],
        action_items=ai_insights["action_items"],
        risk_clauses=ai_insights["risk_clauses"],
        charts_data=ai_insights["charts_data"],
        raw_extracted_text=sanitized_text,
        confidence_score=ai_insights["confidence_score"],
    )
    db.add(insight)

    # Update document status
    doc.page_count = page_count
    doc.status = "ready"
    doc.processed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(doc)

    # 5. Vector indexing in Qdrant with tenant isolation
    searchable_blob = f"{ai_insights['title']}\n{ai_insights['summary']}\n{' '.join(ai_insights['key_points'])}\n{sanitized_text[:4000]}"
    vector_service.upsert_document(
        doc_id=str(doc.id),
        text=searchable_blob,
        metadata={
            "document_id": str(doc.id),
            "filename": doc.original_filename,
            "document_type": ai_insights["document_type"],
            "user_id": str(current_user.id),
            "title": ai_insights["title"],
            "summary": ai_insights["summary"][:250],
            "uploaded_at": doc.uploaded_at.isoformat(),
        },
    )

    # Log immutable audit event
    log_audit_event(
        db=db,
        action="uploaded",
        target_type="document",
        target_id=str(doc.id),
        user_id=str(current_user.id),
        metadata={
            "filename": file.filename,
            "pii_redacted": pii_stats,
            "file_size_bytes": file_size,
            "page_count": page_count,
        },
    )

    return doc


@router.post("/compare", response_model=DocumentCompareResponse)
async def compare_documents(
    request: DocumentCompareRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Automated redline diff comparison between two documents (Doc A vs Doc B).
    Strictly verifies ownership: both documents must belong to the caller.
    """
    doc_a_query = db.query(Document).filter(Document.id == request.base_document_id)
    doc_b_query = db.query(Document).filter(Document.id == request.target_document_id)

    if current_user.role != "admin":
        doc_a_query = doc_a_query.filter(Document.user_id == current_user.id)
        doc_b_query = doc_b_query.filter(Document.user_id == current_user.id)

    doc_a = doc_a_query.first()
    if not doc_a:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Base document (Doc A) not found or access denied.",
        )

    doc_b = doc_b_query.first()
    if not doc_b:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target document (Doc B) not found or access denied.",
        )

    insight_a = doc_a.insights
    insight_b = doc_b.insights
    if not insight_a or not insight_b:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or both documents are still processing insights.",
        )

    insights_data_a = {
        "entities": insight_a.entities or {},
        "risk_clauses": insight_a.risk_clauses or [],
        "line_items": insight_a.line_items or [],
        "key_points": insight_a.key_points or [],
    }
    insights_data_b = {
        "entities": insight_b.entities or {},
        "risk_clauses": insight_b.risk_clauses or [],
        "line_items": insight_b.line_items or [],
        "key_points": insight_b.key_points or [],
    }

    raw_text_a = insight_a.raw_extracted_text or insight_a.summary or ""
    raw_text_b = insight_b.raw_extracted_text or insight_b.summary or ""

    compare_result = await gemini_analyzer.compare_documents(
        doc_a_title=insight_a.title or doc_a.original_filename,
        doc_a_type=insight_a.document_type or "Document",
        doc_a_summary=insight_a.summary or "",
        doc_a_text=raw_text_a,
        doc_a_insights=insights_data_a,
        doc_b_title=insight_b.title or doc_b.original_filename,
        doc_b_type=insight_b.document_type or "Document",
        doc_b_summary=insight_b.summary or "",
        doc_b_text=raw_text_b,
        doc_b_insights=insights_data_b,
        focus_aspects=request.focus_aspects or [],
    )

    # Log immutable audit event
    log_audit_event(
        db=db,
        action="document_comparison",
        target_type="document_pair",
        target_id=str(doc_a.id),
        user_id=str(current_user.id),
        metadata={
            "doc_a_filename": doc_a.original_filename,
            "doc_b_id": str(doc_b.id),
            "doc_b_filename": doc_b.original_filename,
            "diffs_count": len(compare_result.get("diffs", [])),
            "similarity_score": compare_result.get("metrics", {}).get("similarity_score"),
            "risk_delta": compare_result.get("metrics", {}).get("risk_delta"),
        },
    )

    return DocumentCompareResponse(
        base_document_id=str(doc_a.id),
        base_document_title=insight_a.title or doc_a.original_filename,
        base_document_type=insight_a.document_type or "Document",
        target_document_id=str(doc_b.id),
        target_document_title=insight_b.title or doc_b.original_filename,
        target_document_type=insight_b.document_type or "Document",
        executive_summary=compare_result.get("executive_summary", ""),
        metrics=compare_result.get("metrics", {}),
        diffs=compare_result.get("diffs", []),
        key_recommendations=compare_result.get("key_recommendations", []),
    )


@router.get("", response_model=DocumentListResponse)
@router.get("/", response_model=DocumentListResponse)
def list_documents(
    doc_type: str = Query(None),
    status: str = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lists indexed documents strictly scoped to the active user.
    """
    query = db.query(Document)
    if current_user.role != "admin":
        query = query.filter(Document.user_id == current_user.id)

    if status:
        query = query.filter(Document.status == status)
    if doc_type:
        query = query.join(DocumentInsight).filter(DocumentInsight.document_type == doc_type)

    docs = query.order_by(Document.uploaded_at.desc()).all()
    return {"total": len(docs), "items": docs}


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieves document metadata ensuring tenant ownership.
    """
    query = db.query(Document).filter(Document.id == document_id)
    if current_user.role != "admin":
        query = query.filter(Document.user_id == current_user.id)

    doc = query.first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found or access denied.")
    return doc


@router.get("/{document_id}/file")
def get_document_file(
    document_id: str,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """
    Streams original document file. Resolves authentication via header or ?token= param.
    """
    caller = resolve_file_user(token=token, current_user=current_user, db=db)

    query = db.query(Document).filter(Document.id == document_id)
    if caller.role != "admin":
        query = query.filter(Document.user_id == caller.id)

    doc = query.first()
    if not doc or not os.path.exists(doc.file_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found on storage.")

    # Record view audit trail
    log_audit_event(
        db=db,
        action="viewed",
        target_type="document",
        target_id=str(doc.id),
        user_id=str(caller.id),
        metadata={"filename": doc.original_filename, "mime_type": doc.mime_type},
    )

    return FileResponse(
        doc.file_path,
        media_type=doc.mime_type or "application/octet-stream",
        filename=doc.original_filename,
    )


@router.get("/{document_id}/redacted-file")
def get_redacted_document_file(
    document_id: str,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """
    Returns the physically sanitized, blacked-out redacted PDF for compliance and secure sharing.
    """
    caller = resolve_file_user(token=token, current_user=current_user, db=db)

    query = db.query(Document).filter(Document.id == document_id)
    if caller.role != "admin":
        query = query.filter(Document.user_id == caller.id)

    doc = query.first()
    if not doc or not os.path.exists(doc.file_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source document not found.")

    redacted_path = redactor.get_or_create_redacted_file(doc.id, doc.file_path)
    if not os.path.exists(redacted_path):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to locate or generate redacted document.")

    # Record audit trail
    log_audit_event(
        db=db,
        action="viewed_redacted",
        target_type="document",
        target_id=str(doc.id),
        user_id=str(caller.id),
        metadata={
            "filename": doc.original_filename,
            "redacted_filename": os.path.basename(redacted_path),
        },
    )

    base_name = Path(doc.original_filename).stem
    redacted_ext = Path(redacted_path).suffix.lower()
    download_filename = f"{base_name}_REDACTED{redacted_ext}"

    media_type = doc.mime_type or "application/octet-stream"
    if redacted_ext == ".pdf":
        media_type = "application/pdf"
    elif redacted_ext in [".png", ".jpg", ".jpeg", ".webp"]:
        media_type = f"image/{redacted_ext.replace('.', '')}"
    elif redacted_ext in [".txt", ".md"]:
        media_type = "text/plain; charset=utf-8"

    return FileResponse(
        redacted_path,
        media_type=media_type,
        filename=download_filename,
    )


@router.delete("/{document_id}")
@router.delete("/{document_id}/")
def delete_document(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Idempotent deletion scoped strictly to the caller's documents.
    """
    try:
        query = db.query(Document).filter(Document.id == document_id)
        if current_user.role != "admin":
            query = query.filter(Document.user_id == current_user.id)
        doc = query.first()
    except Exception:
        db.rollback()
        doc = None

    if not doc:
        return {"success": True, "message": f"Document {document_id} was already purged or does not exist."}

    # Remove physical original file
    if os.path.exists(doc.file_path):
        try:
            os.remove(doc.file_path)
        except Exception:
            pass

    # Remove redacted file if generated
    redacted_path = os.path.join(settings.UPLOAD_DIR, f"{doc.id}_redacted.pdf")
    if os.path.exists(redacted_path):
        try:
            os.remove(redacted_path)
        except Exception:
            pass

    # Remove vector index
    vector_service.delete_document(doc.id)

    # Log deletion audit event before purging from DB
    log_audit_event(
        db=db,
        action="deleted",
        target_type="document",
        target_id=str(doc.id),
        user_id=str(current_user.id),
        metadata={"filename": doc.original_filename, "purged": True},
    )

    # Remove from DB
    db.delete(doc)
    db.commit()
    return {"success": True, "message": f"Document {document_id} and its vector data were purged."}


@router.post("/{document_id}/chat", response_model=DocumentChatResponse)
async def chat_with_document(
    document_id: str,
    request: DocumentChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Conversational RAG assistant grounded strictly in the target document.
    Enforces tenant access verification.
    """
    query = db.query(Document).filter(Document.id == document_id)
    if current_user.role != "admin":
        query = query.filter(Document.user_id == current_user.id)

    doc = query.first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found or access denied.")

    insight = doc.insights
    if not insight:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Document insights not available yet.")

    insights_data = {
        "entities": insight.entities or {},
        "risk_clauses": insight.risk_clauses or [],
        "line_items": insight.line_items or [],
        "key_points": insight.key_points or [],
    }

    raw_text = insight.raw_extracted_text or insight.summary or ""

    chat_result = await gemini_analyzer.chat_with_document(
        document_title=str(insight.title or doc.original_filename),
        document_type=str(insight.document_type or "Document"),
        summary=str(insight.summary or ""),
        raw_text=raw_text,
        insights_data=insights_data,
        message=request.message,
        conversation_history=request.conversation_history,
    )

    # Log audit event
    log_audit_event(
        db=db,
        action="chat_query",
        target_type="document",
        target_id=str(doc.id),
        user_id=str(current_user.id),
        metadata={
            "query": request.message,
            "filename": str(doc.original_filename),
            "citations_returned": len(chat_result.get("citations", [])),
        },
    )

    return DocumentChatResponse(
        document_id=str(doc.id),
        reply=chat_result.get("reply", "No response generated."),
        citations=chat_result.get("citations", []),
        suggested_followups=chat_result.get("suggested_followups", []),
    )
