import uuid
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from app.models.entities import AuditLog, User


def get_default_analyst(db: Session) -> User:
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


def log_audit_event(
    db: Session,
    action: str,
    target_type: str = "document",
    target_id: Optional[str] = None,
    user_id: Optional[str] = None,
    org_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> AuditLog:
    """
    Records an immutable audit event in the database for SOC2 / GDPR compliance.
    """
    if not user_id:
        user = get_default_analyst(db)
        user_id = str(user.id)

    meta = metadata or {}
    meta.setdefault("actor_name", "Demo Analyst")
    meta.setdefault("actor_email", "demo@cognifolio.ai")
    meta.setdefault("ip_address", "127.0.0.1 (Studio Local)")

    audit_entry = AuditLog(
        id=str(uuid.uuid4()),
        user_id=user_id,
        org_id=org_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        meta=meta,
    )
    db.add(audit_entry)
    db.commit()
    db.refresh(audit_entry)
    return audit_entry
