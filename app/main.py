from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config import settings
from app.database import engine, Base
from app.routers import documents, search, dashboard, compliance, auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure tables are created
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Document Intelligence Studio API",
    version="1.0.0",
    description="Enterprise-grade multimodal document intelligence with semantic vector search and PII redaction.",
    lifespan=lifespan,
)

# CORS setup for web frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register versioned routers
api_v1 = FastAPI()
api_v1.include_router(auth.router)
api_v1.include_router(documents.router)
api_v1.include_router(search.router)
api_v1.include_router(dashboard.router)
api_v1.include_router(compliance.router)


@api_v1.get("/health")
def api_v1_health():
    from app.services.storage import storage_service
    return {
        "status": "online",
        "env": settings.APP_ENV,
        "gemini_model": settings.GEMINI_MODEL,
        "qdrant_location": settings.QDRANT_LOCATION,
        "storage": {
            "provider": "gcs" if storage_service.is_gcs_active else "local",
            "bucket": settings.GCS_BUCKET_NAME if storage_service.is_gcs_active else None,
            "active": storage_service.is_gcs_active,
        },
    }


app.mount(settings.API_V1_PREFIX, api_v1)


@app.get("/")
def root():
    return {
        "service": "Document Intelligence Studio API",
        "docs_url": "/docs",
        "version": "1.0.0",
        "api_v1": settings.API_V1_PREFIX,
    }
