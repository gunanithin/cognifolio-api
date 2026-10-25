import datetime
from datetime import timedelta, timezone
from typing import Optional, Any, Dict
import bcrypt
from jose import jwt
from app.config import settings

JWT_ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES or 1440  # 24 hours default


def hash_password(password: str) -> str:
    """
    Enterprise-grade password hashing using bcrypt with 12 salt rounds.
    """
    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Constant-time password comparison to prevent timing attacks.
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except Exception:
        return False


def create_access_token(
    subject: str,
    role: str = "analyst",
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Generates a cryptographically signed JWT access token.
    """
    now = datetime.datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode: Dict[str, Any] = {
        "sub": str(subject),
        "role": str(role),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }

    if extra_claims:
        to_encode.update(extra_claims)

    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Dict[str, Any]:
    """
    Validates signature and claims of a JWT access token.
    Raises JWTError if expired, tampered with, or malformed.
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
