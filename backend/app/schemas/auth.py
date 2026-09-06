from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    full_name: Optional[str] = Field(default=None, max_length=255)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: Optional[str]
    is_active: bool
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
