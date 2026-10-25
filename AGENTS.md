# Cognifolio API Backend Repository Rules & Engineering Guidelines

This document specifies the architectural conventions, security mandates, dependency practices, and coding rules for the **Cognifolio API** backend (`cognifolio-api`). All AI coding agents and engineers working in this repository must strictly adhere to these guidelines.

---

## 1. Architecture & Tech Stack

- **Framework**: [FastAPI](https://fastapi.tiangolo.com/) with async endpoint handlers, Pydantic v2 schemas, and dependency injection (`Depends`).
- **ORM & Database**: [SQLAlchemy 2.0](https://www.sqlalchemy.org/) with typed `Mapped` annotations.
  - Local / Testing: SQLite (`docstudio.db`).
  - Production: Supabase PostgreSQL 17 via `DATABASE_URL`.
- **Multimodal AI Extraction**: [Google Gemini 2.5 Flash / 3.5 Flash](https://ai.google.dev/) via the modern `google.genai` SDK.
- **Vector Database**: [Qdrant Cloud](https://qdrant.tech/) with 768-D dense embeddings (`gemini-embedding-001`).
- **Document Extractors**:
  - PDF: `pypdf` with fallback heuristic parser.
  - Microsoft Word: `python-docx`.
  - Microsoft Excel & CSV: `openpyxl` & standard `csv`.
  - Images: `Pillow` (PIL) for dimensions, format analysis, and visual redaction.

---

## 2. Python Environment & Dependency Management

1. **Virtual Environment Isolation**:
   - Always run commands using the local virtual environment:
     ```bash
     # Execution
     .venv/bin/python ...
     # Package management
     .venv/bin/pip install ...
     # Uvicorn server
     .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
     ```
   - **NEVER** run global `pip install` without the active `.venv`.
2. **Requirements Synchronization**:
   - Whenever a new third-party Python library is introduced, immediately record the pinned version in [`requirements.txt`](file:///Users/gunanithin/.gemini/antigravity-ide/scratch/cognifolio-api/requirements.txt).

---

## 3. Mandatory Security & PII Redaction Rules

Cognifolio operates in banking, legal, and healthcare contexts. Client-side PII sanitization is **mandatory before any data touches external AI services**:

1. **Pre-AI Sanitization**:
   - Every raw text extraction **must** pass through `PIIRedactor.redact(raw_text)` in [`app/services/redactor.py`](file:///Users/gunanithin/.gemini/antigravity-ide/scratch/cognifolio-api/app/services/redactor.py) before being included in prompts sent to Google Gemini.
   - Masked identifiers: Aadhaar, PAN Card, US SSN, Credit/Debit Cards, Bank Account Numbers, Phone Numbers, and Email Addresses.
2. **Physical Document Redaction**:
   - When users view or export redacted documents, `redactor.get_or_create_redacted_file(doc_id, file_path)` generates physical masked versions:
     - PDFs: Redacted using black rectangle overlays on matching bounding coordinates.
     - Images: Rendered with privacy header banners via Pillow.
     - Plain Text & Code: Written with `[REDACTED_...]` placeholder tokens.
3. **Immutable Audit Logging**:
   - Every file upload, PII sanitization event, deletion, and search must invoke `log_audit_event(db=db, action=..., ...)` in [`app/services/audit.py`](file:///Users/gunanithin/.gemini/antigravity-ide/scratch/cognifolio-api/app/services/audit.py).

---

## 4. Universal Multi-Format Document Processing

Cognifolio processes diverse business assets across invoices, contracts, financial models, and scans:

### Allowed File Extensions
[`app/config.py`](file:///Users/gunanithin/.gemini/antigravity-ide/scratch/cognifolio-api/app/config.py) specifies the single source of truth for allowed extensions:
```python
ALLOWED_EXTENSIONS: str = "pdf,png,jpg,jpeg,webp,docx,doc,xlsx,xls,csv,txt,md"
```
Never re-introduce hardcoded `ext != "pdf"` restrictions in upload routes.

### Dedicated Extraction Handlers ([`app/services/pdf_extractor.py`](file:///Users/gunanithin/.gemini/antigravity-ide/scratch/cognifolio-api/app/services/pdf_extractor.py))
- **`_extract_docx`**: Converts Word headings (`#`, `##`, `###`), styled paragraphs, bullet lists, and embedded tables into markdown tables.
- **`_extract_spreadsheet`**: Extracts all sheets with explicit markers `--- [Sheet: SheetName] ---` and structured markdown tables. Sets `page_count` equal to the sheet count.
- **`_extract_csv`**: Extracts CSV data into markdown tables.
- **`_extract_image`**: Reads image resolution and format metadata for multimodal transcription.
- **`_extract_text`**: Safe UTF-8 reader with fallback encoding handling.

### Multimodal Vision Extraction ([`app/services/gemini.py`](file:///Users/gunanithin/.gemini/antigravity-ide/scratch/cognifolio-api/app/services/gemini.py))
- Images (`png`, `jpg`, `jpeg`, `webp`) are sent directly as image bytes to Gemini using `types.Part.from_bytes(data=img_bytes, mime_type=mime_type)`.
- Extracted transcription is populated in `raw_extracted_text` for search indexing and highlighting.

---

## 5. Defensive Programming & Data Resilience

1. **Null-Safe Timestamp Formatting**:
   - Database timestamp fields (e.g., `d.uploaded_at`, `d.processed_at`, `d.expires_at`) can be `None`.
   - Never call `.strftime()` directly on database fields without a guard:
     ```python
     # Correct:
     uploaded_str = d.uploaded_at.strftime("%Y-%m-%d %H:%M") if d.uploaded_at else ""
     ```
2. **Defensive JSON Column Handling**:
   - JSON columns (such as `entities`, `line_items`, `charts_data`) in SQLite may occasionally be stored as serialized JSON strings or Python dicts.
   - Always parse defensively:
     ```python
     entities = ins.entities
     if isinstance(entities, str):
         try:
             entities = json.loads(entities)
         except Exception:
             entities = {}
     ```
3. **Structured API Error Responses**:
   - Endpoints must raise `HTTPException` with clear, informative `detail` strings:
     ```python
     raise HTTPException(
         status_code=status.HTTP_400_BAD_REQUEST,
         detail=f"Unsupported format '.{ext}'. Supported: {settings.ALLOWED_EXTENSIONS}"
     )
     ```

---

## 6. Authentication & Endpoint Architecture

1. **Route Prefix**: All version 1 routes are mounted under `/api/v1` in [`app/main.py`](file:///Users/gunanithin/.gemini/antigravity-ide/scratch/cognifolio-api/app/main.py).
2. **User Scoping**:
   - Every document, insight, chat query, and dashboard metric is strictly scoped to `current_user.id` unless the user role is `admin`.
3. **Guest Session Access**:
   - `POST /api/v1/auth/guest` issues a transient guest JWT allowing immediate exploration of the platform without initial friction.
4. **File Streaming Endpoints**:
   - Use `resolve_file_user` in [`app/routers/documents.py`](file:///Users/gunanithin/.gemini/antigravity-ide/scratch/cognifolio-api/app/routers/documents.py) to resolve authentication via either `Authorization: Bearer <token>` header or `?token=<jwt>` query parameter (essential for browser iframe / object plugins).
   - Set the appropriate `media_type` header dynamically based on the file extension.

---

## 7. Development & Verification Workflow

```bash
# 1. Start development server on port 8000
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 2. Verify all core modules import cleanly
.venv/bin/python -c "from app.routers import documents, dashboard, search, compliance, auth; print('All routers OK!')"

# 3. Check health endpoint
curl -s http://localhost:8000/api/v1/health

# 4. View interactive Swagger API documentation
open http://localhost:8000/docs
```

---

## 8. Git Workflow & Mandatory Approval Rule

> [!CAUTION]
> ### Mandatory Developer Approval for Git Commits & Pushes
> **NEVER run `git commit`, `git push`, or alter remote git history without explicit developer approval and consent.**
> - AI agents and automated scripts must always propose changes, present diffs to the developer, and wait for explicit confirmation before executing `git commit` or `git push`.
> - Always summarize modified files and request developer authorization prior to committing or pushing code.

- **Branch**: Feature branches or `main`.
- **Commit Messages**: Follow Conventional Commits format:
  - `feat: ...` for new features or capabilities
  - `fix: ...` for bug fixes and defensive hardening
  - `test: ...` for test suite additions
  - `refactor: ...` for architecture improvements
