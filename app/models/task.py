"""Task ORM model for database persistence."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.schemas.task import TaskStatus


class Task(Base):
    """Task model for storing build task history."""

    __tablename__ = "history"

    task_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        index=True,
    )
    user_name: Mapped[str] = mapped_column(String(100), nullable=False)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    python_version: Mapped[str] = mapped_column(String(10), nullable=False)
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    end_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Enum(TaskStatus),
        default=TaskStatus.PENDING,
        nullable=False,
    )
    output_dir: Mapped[str] = mapped_column(String(500), nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Structured build outcome (preflight checks, artifact summary, smoke-test
    # verdict). Added after the table already existed — see init_db()'s
    # idempotent ALTER for the additive migration on existing databases.
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:
        return f"<Task {self.task_id}: {self.project_name} ({self.status})>"
