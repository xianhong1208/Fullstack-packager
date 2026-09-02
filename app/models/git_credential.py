"""GitCredential ORM model: per-host Git access tokens managed in the console.

Replaces the single server-side GITLAB_TOKEN env var. Each row is one token for
one host (e.g. gitlab.com, github.com, or a self-hosted GitLab). The token is
stored encrypted (see app/services/crypto.py) and never returned by the API.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class GitCredential(Base):
    """A Git access token for one host, entered and managed in the console."""

    __tablename__ = "git_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # "gitlab" | "github" | "generic" — decides how the token is injected into the URL.
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    # Host the token authenticates against, lowercased (e.g. "github.com"). One token per host.
    host: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    # Optional human label shown in the console.
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Fernet ciphertext of the token; never exposed via the API.
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Last 4 chars of the plaintext token, for recognising it in the UI without revealing it.
    token_hint: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    creator: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return f"<GitCredential host={self.host!r} provider={self.provider!r}>"
