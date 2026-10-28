# Cognifolio API (`cognifolio-api`)

> **Enterprise Multimodal Document Intelligence, Zero-Trust Compliance & Financial Audit Platform**  
> Powered by **FastAPI**, **Google Gemini 3.5 / 3.8 Flash**, **Google Cloud Storage (GCS)**, **Supabase PostgreSQL 17**, and **Qdrant Cloud Vector Search**.

---

## 1. Executive Summary & Vision

**Cognifolio** is an enterprise-grade document intelligence and legal/financial audit platform designed for banking, insurance, legal counsel, and corporate risk management teams. 

Modern enterprises handle thousands of complex documents daily—multi-page PDF contracts, scanned receipts, high-resolution architecture diagrams, financial spreadsheets (`.xlsx`/`.csv`), and executive Word memos (`.docx`). Traditional OCR solutions produce brittle, unstructured text dumps that strip away layout semantics, while naive generative AI integrations expose sensitive customer personally identifiable information (PII) to third-party model providers.

Cognifolio solves these challenges through a **Zero-Trust, Multi-Format, Spatial-First Architecture**:
1. **Client-Side Pre-AI Sanitization**: Redacts banking, tax, and personal identifiers *before* any text or visual token reaches external AI models.
2. **Spatial Leaf-Level Precision**: Pinpoints exact, normalized 2D coordinates (`box_2d`) for fine-grained components, line items, and risk clauses—eliminating macro-box grouping.
3. **Cloud-Native Storage Tier**: Archives originals and physically redacted copies in **Google Cloud Storage (GCS)**, enabling direct streaming to desktop, tablet, and mobile clients with zero device storage footprint.
4. **Sub-Second Semantic Retrieval**: Combines 768-D dense vector embeddings (`gemini-embedding-001`) with HNSW indexing in Qdrant Cloud for natural-language search and grounded conversational Q&A.

---

## 2. End-to-End User Experience & Journey (End-User POV)

Cognifolio is engineered around real-world enterprise user workflows. Below is how different roles experience the platform from ingestion to final audit:

```
 ┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                       END-USER JOURNEY OVERVIEW                                        │
 └────────────────────────────────────────────────────────────────────────────────────────────────────────┘
    1. INGESTION             2. ZERO-TRUST PURITY        3. MULTIMODAL EXTRACTION    4. CLOUD PERSISTENCE
   ┌───────────────┐        ┌───────────────────┐       ┌───────────────────────┐   ┌────────────────────┐
   │ Drag & Drop   │  ───►  │ Instant PII Mask  │ ───►  │ Gemini 3.5/3.8 Flash  │ ──►│ Google Cloud       │
   │ Any Document  │        │ (Aadhaar, PAN,    │       │ (Spatial coordinates, │   │ Storage (GCS)      │
   │ (12+ Formats) │        │  Cards, Accounts) │       │  financial breakdown) │   │ Durable Vault      │
   └───────────────┘        └───────────────────┘       └───────────────────────┘   └────────────────────┘
           │                                                                                   │
           ▼                                                                                   ▼
    8. IMMUTABLE AUDIT       7. REDLINE AUDITING         6. CONVERSATIONAL RAG       5. INTERACTIVE CANVAS
   ┌───────────────┐        ┌───────────────────┐       ┌───────────────────────┐   ┌────────────────────┐
   │ Permanent Log │  ◄───  │ Side-by-Side Diff │ ◄───  │ Ask natural questions │ ◄──│ Golden highlight   │
   │ Cryptographic │        │ (Base vs Target   │       │ with verbatim text    │   │ pulses on exact    │
   │ Verification  │        │  Contract Risk)   │       │ citations & box jumps │   │ leaf components    │
   └───────────────┘        └───────────────────┘       └───────────────────────┘   └────────────────────┘
```

### User Personas & Real-World Workflows

#### A. The Financial Auditor / Risk Analyst
- **Goal**: Ingest hundreds of vendor invoices, quarterly spreadsheets, and balance sheets to audit discrepancies, tax compliance, and overall expenditure.
- **User Experience**:
  1. The auditor drops a `.xlsx` financial model or high-res scanned invoice into Cognifolio.
  2. The system instantly parses every sheet into structured tables, redacting any sensitive employee personal banking or card numbers.
  3. The auditor views the extracted line items, tax breakdowns, and vendor metadata on an interactive data grid.
  4. Clicking any financial discrepancy instantly highlights the corresponding row or physical receipt line item with golden bounding boxes.

