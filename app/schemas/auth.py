from datetime import datetime
from typing import Optional, Union
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


class UserRegisterRequest(BaseModel):
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+$", description="Valid email address")
    password: str = Field(..., min_length=6, description="Password must be at least 6 characters")
    name: str = Field(..., min_length=2, max_length=100, description="Full Name")


class UserLoginRequest(BaseModel):
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+$", description="Valid email address")
    password: str


class GoogleAuthRequest(BaseModel):
    id_token: str = Field(..., description="Google OAuth 2.0 credential/ID token")
    email: Optional[str] = None
    name: Optional[str] = None
    avatar_url: Optional[str] = None


class SecureAuthRequest(BaseModel):
    """
    Zero-knowledge asymmetric envelope encrypted authentication payload.
    Contains AES-256-GCM ciphertext and 4096-bit RSA-OAEP wrapped key.
    Raw password is never visible in the Network tab or over the wire.
    """
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+$", description="Valid email address")
    encrypted_payload: str = Field(..., description="Base64 encoded AES-256-GCM ciphertext")
    encrypted_key: str = Field(..., description="Base64 encoded RSA-OAEP 4096 wrapped AES key")
    iv: str = Field(..., description="Base64 encoded 12-byte initialization vector")
    auth_tag: Optional[str] = Field(None, description="Base64 encoded GCM authentication tag if separate")
    client_nonce: str = Field(..., min_length=16, description="Cryptographic client nonce for anti-replay")
    timestamp: int = Field(..., description="UTC millisecond client timestamp")
    key_id: Optional[str] = Field(None, description="Key identifier for rotated keys")


class PublicKeyResponse(BaseModel):
    key_id: str
    public_key_pem: str
    algorithm: str = "RSA-OAEP-256"



class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Union[UUID, str]
    email: str
    name: str
    role: str = "analyst"
    is_guest: bool = False
    avatar_url: Optional[str] = None
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse
