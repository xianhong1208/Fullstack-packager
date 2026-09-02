"""Audit log ORM model for security tracking."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditLog(Base):
    """Audit log for tracking security-relevant actions."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Action details
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Actor information
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Request context
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Result
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Additional context (JSON-serializable data)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.id}: {self.action} on {self.resource_type}>"
