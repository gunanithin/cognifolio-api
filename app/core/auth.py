import time
from typing import Optional, List, Dict, Tuple
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.entities import User
from app.core.security import decode_access_token

# Enterprise bearer token scheme
security = HTTPBearer(auto_error=False)

# In-memory user cache to eliminate redundant remote DB roundtrips on concurrent requests
_USER_CACHE: Dict[str, Tuple[float, User]] = {}
_USER_CACHE_TTL = 60.0


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """
    Strict authentication dependency.
    Validates JWT token, verifies signature and expiration, and retrieves active user.
    Uses 60s in-memory session cache for sub-millisecond repeated request handling.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided. Please log in or continue as Guest.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        payload = decode_access_token(token)
        user_id: Optional[str] = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token claims: subject identifier missing.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired authentication token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check fast in-memory user cache
    now = time.time()
    if user_id in _USER_CACHE:
        cached_time, cached_user = _USER_CACHE[user_id]
        if now - cached_time < _USER_CACHE_TTL:
            try:
                return db.merge(cached_user, load=False)
            except Exception:
                pass

    # Resolve user in database
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        # Check if this was a valid guest token whose record needs to be initialized
        role = payload.get("role", "guest")
        if role == "guest" or user_id.startswith("guest_"):
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
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account associated with this token was not found.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    if user:
        _USER_CACHE[user_id] = (now, user)

    return user


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    Permissive dependency for public endpoints.
    Returns authenticated User if valid token is provided, or None if unauthenticated.
    """
    if not credentials or not credentials.credentials:
        return None
    try:
        return get_current_user(credentials=credentials, db=db)
    except HTTPException:
        return None


def require_role(allowed_roles: List[str]):
    """
    Role-Based Access Control (RBAC) dependency factory.
    Example usage: Depends(require_role(["admin", "analyst"]))
    """
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Requires one of the following roles: {', '.join(allowed_roles)}",
            )
        return current_user

    return role_checker
