from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.message import MessageRole


class ConversationCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=255)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    repository_id: UUID
    title: Optional[str]
    created_at: datetime
    updated_at: datetime


class MessageSource(BaseModel):
    code_chunk_id: UUID
    file_path: str
    start_line: Optional[int]
    end_line: Optional[int]


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class MessageRead(BaseModel):
    id: UUID
    role: MessageRole
    content: str
    sources: list[MessageSource]
    created_at: datetime
