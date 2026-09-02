"""User ORM model for authentication."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    """User model for authentication with RBAC support."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # New fields for enhanced authentication
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    role_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("roles.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Security question for password recovery
    security_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    security_answer_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # External identity when the account signs in through MCP Center SSO.
    oauth_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    oauth_subject: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # Relationships
    role: Mapped["Role | None"] = relationship("Role", back_populates="users")
    login_history: Mapped[list["LoginHistory"]] = relationship(
        "LoginHistory", back_populates="user", cascade="all, delete-orphan"
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )
    password_reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(
        "PasswordResetToken", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User {self.id}: {self.username}>"


# Import at end to avoid circular imports
from app.models.role import Role
from app.models.login_history import LoginHistory
from app.models.refresh_token import RefreshToken
from app.models.password_reset import PasswordResetToken
