import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, BigInteger, Text, Float, JSON
from sqlalchemy.orm import relationship
from app.database import Base


def generate_uuid():
    return str(uuid.uuid4())


def utc_now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=True)
    avatar_url = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    documents = relationship("Document", back_populates="user", cascade="all, delete-orphan")


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class Document(Base):
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    org_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)
    file_size_bytes = Column(BigInteger, default=0)
    file_type = Column(String(50), nullable=False)  # pdf, png, jpg, etc.
    mime_type = Column(String(100), nullable=True)
    page_count = Column(Integer, default=1)
    status = Column(String(50), default="uploading", index=True)  # uploading, processing, ready, failed
    error_message = Column(Text, nullable=True)
    retention_days = Column(Integer, default=90)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    uploaded_at = Column(DateTime(timezone=True), default=utc_now)
    processed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="documents")
    insights = relationship("DocumentInsight", back_populates="document", uselist=False, cascade="all, delete-orphan")


class DocumentInsight(Base):
    __tablename__ = "document_insights"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    document_type = Column(String(100), nullable=True, index=True)  # Invoice, Contract, Report, Policy, etc.
    title = Column(String(255), nullable=True)
    summary = Column(Text, nullable=True)
    key_points = Column(JSON, default=list)  # list of strings
    entities = Column(JSON, default=dict)    # dict of key-value pairs (amounts, dates, vendor, parties, etc.)
    line_items = Column(JSON, default=list)  # list of dicts for tables/breakdowns
    action_items = Column(JSON, default=list) # deadlines, signatures needed, payments
    risk_clauses = Column(JSON, default=list) # auto-renewal, penalty, liability
    charts_data = Column(JSON, default=dict) # prepared chart data for frontend (categories, amounts, timeline)
    raw_extracted_text = Column(Text, nullable=True)
    confidence_score = Column(Float, default=1.0)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    document = relationship("Document", back_populates="insights")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    org_id = Column(String(36), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(100), nullable=False)  # 'uploaded', 'viewed', 'deleted', 'searched'
    target_type = Column(String(50), nullable=True)
    target_id = Column(String(36), nullable=True)
    meta = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now)
