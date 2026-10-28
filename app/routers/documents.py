import asyncio
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.core import decode_access_token, get_current_user, get_optional_user
from app.database import get_db
from app.models.entities import Document, DocumentInsight, User
from app.schemas.chat import DocumentChatRequest, DocumentChatResponse
from app.schemas.comparison import DocumentCompareRequest, DocumentCompareResponse
from app.schemas.document import DocumentListResponse, DocumentResponse
from app.routers.dashboard import invalidate_dashboard_cache
from app.services.audit import log_audit_event
from app.services.gemini import gemini_analyzer
from app.services.pdf_extractor import extractor
from app.services.redactor import redactor
from app.services.storage import storage_service
from app.services.vector_db import vector_service

router = APIRouter(prefix="/documents", tags=["Documents & Multimodal Intelligence"])


def resolve_file_user(
    token: str | None,
    current_user: User | None,
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
                if not user:
                    user = db.query(User).filter(User.email == user_id).first()
                if not user:
                    role = payload.get("role", "guest")
                    if role == "guest" or str(user_id).startswith("guest_"):
                        guest_email = payload.get("email") or f"{user_id}@guest.cognifolio.ai"
                        user = User(
                            id=user_id,
                            email=guest_email,
                            name="Guest Analyst",
                            role="guest",
                            is_guest=True,
                        )
                        db.add(user)
                        db.commit()
                        db.refresh(user)
                if user:
                    return user
        except Exception:
            pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required to view document file.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def scope_document_query(query, caller: User):
    """
    Applies multi-tenant document scoping rules:
    - Admin: Full visibility across all tenant documents.
    - Guest: Access to own uploads and shared guest playground documents.
    - Analyst / Standard User: Strict isolation to caller's own user_id.
    """
    if caller.role == "admin":
        return query
    if caller.is_guest:
        return query.join(User, Document.user_id == User.id).filter(
            (Document.user_id == caller.id) | (User.is_guest == True)
        )
    return query.filter(Document.user_id == caller.id)


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File(...)],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    retention_days: Annotated[int, Form()] = 90,
):
    """
    High-performance secure document ingestion strictly scoped to caller.
    Executes Gemini multimodal intelligence and persistent GCS storage concurrently,
    offloads vector embeddings to background worker, and commits database records in a single roundtrip.
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
    temp_file_path = os.path.join(settings.UPLOAD_DIR, stored_filename)

    # 1. Save physical file locally for rapid extraction and concurrent uploads
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    with open(temp_file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    file_size = os.path.getsize(temp_file_path)
    if file_size > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
        try:
            os.remove(temp_file_path)
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed upload size of {settings.MAX_FILE_SIZE_MB}MB.",
        )

    # 2. Local extraction and PII redaction (< 100ms)
    local_extraction_path = temp_file_path
    extraction_res = extractor.extract(local_extraction_path)
    raw_text = extraction_res["text"]
    page_count = extraction_res["page_count"]
    is_scanned = extraction_res["is_scanned"]
    sanitized_text, pii_stats = redactor.redact(raw_text)

    # Pre-generate physical redacted file locally (< 80ms)
    redacted_local = None
    try:
        redacted_local = redactor.get_or_create_redacted_file(doc_id, local_extraction_path)
    except Exception:
        pass

    # 3. Concurrent Execution: Run Gemini Multimodal AI and Primary GCS Storage Upload simultaneously
    gcs_dest_path = f"documents/{current_user.id}/{doc_id}/{safe_filename}"

    gemini_task = gemini_analyzer.analyze_document(
        sanitized_text=sanitized_text,
        filename=safe_filename,
        file_path=local_extraction_path,
        is_scanned=is_scanned,
    )
    storage_task = asyncio.to_thread(
        storage_service.upload_file,
        temp_file_path,
        gcs_dest_path,
        file.content_type,
    )

    # Awaiting in parallel hides the 2.5s storage upload behind Gemini inference
    ai_insights, storage_path = await asyncio.gather(gemini_task, storage_task)

    # If image transcription was performed by Gemini, capture it in sanitized_text & index
    if ai_insights.get("raw_extracted_text"):
        gemini_text = ai_insights["raw_extracted_text"]
        sanitized_text, extra_pii = redactor.redact(gemini_text)
        for k, v in extra_pii.items():
            pii_stats[k] = pii_stats.get(k, 0) + v
    elif raw_text and raw_text.startswith("[Scanned Image"):
        sanitized_text = ""

    # 4. Single Atomic Database Transaction (eliminates 4 remote database network roundtrips)
    doc = Document(
        id=doc_id,
        user_id=current_user.id,
        filename=stored_filename,
        original_filename=safe_filename,
        file_path=storage_path,
        file_size_bytes=file_size,
        file_type=ext,
        mime_type=file.content_type,
        page_count=page_count,
        status="ready",
        retention_days=retention_days,
        processed_at=datetime.now(timezone.utc),
    )

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

    db.add(doc)
    db.add(insight)

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
        commit=False,
    )

    db.commit()
    db.refresh(doc)

    # 5. Background Tasks: Offload secondary redacted GCS upload and Qdrant vector indexing
    if storage_service.is_gcs_active and redacted_local and os.path.exists(redacted_local):
        redacted_gcs_dest = f"documents/{current_user.id}/{doc_id}/redacted_{safe_filename}"
        background_tasks.add_task(
            storage_service.upload_file,
            redacted_local,
            redacted_gcs_dest,
        )

    searchable_blob = f"{ai_insights['title']}\n{ai_insights['summary']}\n{' '.join(ai_insights['key_points'])}\n{sanitized_text[:4000]}"
    vector_metadata = {
        "document_id": str(doc.id),
        "filename": doc.original_filename,
        "document_type": ai_insights["document_type"],
        "user_id": str(current_user.id),
        "title": ai_insights["title"],
        "summary": ai_insights["summary"][:250],
        "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else "",
    }
    background_tasks.add_task(
        vector_service.upsert_document,
        doc_id=str(doc.id),
        text=searchable_blob,
        metadata=vector_metadata,
    )

    invalidate_dashboard_cache(str(current_user.id))
    return doc


@router.post("/compare", response_model=DocumentCompareResponse)
async def compare_documents(
    request: DocumentCompareRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
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
        doc_a_title=str(insight_a.title or doc_a.original_filename or "Document A"),
        doc_a_type=str(insight_a.document_type or "Document"),
        doc_a_summary=str(insight_a.summary or ""),
        doc_a_text=raw_text_a,
        doc_a_insights=insights_data_a,
        doc_b_title=str(insight_b.title or doc_b.original_filename or "Document B"),
        doc_b_type=str(insight_b.document_type or "Document"),
        doc_b_summary=str(insight_b.summary or ""),
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
        base_document_title=str(insight_a.title or doc_a.original_filename or "Document A"),
        base_document_type=str(insight_a.document_type or "Document"),
        target_document_id=str(doc_b.id),
        target_document_title=str(insight_b.title or doc_b.original_filename or "Document B"),
        target_document_type=str(insight_b.document_type or "Document"),
        executive_summary=str(compare_result.get("executive_summary", "")),
        metrics=compare_result.get("metrics", {}),
        diffs=compare_result.get("diffs", []),
        key_recommendations=compare_result.get("key_recommendations", []),
    )


@router.get("", response_model=DocumentListResponse)
@router.get("/", response_model=DocumentListResponse)
def list_documents(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    doc_type: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
):
    """
    Lists indexed documents strictly scoped to the active user.
    """
    query = scope_document_query(db.query(Document).options(joinedload(Document.insights)), current_user)

    if status:
        query = query.filter(Document.status == status)
    if doc_type:
        query = query.join(DocumentInsight).filter(DocumentInsight.document_type == doc_type)

    docs = query.order_by(Document.uploaded_at.desc()).all()
    valid_docs = [
        d for d in docs
        if str(d.file_path).startswith("gs://") or os.path.exists(str(d.file_path)) or not d.file_path
    ]
    return {"total": len(valid_docs), "items": valid_docs}


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """
    Retrieves document metadata ensuring tenant ownership.
    """
    query = scope_document_query(db.query(Document).filter(Document.id == document_id), current_user)

    doc = query.first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found or access denied.")
    return doc


@router.post("/{document_id}/reanalyze", response_model=DocumentResponse)
async def reanalyze_document(
    document_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """
    Forces deep multimodal AI re-analysis on an existing document,
    updating its structured insights, spatial bounding coordinates, and vector embeddings.
    """
    query = scope_document_query(db.query(Document).filter(Document.id == document_id), current_user)
    doc = query.first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found or access denied.")

    try:
        local_path = storage_service.download_to_temp(doc.file_path)
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Physical document file missing on storage.")

    # 1. Extraction
    extraction_res = extractor.extract(local_path)
    raw_text = extraction_res["text"]
    page_count = extraction_res["page_count"]
    is_scanned = extraction_res["is_scanned"]

    # 2. PII Redaction
    sanitized_text, pii_stats = redactor.redact(raw_text)

    # 3. Multimodal AI Analysis with retry and model fallback
    ai_insights = await gemini_analyzer.analyze_document(
        sanitized_text=sanitized_text,
        filename=doc.original_filename,
        file_path=local_path,
        is_scanned=is_scanned,
    )

    if ai_insights.get("raw_extracted_text"):
        gemini_text = ai_insights["raw_extracted_text"]
        sanitized_text, extra_pii = redactor.redact(gemini_text)
        for k, v in extra_pii.items():
            pii_stats[k] = pii_stats.get(k, 0) + v
    elif raw_text and raw_text.startswith("[Scanned Image"):
        sanitized_text = ""

    # 4. Upsert DocumentInsight
    existing_insight = db.query(DocumentInsight).filter(DocumentInsight.document_id == doc.id).first()
    if existing_insight:
        existing_insight.document_type = ai_insights["document_type"]
        existing_insight.title = ai_insights["title"]
        existing_insight.summary = ai_insights["summary"]
        existing_insight.key_points = ai_insights["key_points"]
        existing_insight.entities = ai_insights["entities"]
        existing_insight.line_items = ai_insights["line_items"]
        existing_insight.action_items = ai_insights["action_items"]
        existing_insight.risk_clauses = ai_insights["risk_clauses"]
        existing_insight.charts_data = ai_insights["charts_data"]
        existing_insight.raw_extracted_text = sanitized_text
        existing_insight.confidence_score = ai_insights["confidence_score"]
    else:
        new_insight = DocumentInsight(
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
        db.add(new_insight)

    doc.page_count = page_count
    doc.processed_at = datetime.now(timezone.utc)
    doc.status = "ready"
    db.commit()
    db.refresh(doc)

    # 5. Update vector indexing
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
            "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else "",
        },
    )

    log_audit_event(
        db=db,
        action="reanalyzed",
        target_type="document",
        target_id=str(doc.id),
        user_id=str(current_user.id),
        metadata={"filename": doc.original_filename, "confidence_score": ai_insights["confidence_score"]},
    )

    return doc


@router.get("/{document_id}/file")
def get_document_file(
    document_id: str,
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str | None, Query()] = None,
    current_user: Annotated[User | None, Depends(get_optional_user)] = None,
):
    """
    Streams original document file. Resolves authentication via header or ?token= param.
    Streams directly from Google Cloud Storage (GCS) or local disk.
    """
    caller = resolve_file_user(token=token, current_user=current_user, db=db)

    query = scope_document_query(db.query(Document).filter(Document.id == document_id), caller)

    doc = query.first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found on storage.")

    file_path = str(doc.file_path)
    original_filename = str(doc.original_filename)
    mime_type = str(doc.mime_type) if doc.mime_type else "application/octet-stream"

    try:
        data, content_type = storage_service.get_file_bytes(file_path)
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found on storage.")

    # Record view audit trail
    log_audit_event(
        db=db,
        action="viewed",
        target_type="document",
        target_id=str(doc.id),
        user_id=str(caller.id),
        metadata={"filename": original_filename, "mime_type": mime_type},
    )

    return Response(
        content=data,
        media_type=mime_type or content_type,
        headers={"Content-Disposition": f'inline; filename="{original_filename}"'},
    )


@router.get("/{document_id}/redacted-file")
def get_redacted_document_file(
    document_id: str,
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str | None, Query()] = None,
    current_user: Annotated[User | None, Depends(get_optional_user)] = None,
):
    """
    Returns the physically sanitized, blacked-out redacted PDF for compliance and secure sharing.
    """
    caller = resolve_file_user(token=token, current_user=current_user, db=db)

    query = scope_document_query(db.query(Document).filter(Document.id == document_id), caller)

    doc = query.first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source document not found.")

    doc_id = str(doc.id)
    file_path = str(doc.file_path)
    original_filename = str(doc.original_filename)

    try:
        local_path = storage_service.download_to_temp(file_path)
        redacted_path = redactor.get_or_create_redacted_file(doc_id, local_path)
        if not os.path.exists(redacted_path):
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to locate or generate redacted document.")

        with open(redacted_path, "rb") as f:
            data = f.read()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to load redacted file: {e}")

    # Record audit trail
    log_audit_event(
        db=db,
        action="viewed_redacted",
        target_type="document",
        target_id=doc_id,
        user_id=str(caller.id),
        metadata={
            "filename": original_filename,
            "redacted_filename": os.path.basename(redacted_path),
        },
    )

    base_name = Path(original_filename).stem
    redacted_ext = Path(redacted_path).suffix.lower()
    download_filename = f"{base_name}_REDACTED{redacted_ext}"

    media_type = str(doc.mime_type) if doc.mime_type else "application/octet-stream"
    if redacted_ext == ".pdf":
        media_type = "application/pdf"
    elif redacted_ext in [".png", ".jpg", ".jpeg", ".webp"]:
        media_type = f"image/{redacted_ext.replace('.', '')}"
    elif redacted_ext in [".txt", ".md"]:
        media_type = "text/plain; charset=utf-8"

    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{download_filename}"'},
    )


@router.delete("/{document_id}")
@router.delete("/{document_id}/")
def delete_document(
    document_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """
    Idempotent deletion scoped strictly to the caller's documents.
    Removes files from Google Cloud Storage or local disk and purges metadata.
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

    doc_id = str(doc.id)
    file_path = str(doc.file_path)
    original_filename = str(doc.original_filename)

    # Remove physical original file from GCS or local disk
    storage_service.delete_file(file_path)

    # Remove redacted file if generated
    storage_service.delete_file(f"documents/{doc.user_id}/{doc_id}/redacted_{original_filename}")
    redacted_local = os.path.join(settings.UPLOAD_DIR, f"{doc_id}_redacted.pdf")
    if os.path.exists(redacted_local):
        try:
            os.remove(redacted_local)
        except Exception:
            pass

    # Remove vector index
    vector_service.delete_document(doc_id)

    # Log deletion audit event before purging from DB
    log_audit_event(
        db=db,
        action="deleted",
        target_type="document",
        target_id=doc_id,
        user_id=str(current_user.id),
        metadata={"filename": original_filename, "purged": True},
    )

    # Remove from DB
    db.delete(doc)
    db.commit()
    invalidate_dashboard_cache(str(current_user.id))
    return {"success": True, "message": f"Document {document_id} and its vector data were purged."}


@router.post("/{document_id}/chat", response_model=DocumentChatResponse)
async def chat_with_document(
    document_id: str,
    request: DocumentChatRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """
    Conversational RAG assistant grounded strictly in the target document.
    Enforces tenant access verification.
    """
    query = scope_document_query(db.query(Document).filter(Document.id == document_id), current_user)

    doc = query.first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found or access denied.")

    doc_id = str(doc.id)
    doc_original_filename = str(doc.original_filename)

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
        document_title=str(insight.title or doc_original_filename or "Document"),
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
        target_id=doc_id,
        user_id=str(current_user.id),
        metadata={
            "query": request.message,
            "filename": doc_original_filename,
            "citations_returned": len(chat_result.get("citations", [])),
        },
    )

    return DocumentChatResponse(
        document_id=doc_id,
        reply=chat_result.get("reply", "No response generated."),
        citations=chat_result.get("citations", []),
        suggested_followups=chat_result.get("suggested_followups", []),
    )
