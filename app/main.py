import os
import secrets
import traceback
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models.entities import ErrorLog
from app.routers import auth, compliance, dashboard, documents, search, telemetry


def generate_correlation_id() -> str:
    chars = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    p1 = "".join(secrets.choice(chars) for _ in range(4))
    p2 = "".join(secrets.choice(chars) for _ in range(4))
    return f"ERR-{p1}-{p2}"


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
api_v1.include_router(telemetry.router)


@api_v1.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    correlation_id = generate_correlation_id()
    errors = exc.errors()

    user_msg = "Please verify your input fields."
    if errors:
        first = errors[0]
        field = first.get("loc", ["field"])[-1]
        msg = first.get("msg", "Invalid value")
        if field == "email":
            user_msg = "Please enter a valid work email address (e.g. name@company.io)."
        elif field == "password":
            user_msg = "Password does not meet required criteria."
        else:
            user_msg = f"{str(field).capitalize()}: {msg}."

    # Centralized Supabase error logging
    subsystem = "auth" if "/auth" in str(request.url.path) else "ingestion"
    db = SessionLocal()
    try:
        log_entry = ErrorLog(
            id=str(uuid.uuid4()),
            correlation_id=correlation_id,
            user_type="guest",
            subsystem=subsystem,
            severity="ERROR",
            endpoint=str(request.url.path),
            http_method=request.method,
            status_code=422,
            error_code="REQUEST_VALIDATION_ERROR",
            user_friendly_message=user_msg[:500],
            technical_message=str(errors),
            client_context={"client_host": request.client.host if request.client else None},
        )
        db.add(log_entry)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    return JSONResponse(
        status_code=422,
        content={
            "detail": user_msg,
            "raw_errors": errors,
            "correlation_id": correlation_id,
            "error_code": "REQUEST_VALIDATION_ERROR",
        },
        headers={"X-Correlation-ID": correlation_id},
    )


@api_v1.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    correlation_id = generate_correlation_id()
    stack = traceback.format_exc()
    subsystem = "auth" if "/auth" in str(request.url.path) else "server"

    db = SessionLocal()
    try:
        log_entry = ErrorLog(
            id=str(uuid.uuid4()),
            correlation_id=correlation_id,
            user_type="guest",
            subsystem=subsystem,
            severity="CRITICAL",
            endpoint=str(request.url.path),
            http_method=request.method,
            status_code=500,
            error_code="INTERNAL_SERVER_ERROR",
            user_friendly_message="An unexpected server condition occurred while processing your request.",
            technical_message=str(exc),
            stack_trace=stack,
            client_context={"client_host": request.client.host if request.client else None},
        )
        db.add(log_entry)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    return JSONResponse(
        status_code=500,
        content={
            "detail": "An unexpected server condition occurred. Raw inputs have been preserved.",
            "correlation_id": correlation_id,
            "error_code": "INTERNAL_SERVER_ERROR",
        },
        headers={"X-Correlation-ID": correlation_id},
    )


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
            "bucket": (
                getattr(settings, "GCS_BUCKET_NAME", None)
                or getattr(settings, "GCS_BUCKET", None)
                or os.environ.get("GCS_BUCKET_NAME", None)
                or os.environ.get("GCS_BUCKET", None)
            ) if storage_service.is_gcs_active else None,
            "active": storage_service.is_gcs_active,
            "diagnostic": "Connected to Google Cloud Storage" if storage_service.is_gcs_active else (storage_service.init_error or "GCS inactive, using local fallback"),
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
