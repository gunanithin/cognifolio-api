import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models.entities import Document, DocumentInsight, User, AuditLog
from app.schemas.document import DocumentResponse, DocumentListResponse
from app.schemas.chat import DocumentChatRequest, DocumentChatResponse
from app.schemas.comparison import DocumentCompareRequest, DocumentCompareResponse
from app.services.pdf_extractor import extractor
from app.services.redactor import redactor
from app.services.gemini import gemini_analyzer
from app.services.vector_db import vector_service
from app.services.audit import log_audit_event

router = APIRouter(prefix="/documents", tags=["Documents"])


def get_or_create_default_user(db: Session) -> User:
    user = db.query(User).first()
    if not user:
        user = User(
            id=str(uuid.uuid4()),
            email="demo@cognifolio.ai",
            name="Demo Analyst",
            password_hash="mock",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    retention_days: int = Form(default=90),
    db: Session = Depends(get_db),
):
    """
    Upload a document, extract content, redact PII, process with Gemini Flash,
    and index in both relational DB and Qdrant vector database.
    """
    ext = Path(file.filename).suffix.lower().replace(".", "")
    if ext not in settings.allowed_extensions_list:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{ext}'. Allowed: {settings.ALLOWED_EXTENSIONS}",
        )

    user = get_or_create_default_user(db)
    doc_id = str(uuid.uuid4())
    stored_filename = f"{doc_id}_{file.filename}"
    file_path = os.path.join(settings.UPLOAD_DIR, stored_filename)

    # Save uploaded file
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

    file_size = os.path.getsize(file_path)

    # Create initial document record
    doc = Document(
        id=doc_id,
        user_id=user.id,
        filename=stored_filename,
        original_filename=file.filename,
        file_path=file_path,
        file_size_bytes=file_size,
        file_type=ext,
        mime_type=file.content_type,
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

    # Pre-generate physically redacted PDF if PDF file
    if ext == "pdf":
        try:
            redactor.get_or_create_redacted_pdf(doc.id, file_path)
        except Exception:
            pass

    # 3. AI Analysis (Gemini Flash)
    ai_insights = await gemini_analyzer.analyze_document(
        sanitized_text=sanitized_text,
        filename=file.filename,
        file_path=file_path,
        is_scanned=is_scanned,
    )

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

    # 5. Vector indexing in Qdrant
    searchable_blob = f"{ai_insights['title']}\n{ai_insights['summary']}\n{' '.join(ai_insights['key_points'])}\n{sanitized_text[:4000]}"
    vector_service.upsert_document(
        doc_id=str(doc.id),
        text=searchable_blob,
        metadata={
            "document_id": str(doc.id),
            "filename": doc.original_filename,
            "document_type": ai_insights["document_type"],
            "user_id": str(user.id),
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
        user_id=str(user.id),
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
):
    """
    Automated redline diff comparison between two documents (Doc A vs Doc B).
    Returns added, removed, and modified clauses, financial deltas, and risk changes.
    """
    doc_a = db.query(Document).filter(Document.id == request.base_document_id).first()
    if not doc_a:
        raise HTTPException(status_code=404, detail="Base document (Doc A) not found.")

    doc_b = db.query(Document).filter(Document.id == request.target_document_id).first()
    if not doc_b:
        raise HTTPException(status_code=404, detail="Target document (Doc B) not found.")

    insight_a = doc_a.insights
    insight_b = doc_b.insights
    if not insight_a or not insight_b:
        raise HTTPException(status_code=400, detail="One or both documents are still processing insights.")

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
        focus_aspects=request.focus_aspects,
    )

    # Log immutable audit event
    log_audit_event(
        db=db,
        action="document_comparison",
        target_type="document_pair",
        target_id=str(doc_a.id),
        user_id=str(doc_a.user_id),
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
def list_documents(
    doc_type: str = Query(None),
    status: str = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(Document)
    if status:
        query = query.filter(Document.status == status)
    if doc_type:
        query = query.join(DocumentInsight).filter(DocumentInsight.document_type == doc_type)

    docs = query.order_by(Document.uploaded_at.desc()).all()
    return {"total": len(docs), "items": docs}


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(document_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/{document_id}/file")
def get_document_file(document_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc or not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="File not found on storage")

    # Record view audit trail
    log_audit_event(
        db=db,
        action="viewed",
        target_type="document",
        target_id=str(doc.id),
        user_id=str(doc.user_id),
        metadata={"filename": doc.original_filename, "mime_type": doc.mime_type},
    )

    return FileResponse(
        doc.file_path,
        media_type=doc.mime_type or "application/octet-stream",
        filename=doc.original_filename,
    )


@router.get("/{document_id}/redacted-file")
def get_redacted_document_file(document_id: str, db: Session = Depends(get_db)):
    """
    Returns the physically sanitized, blacked-out redacted PDF for compliance and secure sharing.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc or not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="Source document not found")

    redacted_path = redactor.get_or_create_redacted_pdf(doc.id, doc.file_path)
    if not os.path.exists(redacted_path):
        raise HTTPException(status_code=500, detail="Failed to locate or generate redacted document")

    # Record audit trail
    log_audit_event(
        db=db,
        action="viewed_redacted",
        target_type="document",
        target_id=str(doc.id),
        user_id=str(doc.user_id),
        metadata={
            "filename": doc.original_filename,
            "redacted_filename": os.path.basename(redacted_path),
        },
    )

    base_name = Path(doc.original_filename).stem
    download_filename = f"{base_name}_REDACTED.pdf"

    return FileResponse(
        redacted_path,
        media_type="application/pdf",
        filename=download_filename,
    )


@router.delete("/{document_id}")
@router.delete("/{document_id}/")
def delete_document(document_id: str, db: Session = Depends(get_db)):
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
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
        user_id=str(doc.user_id),
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
):
    """
    Conversational RAG assistant grounded strictly in the target document.
    Returns answers with clickable citations that navigate to the exact PDF coordinates.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    insight = doc.insights
    if not insight:
        raise HTTPException(status_code=400, detail="Document insights not available yet.")

    insights_data = {
        "entities": insight.entities or {},
        "risk_clauses": insight.risk_clauses or [],
        "line_items": insight.line_items or [],
        "key_points": insight.key_points or [],
    }

    raw_text = insight.raw_extracted_text or insight.summary or ""

    chat_result = await gemini_analyzer.chat_with_document(
        document_title=insight.title or doc.original_filename,
        document_type=insight.document_type or "Document",
        summary=insight.summary or "",
        raw_text=raw_text,
        insights_data=insights_data,
        message=request.message,
        conversation_history=request.conversation_history,
    )

    # Log immutable audit event for enterprise security
    log_audit_event(
        db=db,
        action="chat_query",
        target_type="document",
        target_id=str(doc.id),
        user_id=str(doc.user_id),
        metadata={
            "filename": doc.original_filename,
            "query": request.message[:200],
            "citations_returned": len(chat_result.get("citations", [])),
        },
    )

    return DocumentChatResponse(
        document_id=str(doc.id),
        reply=chat_result["reply"],
        citations=chat_result["citations"],
        suggested_followups=chat_result["suggested_followups"],
    )



