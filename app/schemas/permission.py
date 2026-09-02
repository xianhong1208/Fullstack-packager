"""Permission schemas."""

from pydantic import BaseModel


class PermissionBase(BaseModel):
    """Base permission schema."""

    code: str
    name: str
    description: str | None = None
    category: str = "general"


class PermissionCreate(PermissionBase):
    """Schema for permission creation."""

    pass


class PermissionResponse(BaseModel):
    """Schema for permission response."""

    id: int
    code: str
    name: str
    description: str | None
    category: str

    model_config = {"from_attributes": True}


class PermissionCategory(BaseModel):
    """Permissions grouped by category."""

    category: str
    permissions: list[PermissionResponse]
