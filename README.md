# Cognifolio API (`cognifolio-api`)

Enterprise Multimodal Document Intelligence Backend powered by **FastAPI**, **Google Gemini 3.5 Flash**, **Supabase PostgreSQL 17**, and **Qdrant Cloud Vector Search**.

---

## Architecture Overview

* **REST API Engine:** FastAPI with async routes, structured Pydantic schemas, and SQLAlchemy 2.0 ORM.
* **Multimodal Document Extraction:** Powered by Google Gemini 3.5 Flash (`gemini-3.5-flash`) with dynamic fallback heuristic parsing.
* **Neural Vector Embeddings:** Google Gemini Embedding (`gemini-embedding-001`, 768-D) with cosine similarity search in Qdrant Cloud.
* **Relational Storage & Audit:** Supabase PostgreSQL 17 storing documents, structured entities, line items, risk clauses, and immutable PII redaction audit logs.
* **Pre-AI Client-Side PII Redaction:** Regex-based sanitization masking Aadhaar, PAN, SSN, Credit Cards, Bank Accounts, Phone Numbers, and Emails before sending to external AI models.

---

## Getting Started

### 1. Prerequisites
* Python 3.10+
* Virtual environment (`venv`)

### 2. Setup Virtual Environment & Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment Variables
Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```

| Variable | Description |
| :--- | :--- |
| `GEMINI_API_KEY` | Google Gemini API key from Google AI Studio |
| `GEMINI_MODEL` | `gemini-3.5-flash` |
| `EMBEDDING_MODEL` | `gemini-embedding-001` (output dimensionality: 768) |
| `DATABASE_URL` | Supabase PostgreSQL URI (`postgresql+psycopg2://...`) |
| `QDRANT_LOCATION` | Qdrant Cloud Cluster URL or `:memory:` |
| `QDRANT_API_KEY` | Qdrant Cloud API Key |
| `QDRANT_COLLECTION` | Collection name (`cognifolio_embeddings`) |

### 4. Run Development Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive API documentation will be available at:
* Swagger UI: `http://localhost:8000/docs`
* ReDoc: `http://localhost:8000/redoc`

---

## Core API Endpoints

* `POST /api/v1/documents/upload` - Upload PDF/Image, redact PII, extract multimodal intel, generate vectors.
* `GET /api/v1/documents` - List all uploaded documents with metadata and status.
* `GET /api/v1/documents/{id}` - Get full document details, key takeaways, entities, line items, and risk clauses.
* `GET /api/v1/documents/{id}/file` - Stream visual PDF/image file for canvas rendering.
* `POST /api/v1/search` - Neural semantic search with Qdrant vector retrieval.
* `GET /api/v1/dashboard/stats` - Aggregated metrics, category distributions, and spend breakdowns.
* `GET /api/v1/health` - System health check for relational and vector databases.