#### B. The Legal Counsel / Compliance Officer
- **Goal**: Review vendor master service agreements (MSAs) and non-disclosure agreements (NDAs) for indemnification caps, jurisdiction clauses, and non-compete liabilities.
- **User Experience**:
  1. The attorney uploads a 45-page `.pdf` or `.docx` agreement.
  2. Cognifolio identifies high-risk clauses (unlimited liability, non-standard governing law) and grades compliance risks.
  3. The attorney runs **Redline Comparison** (`POST /documents/compare`) against a standard template to instantly visualize added, modified, or omitted clauses.
  4. For external distribution or discovery, the attorney exports a **Burned-in Redacted Copy** with permanent black overlays over private corporate secrets.

#### C. The Enterprise Executive / Operator
- **Goal**: Query organizational knowledge across thousands of documents without reading hundreds of pages.
- **User Experience**:
  1. The executive opens the search bar and types: *"What are our quarterly commitments for cloud infrastructure?"*
  2. Qdrant Cloud performs a dense semantic vector search across 768-D embeddings, returning ranked passages in sub-150ms.
  3. The executive opens the AI Conversational Assistant (`POST /documents/{id}/chat`) and asks clarifying questions. The response provides verbatim quotations and one-click coordinate jumps directly to the primary source document.

---

## 3. End-to-End System Architecture

Cognifolio API follows a clean, decoupled microservice architecture structured for high throughput, sub-second latency, and fault isolation:

```mermaid
flowchart TD
    subgraph ClientLayer["1. Client & Presentation Layer"]
        User["Desktop / Tablet / Mobile Browser"]
    end

    subgraph GatewayLayer["2. Gateway & Ingestion Layer"]
        FastAPI["FastAPI Async Gateway (/api/v1)"]
        AuthDep["JWT Multi-Tenant Dependency Injection"]
        Extractor["Multi-Format Parser<br/>(pypdf, pymupdf, docx, openpyxl, Pillow)"]
    end

    subgraph SecurityLayer["3. Zero-Trust Security & PII Redaction"]
        Redactor["Pre-AI Sanitizer<br/>(Aadhaar, PAN, SSN, Luhn Cards, Bank Accs)"]
        Burner["Physical Masking Engine<br/>(Blackout Overlays & Privacy Banners)"]
        Audit["Immutable Audit Logger<br/>(User, IP, Action, Timestamp)"]
    end

    subgraph AILayer["4. Multimodal AI Intelligence Tier"]
        GeminiFlash["Google Gemini 3.5 / 3.8 Flash<br/>(Vision Transcription + Structured JSON)"]
        SpatialEngine["Spatial Leaf-Level Precision Engine<br/>(Compound Unpacking + Primacy Scoring)"]
        Embedder["Gemini Dense Embeddings<br/>(gemini-embedding-001: 768-D)"]
    end

    subgraph StorageLayer["5. Enterprise Storage & Data Tier"]
        GCS[("Google Cloud Storage (GCS)<br/>gs://cognifolio-prod-storage/")]
        Postgres[("Supabase PostgreSQL 17<br/>Entities, Insights, Audit Logs, JSONB")]
        Qdrant[("Qdrant Cloud Vector Database<br/>768-D HNSW Cosine Index")]
    end

    User -->|"1. Secure Upload (Bearer JWT)"| FastAPI
    FastAPI --> AuthDep
    AuthDep --> Extractor
    Extractor -->|"2. Raw Text & Layout"| Redactor
    Redactor -->|"3. Pre-Sanitized Text & Pixels"| GeminiFlash
    Redactor -->|"4. Burn Redacted Copy"| Burner
    FastAPI -->|"5. Archive Raw & Redacted Files"| GCS
    GeminiFlash -->|"6. Extract Entities & Bounding Boxes"| SpatialEngine
    SpatialEngine -->|"7. Normalized Leaves & Insights"| FastAPI
    FastAPI -->|"8. Persist Records & Audit Events"| Postgres
    FastAPI -->|"9. Upsert Document Embeddings"| Embedder
    Embedder -->|"10. Index Dense Vectors"| Qdrant
    FastAPI -->|"11. Record Security Audit Log"| Audit
    User <-.-|"12. Stream Direct from GCS / Chat / Search"| FastAPI
```

---

## 4. Zero-Trust Security & PII Redaction

Cognifolio operates under a strict **Zero-Trust Client-Side Redaction Mandate**. Enterprise data is sanitized before it ever touches external AI infrastructure:

