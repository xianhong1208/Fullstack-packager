"""LoginHistory ORM model for security logging."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LoginHistory(Base):
    """Login history model for tracking authentication attempts."""

    __tablename__ = "login_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    username_attempted: Mapped[str] = mapped_column(String(100), nullable=False)
    login_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)  # IPv6 support
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    browser: Mapped[str | None] = mapped_column(String(100), nullable=True)
    os: Mapped[str | None] = mapped_column(String(100), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    user: Mapped["User | None"] = relationship("User", back_populates="login_history")

    def __repr__(self) -> str:
        status = "success" if self.success else "failed"
        return f"<LoginHistory {self.id}: {self.username_attempted} ({status})>"


# Import at end to avoid circular imports
from app.models.user import User
