"""Role schemas for RBAC."""

from datetime import datetime

from pydantic import BaseModel


class PermissionResponse(BaseModel):
    """Schema for permission response."""

    id: int
    code: str
    name: str
    description: str | None
    category: str

    model_config = {"from_attributes": True}


class RoleBase(BaseModel):
    """Base role schema."""

    name: str
    display_name: str
    description: str | None = None


class RoleCreate(RoleBase):
    """Schema for role creation."""

    permission_codes: list[str] = []
    parent_role_id: int | None = None


class RoleUpdate(BaseModel):
    """Schema for role update."""

    display_name: str | None = None
    description: str | None = None
    permission_codes: list[str] | None = None
    parent_role_id: int | None = None
    is_active: bool | None = None


class RoleResponse(BaseModel):
    """Schema for role response."""

    id: int
    name: str
    display_name: str
    description: str | None
    created_at: datetime
    permissions: list[PermissionResponse] = []
    parent_role_id: int | None = None
    is_system: bool = False
    is_active: bool = True

    model_config = {"from_attributes": True}


class RoleSummary(BaseModel):
    """Simplified role response without permissions list."""

    id: int
    name: str
    display_name: str

    model_config = {"from_attributes": True}