```
[Raw Document Upload]
         │
         ▼
 ┌──────────────────────────────────────────────────────────┐
 │           PIIRedactor (app/services/redactor.py)         │
 │  - Aadhaar:  \b[2-9][0-9]{3}\s?[0-9]{4}\s?[0-9]{4}\b      │
 │  - PAN Card: \b[A-Z]{5}[0-9]{4}[A-Z]\b                  │
 │  - US SSN:   \b(?!000)(?!666)[0-8][0-9]{2}-...          │
 │  - Luhn:     16-digit credit cards with check-digit test │
 │  - Banking:  IFSC + Account numbers, IBANs               │
 │  - Contact:  RFC-compliant email & phone regexes         │
 └──────────────────────────────────────────────────────────┘
         │
         ├───► [Sanitized Text] ──► [Google Gemini AI (External API)]
         │
         └───► [Physical Blackout Overlays] ──► [GCS gs://cognifolio-prod-storage/]
```

### Core Security Guarantees

| Security Measure | Implementation Mechanism | Enterprise Benefit |
| :--- | :--- | :--- |
| **Pre-AI Masking** | `PIIRedactor.redact()` executed on all raw extracted text before prompt interpolation. | Guarantees compliance with GDPR, HIPAA, and DPDP by preventing external model ingestion of sensitive credentials. |
| **Physical Burn-In** | Overlay engine draws opaque blackout polygons on PDF canvases and image headers. | Redacted files can be safely shared with third parties without risk of underlying text scraping. |
| **Multi-Tenant Isolation** | Every SQL query, vector upsert, and GCS object path is scoped strictly to `current_user.id`. | Prevents data cross-contamination between enterprise tenants or user accounts. |
| **Cryptographic Audit** | `log_audit_event()` persists every file view, redaction generation, vector query, and purge. | Provides a tamper-evident audit log for SOC 2, ISO 27001, and regulatory compliance reviews. |
| **Secret Zero-Leakage** | All credentials (`gcp-credentials.json`, `.env`, API keys) are strictly `.gitignore`d. | Eliminates exposure of infrastructure credentials in version control. |

---

## 5. Enterprise Storage Tier: Google Cloud Storage (GCS)

Cognifolio uses **Google Cloud Storage (GCS)** as its primary, durable object vault, replacing ephemeral local server disks:

```
gs://cognifolio-prod-storage/
└── documents/
    └── <user_id>/
        └── <document_id>/
            ├── agreement_scan.png              # Raw original upload
            └── redacted_agreement_scan.png     # Physically redacted copy
```

### Storage Engineering Highlights

1. **Direct Byte Streaming (`GET /documents/{id}/file`)**:
   Files are read from GCS into an async streaming response with dynamic MIME typing and `Content-Disposition: inline`. Web and mobile clients render high-resolution documents instantly without saving multi-megabyte files locally.
2. **Time-Limited Signed URLs**:
   The storage engine can generate secure, 15-minute signed URLs for decentralized distribution and caching across enterprise content delivery networks (CDNs).
3. **Resilient Local Fallback**:
   If GCS credentials or bucket settings are omitted during local development, the platform automatically routes storage operations to `./uploads/` on disk without throwing unhandled exceptions.
4. **Idempotent Deletion**:
   Deleting a document executes an atomic clean-up across GCS (original + redacted), Supabase PostgreSQL (metadata + audit), and Qdrant Cloud (vector points).

---

## 6. Optimization & Performance Engineering

Cognifolio is engineered to handle complex enterprise documents with sub-second response times:

### 1. Spatial Leaf-Level Precision Engine (`box_2d`)
Traditional OCR engines group large diagram sections or multi-item columns into one giant, ambiguous bounding box. Cognifolio solves this with:
- **Normalized 2D Coordinates**: Vision models return coordinates on a standardized $0\text{–}1000$ scale:
  $$\text{box\_2d} = [y_{\min}, x_{\min}, y_{\max}, x_{\max}]$$
- **Compound Container Unpacking (`_unpack_compound_line_items`)**:
  Detects compound entities (e.g., `Backend & Processing (FastAPI, PII Redaction, Temporal, Gemini)`) and programmatically decomposes them into distinct leaf components with proportional sub-coordinates.
