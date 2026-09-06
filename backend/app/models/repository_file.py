import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPKMixin
from datetime import datetime

if TYPE_CHECKING:
    from app.models.code_chunk import CodeChunk
    from app.models.repository import Repository


class RepositoryFile(Base, UUIDPKMixin):
    __tablename__ = "repository_files"
    __table_args__ = (UniqueConstraint("repository_id", "file_path", name="uq_repository_file_path"),)

    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    language: Mapped[Optional[str]] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    repository: Mapped["Repository"] = relationship(back_populates="files")
    chunks: Mapped[list["CodeChunk"]] = relationship(
        back_populates="repository_file", cascade="all, delete-orphan"
    )
