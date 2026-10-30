import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import String, DateTime, ForeignKey, Integer, BigInteger, Text, Float, JSON, Boolean
from sqlalchemy.orm import relationship, Mapped, mapped_column
from app.database import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(50), default="analyst", index=True)  # 'admin' | 'analyst' | 'guest'
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    documents = relationship("Document", back_populates="user", cascade="all, delete-orphan")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    org_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)  # pdf, png, jpg, etc.
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(50), default="uploading", index=True)  # uploading, processing, ready, failed
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retention_days: Mapped[int] = mapped_column(Integer, default=90)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="documents")
    insights: Mapped[Optional["DocumentInsight"]] = relationship(
        "DocumentInsight",
        back_populates="document",
        uselist=False,
        lazy="joined",
        cascade="all, delete-orphan",
    )


class DocumentInsight(Base):
    __tablename__ = "document_insights"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    document_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)  # Invoice, Contract, Report, Policy, etc.
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    key_points: Mapped[List[str]] = mapped_column(JSON, default=list)  # list of strings
    entities: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)    # dict of key-value pairs (amounts, dates, vendor, parties, etc.)
    line_items: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)  # list of dicts for tables/breakdowns
    action_items: Mapped[List[str]] = mapped_column(JSON, default=list) # deadlines, signatures needed, payments
    risk_clauses: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list) # auto-renewal, penalty, liability
    charts_data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict) # prepared chart data for frontend (categories, amounts, timeline)
    raw_extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    document = relationship("Document", back_populates="insights")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    org_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)  # 'uploaded', 'viewed', 'deleted', 'searched'
    target_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    meta: Mapped[Dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ErrorLog(Base):
    __tablename__ = "error_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    correlation_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    # Dimension 1: Actor Persona Segregation
    user_type: Mapped[str] = mapped_column(String(20), default="guest", index=True)  # 'guest' | 'authenticated' | 'system'
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    user_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Dimension 2: Subsystem & Functional Domain
    subsystem: Mapped[str] = mapped_column(String(50), default="general", index=True)

    # Dimension 3: Severity Level
    severity: Mapped[str] = mapped_column(String(20), default="ERROR", index=True)  # 'CRITICAL' | 'ERROR' | 'WARNING' | 'INFO'

    # HTTP & Network Context
    endpoint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    http_method: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    error_code: Mapped[str] = mapped_column(String(100), nullable=False)

    # Presentation Segregation
    user_friendly_message: Mapped[str] = mapped_column(String(500), nullable=False)
    technical_message: Mapped[str] = mapped_column(Text, nullable=False)
    stack_trace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Environment & Audit Payloads
    client_context: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    request_payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

