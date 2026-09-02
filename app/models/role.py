"""Role ORM model for RBAC."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Role(Base):
    """Role model for role-based access control."""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Role inheritance
    parent_role_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("roles.id", ondelete="SET NULL"), nullable=True
    )

    # System role protection (cannot be deleted or renamed)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Active status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    users: Mapped[list["User"]] = relationship("User", back_populates="role")
    permissions: Mapped[list["Permission"]] = relationship(
        "Permission",
        secondary="role_permissions",
        back_populates="roles",
    )

    # Self-referential relationship for inheritance
    parent: Mapped["Role | None"] = relationship(
        "Role",
        remote_side=[id],
        backref="children",
        foreign_keys=[parent_role_id],
    )

    def __repr__(self) -> str:
        return f"<Role {self.id}: {self.name}>"


# Import at end to avoid circular imports
from app.models.user import User
from app.models.permission import Permission