- **Subject-Primacy Scoring Algorithm**:
  When a user searches for or selects a finding, candidates are ranked using weighted heuristic scoring:
  - **Primary Subject Boost (+420 pts)**: Awarded when the box title directly begins with the focal term.
  - **Word Density & Purity (+140 pts $\times$ density)**: Favors focused, concise leaf nodes over large multi-line containers.
  - **Compactness Bonus (+130 pts)** & **Anti-Macro Penalty (-220 pts)**: Penalizes containers covering $>20\%$ of the canvas area, ensuring that clicking "Temporal" highlights *only* the Temporal engine box.

### 2. Sub-Second Semantic Search (Qdrant Cloud + 768-D Embeddings)
- Every document is chunked with semantic sliding windows, embedded via `gemini-embedding-001`, and indexed in Qdrant using Hierarchical Navigable Small World (HNSW) graphs.
- Vector search returns ranked semantic matches in under 150ms with cosine similarity confidence scores.

### 3. Asynchronous Non-Blocking I/O
- FastAPI async handlers run on an event-driven `uvloop`, allowing concurrent document uploads, vector searches, and LLM streaming without blocking the server thread pool.
- Database connections use SQLAlchemy 2.0 with connection pooling and defensive JSON parsing for maximum resilience.

### 4. Dynamic Model Failover
- Ingestion defaults to Google's primary reasoning model (`gemini-3.5-flash` or `gemini-3.8-flash`).
- Built-in retry and fallback logic automatically handles transient rate limits or payload boundaries without failing user uploads.

---

## 7. Supported Multi-Format Ingestion

Cognifolio provides dedicated parsing pipelines across 12+ enterprise formats:

| Category | File Extensions | Parsing Strategy |
| :--- | :--- | :--- |
| **PDF Documents** | `.pdf` | Structured text extraction via `pypdf`/`pymupdf` with fallback heuristics and bounding coordinate extraction. |
| **Images & Scans** | `.png`, `.jpg`, `.jpeg`, `.webp` | High-res multimodal vision ingestion via Gemini Flash with spatial bounding boxes and Pillow resolution analysis. |
| **Spreadsheets** | `.xlsx`, `.xls` | Multi-sheet extraction via `openpyxl` with sheet separators (`--- [Sheet: Name] ---`) and structured markdown tables. |
| **Tabular Data** | `.csv` | Streaming CSV parser converting rows into markdown tables for accurate tabular RAG. |
| **Word Documents** | `.docx`, `.doc` | Heading hierarchy extraction (`#`, `##`, `###`), styled paragraphs, and embedded table reconstruction via `python-docx`. |
| **Plain Text / Code** | `.txt`, `.md` | UTF-8 reader with fallback encoding handlers and focal line markers. |

---

## 8. Configuration & Environment Variables

Copy `.env.example` to `.env` in the `cognifolio-api` root:

```bash
cp .env.example .env
```

| Variable | Description | Example / Default |
| :--- | :--- | :--- |
| `APP_ENV` | Application environment (`development` / `production`) | `production` |
| `API_V1_PREFIX` | Version 1 API route prefix | `/api/v1` |
| `SECRET_KEY` | Secret key for signing multi-tenant JWT access tokens | `your-secure-32-char-secret-key` |
| `DATABASE_URL` | Supabase PostgreSQL Connection URI | `postgresql+psycopg2://postgres:...@...pooler.supabase.com:5432/postgres?sslmode=require` |
| `GEMINI_API_KEY` | Google Gemini API Key from Google AI Studio | `AIzaSy...` |
| `GEMINI_MODEL` | Primary vision and analytical reasoning model | `gemini-3.5-flash` (or `gemini-3.8-flash`) |
| `EMBEDDING_MODEL` | Dense vector embedding model | `gemini-embedding-001` (768 dimensions) |
| `QDRANT_LOCATION` | Qdrant Cloud Cluster URL | `https://xxxxxx.eu-central-1-0.aws.cloud.qdrant.io` |
| `QDRANT_API_KEY` | Qdrant Cloud API Key | `eyJhbGci...` |
| `QDRANT_COLLECTION` | Target collection name in Qdrant | `cognifolio_embeddings` |
| `STORAGE_PROVIDER` | Object storage provider (`gcs` or `local`) | `gcs` |
| `GCS_BUCKET_NAME` | Google Cloud Storage Bucket Name | `cognifolio-prod-storage` |
| `GCS_PROJECT_ID` | Google Cloud Platform Project ID | `cognifolio` |
| `GCS_CREDENTIALS_FILE`| Path to Google Cloud Service Account JSON key | `./gcp-credentials.json` |
| `UPLOAD_DIR` | Local disk directory (used when provider is `local`) | `./uploads` |
| `MAX_FILE_SIZE_MB` | Maximum allowed file upload size in megabytes | `25` |
| `ALLOWED_EXTENSIONS`| Single source of truth for allowed file extensions | `pdf,png,jpg,jpeg,webp,docx,doc,xlsx,xls,csv,txt,md` |

---

## 9. Installation, Setup & Verification

### 1. Prerequisites
- **Python**: 3.10+ (Python 3.11 or 3.12 recommended)
- **Virtual Environment**: `.venv`
- **GCP Credentials**: Service Account JSON key with `roles/storage.objectAdmin` permissions.

### 2. Setup Instructions

```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install pinned dependencies
pip install -r requirements.txt

# 3. Configure environment variables
cp .env.example .env
# Open .env and add your Supabase, Qdrant, Gemini, and GCS credentials

# 4. Place your Google Cloud Service Account key in the backend root
cp /path/to/downloaded-service-account-key.json ./gcp-credentials.json
```

### 3. Launching the Backend Server

```bash
# Run FastAPI with live hot-reloading on port 8000
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive documentation is automatically generated and accessible at:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

### 4. Verification Suite

Run these commands to verify that all systems are functioning properly:

```bash
# 1. Verify module imports and routing integrity
.venv/bin/python -c "from app.routers import documents, dashboard, search, compliance, auth; print('✓ All routers imported cleanly!')"

# 2. Verify Google Cloud Storage connection
.venv/bin/python -c "from app.services.storage import storage_service; print('✓ GCS Active:', storage_service.is_gcs_active, '| Bucket:', storage_service.bucket_name)"

# 3. Check live health endpoint
curl -s http://localhost:8000/api/v1/health | python3 -m json.tool
```

Expected health response:
```json
{
  "status": "online",
  "version": "1.0.0",
  "database": "connected",
  "storage": {
    "provider": "gcs",
    "bucket": "cognifolio-prod-storage",
    "active": true
  },
  "gemini_model": "gemini-3.5-flash",
  "vector_db": "qdrant_cloud"
}
```

---

## 10. API Reference Overview

All API endpoints are mounted under `/api/v1` and require `Authorization: Bearer <token>` (or `?token=<jwt>` for document streaming in browser viewports):

### Health & Diagnostics
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Real-time health diagnostic verifying database, Gemini model, Qdrant, and GCS connectivity. |

### Document Ingestion & Management
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/documents/upload` | Ingests multi-format document, strips PII, archives to GCS, and extracts structured insights. |
| `GET` | `/api/v1/documents` | Lists all documents belonging to the authenticated tenant. |
| `GET` | `/api/v1/documents/{id}` | Retrieves detailed document metadata, entities, line items, and risk clauses. |
| `GET` | `/api/v1/documents/{id}/file` | Streams original document bytes directly from GCS with dynamic MIME typing. |
| `GET` | `/api/v1/documents/{id}/redacted-file`| Streams the compliance-redacted copy with burned-in blackout overlays. |
| `POST` | `/api/v1/documents/{id}/reanalyze` | Re-triggers deep multimodal extraction with alternative model parameters. |
| `DELETE`| `/api/v1/documents/{id}` | Idempotently purges document from GCS, database records, and vector indices. |

### Intelligence, Search & Audit
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/documents/{id}/chat` | Grounded conversational assistant with verbatim citations and coordinate jumps. |
| `POST` | `/api/v1/documents/compare` | Automated Redline Diff auditor comparing Base vs Target contract revisions. |
| `POST` | `/api/v1/search` | 768-D dense semantic vector search with cosine confidence rankings. |
| `GET` | `/api/v1/dashboard/stats` | Aggregated executive metrics: spend breakdown, document distribution, compliance scores. |
| `GET` | `/api/v1/compliance/audit-logs` | Immutable audit log trail showing all user actions with cryptographic timestamps. |

---

## 11. Security, Regulatory & Deployment Guidelines

1. **Secret & Key Isolation**: Never commit `gcp-credentials.json`, `*.pem`, or `.env` files to git. Use environment variables or cloud secret managers in staging and production.
2. **Mandatory Pre-AI Redaction**: Always route new document types or raw text inputs through `PIIRedactor` prior to invoking external AI services.
3. **Multi-Tenant Scoping**: Ensure every new router endpoint enforces `current_user.id` tenancy checks to prevent cross-account data leakage.
4. **Git Approval Mandate**: In accordance with project policy, automated scripts or AI assistants must never execute `git commit` or `git push` without explicit developer review and authorization.
