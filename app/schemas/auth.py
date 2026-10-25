from datetime import datetime
from typing import Optional, Union
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


class UserRegisterRequest(BaseModel):
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", description="Valid email address")
    password: str = Field(..., min_length=6, description="Password must be at least 6 characters")
    name: str = Field(..., min_length=2, max_length=100, description="Full Name")


class UserLoginRequest(BaseModel):
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", description="Valid email address")
    password: str


class GoogleAuthRequest(BaseModel):
    id_token: str = Field(..., description="Google OAuth 2.0 credential/ID token")
    email: Optional[str] = None
    name: Optional[str] = None
    avatar_url: Optional[str] = None


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
