"""PasswordResetToken ORM model for password recovery."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PasswordResetToken(Base):
    """Password reset token model for account recovery via security questions."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reset_method: Mapped[str] = mapped_column(
        String(50), nullable=False, default="security_question"
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="password_reset_tokens")

    @property
    def is_valid(self) -> bool:
        """Check if token is still valid (not expired and not used)."""
        from datetime import timezone
        now = datetime.now(timezone.utc)
        return self.used_at is None and self.expires_at > now

    def __repr__(self) -> str:
        status = "valid" if self.is_valid else "invalid"
        return f"<PasswordResetToken {self.id}: user={self.user_id} ({status})>"


# Import at end to avoid circular imports
from app.models.user import User
