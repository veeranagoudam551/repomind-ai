from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.repository import RepositoryStatus


class RepositoryCreate(BaseModel):
    github_url: str = Field(min_length=1, max_length=2048)


class RepositoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    github_url: str
    name: str
    description: Optional[str]
    default_branch: Optional[str]
    status: RepositoryStatus
    error_message: Optional[str]
    file_count: int
    total_size_bytes: int
    created_at: datetime
    updated_at: datetime


class RepositoryFileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_path: str
    language: Optional[str]
    size_bytes: int
    content_hash: Optional[str]
    created_at: datetime


class CodeChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    repository_file_id: UUID
    chunk_index: int
    content: str
    start_line: Optional[int]
    end_line: Optional[int]
    vector_id: Optional[str]
    created_at: datetime


class RepositorySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=50)


class CodeSearchResult(BaseModel):
    code_chunk_id: UUID
    file_path: str
    content: str
    start_line: Optional[int]
    end_line: Optional[int]
    score: float


class ExplainFileResponse(BaseModel):
    file_path: str
    explanation: str
