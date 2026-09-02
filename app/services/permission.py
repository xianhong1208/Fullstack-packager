"""Permission service for role-based access control."""

from enum import Enum
from functools import wraps
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.user import User
from app.models.role import Role
from app.models.permission import Permission
from app.services.auth import decode_token


class PermissionCode(str, Enum):
    """All available permission codes."""

    # Task permissions
    TASK_CREATE = "task:create"
    TASK_VIEW_OWN = "task:view_own"
    TASK_VIEW_ALL = "task:view_all"
    TASK_CANCEL_OWN = "task:cancel_own"
    TASK_CANCEL_ALL = "task:cancel_all"

    # User management permissions
    USER_VIEW = "user:view"
    USER_MANAGE = "user:manage"
    USER_RESET_PASSWORD = "user:reset_password"

    # History permissions
    HISTORY_VIEW_OWN = "history:view_own"
    HISTORY_VIEW_ALL = "history:view_all"
    HISTORY_EXPORT = "history:export"

    # Role management permissions
    ROLE_VIEW = "role:view"
    ROLE_MANAGE = "role:manage"


# Default permission sets for roles
DEFAULT_ADMIN_PERMISSIONS = [p.value for p in PermissionCode]

DEFAULT_USER_PERMISSIONS = [
    PermissionCode.TASK_CREATE.value,
    PermissionCode.TASK_VIEW_OWN.value,
    PermissionCode.TASK_CANCEL_OWN.value,
    PermissionCode.HISTORY_VIEW_OWN.value,
    PermissionCode.HISTORY_EXPORT.value,
]


def has_permission(user_permissions: set[str], required: str) -> bool:
    """Check if user has a permission (supports wildcards).

    Wildcard patterns:
    - "*:*" matches all permissions
    - "resource:*" matches all actions on a resource (e.g., "task:*")
    - "*:action" matches an action on all resources (e.g., "*:view")

    Args:
        user_permissions: Set of permission codes the user has
        required: The permission code to check

    Returns:
        True if user has the required permission
    """
    # Direct match or superadmin wildcard
    if required in user_permissions or "*:*" in user_permissions:
        return True

    # Parse required permission
    if ":" not in required:
        return False

    resource, action = required.split(":", 1)

    # Check resource wildcard (e.g., "task:*" matches "task:create")
    if f"{resource}:*" in user_permissions:
        return True

    # Check action wildcard (e.g., "*:view" matches "task:view")
    if f"*:{action}" in user_permissions:
        return True

    return False


async def resolve_role_permissions(db: AsyncSession, role: "Role | None") -> set[str]:
    """Collect a role's own permissions plus everything it inherits.

    The chain is walked by foreign key rather than through Role.parent. The
    relationship version required the entire ancestry to be eager-loaded up
    front, and the two callers loaded different depths — three levels here,
    two in get_current_user. One level deeper than whatever was loaded,
    reading role.parent triggers a lazy load inside the async session and
    raises MissingGreenlet: a 500 on *every* request from anyone holding that
    role, with nothing in the traceback naming the role hierarchy. Nothing in
    the role API rejects a chain that deep, so it was reachable from the admin
    UI alone.

    Each hop past the first costs one query. A role with no parent — the
    default setup, and every role the seed data creates — takes none.

    An inactive role contributes nothing and stops the walk, so deactivating a
    role also revokes what it lends to its children.
    """
    permissions: set[str] = set()
    visited: set[int] = set()
    current = role

    while current is not None and current.is_active and current.id not in visited:
        visited.add(current.id)
        permissions.update(p.code for p in current.permissions)

        parent_id = current.parent_role_id
        # A cycle is not rejected at creation either; revisiting is the exit.
        if parent_id is None or parent_id in visited:
            break

        result = await db.execute(
            select(Role)
            .where(Role.id == parent_id)
            .options(selectinload(Role.permissions))
        )
        current = result.scalar_one_or_none()

    return permissions


async def get_user_permissions(db: AsyncSession, user_id: int) -> set[str]:
    """Get all permission codes for a user (including inherited from parent roles)."""
    result = await db.execute(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.role).selectinload(Role.permissions))
    )
    user = result.scalar_one_or_none()

    if not user or not user.role:
        return set()

    return await resolve_role_permissions(db, user.role)


async def check_permission(
    db: AsyncSession,
    user_id: int,
    permission_code: str | PermissionCode,
) -> bool:
    """Check if a user has a specific permission (supports wildcards)."""
    if isinstance(permission_code, PermissionCode):
        permission_code = permission_code.value

    permissions = await get_user_permissions(db, user_id)
    return has_permission(permissions, permission_code)


async def get_role_by_name(db: AsyncSession, name: str) -> Role | None:
    """Get a role by its name."""
    result = await db.execute(
        select(Role)
        .where(Role.name == name)
        .options(selectinload(Role.permissions))
    )
    return result.scalar_one_or_none()


async def get_all_roles(db: AsyncSession) -> list[Role]:
    """Get all roles with their permissions."""
    result = await db.execute(
        select(Role).options(selectinload(Role.permissions))
    )
    return list(result.scalars().all())


async def get_all_permissions(db: AsyncSession) -> list[Permission]:
    """Get all available permissions."""
    result = await db.execute(select(Permission).order_by(Permission.category, Permission.code))
    return list(result.scalars().all())


# FastAPI dependency for permission checking
def require_permission(*required_permissions: str | PermissionCode):
    """
    FastAPI dependency that requires specific permissions.

    Usage:
        @router.get("/admin", dependencies=[Depends(require_permission("user:manage"))])
        async def admin_endpoint():
            ...

    Or with multiple permissions (user must have ALL):
        @router.get("/admin", dependencies=[Depends(require_permission("user:view", "user:manage"))])
    """
    # Convert any PermissionCode enums to strings
    permission_codes = [
        p.value if isinstance(p, PermissionCode) else p
        for p in required_permissions
    ]

    async def permission_checker(
        current_user: Annotated["CurrentUser", Depends(get_current_user)],
        db: AsyncSession = Depends(get_db),
    ) -> None:
        user_permissions = await get_user_permissions(db, current_user.id)

        missing = [p for p in permission_codes if not has_permission(user_permissions, p)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permissions: {', '.join(missing)}",
            )

    return permission_checker


def require_any_permission(*required_permissions: str | PermissionCode):
    """
    FastAPI dependency that requires at least ONE of the specified permissions.

    Usage:
        @router.get("/tasks", dependencies=[Depends(require_any_permission("task:view_own", "task:view_all"))])
    """
    permission_codes = [
        p.value if isinstance(p, PermissionCode) else p
        for p in required_permissions
    ]

    async def permission_checker(
        current_user: Annotated["CurrentUser", Depends(get_current_user)],
        db: AsyncSession = Depends(get_db),
    ) -> None:
        user_permissions = await get_user_permissions(db, current_user.id)

        if not any(has_permission(user_permissions, p) for p in permission_codes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires at least one of: {', '.join(permission_codes)}",
            )

    return permission_checker


# Type alias for current user dependency
class CurrentUser:
    """Represents the current authenticated user from JWT token."""

    def __init__(self, id: int, username: str, permissions: list[str], role: str | None):
        self.id = id
        self.username = username
        self.permissions = set(permissions)
        self.role = role

    def has_permission(self, permission: str | PermissionCode) -> bool:
        """Check if user has a specific permission (supports wildcards)."""
        code = permission.value if isinstance(permission, PermissionCode) else permission
        return has_permission(self.permissions, code)

    def has_any_permission(self, *permissions: str | PermissionCode) -> bool:
        """Check if user has any of the specified permissions (supports wildcards)."""
        codes = [p.value if isinstance(p, PermissionCode) else p for p in permissions]
        return any(has_permission(self.permissions, c) for c in codes)


from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """Get the current authenticated user from JWT token."""
    token = credentials.credentials
    payload = decode_token(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = int(payload.get("sub", 0))
    username = payload.get("username", "")
    permissions = payload.get("permissions", [])
    role = payload.get("role")

    # Verify user still exists and is active, and re-fetch permissions from DB
    # to ensure revoked permissions take effect immediately
    result = await db.execute(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.role).selectinload(Role.permissions))
    )
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Use DB permissions instead of JWT payload permissions
    if user.role and user.role.is_active:
        db_permissions = list(await resolve_role_permissions(db, user.role))
        db_role = user.role.name
    else:
        db_permissions = []
        db_role = None

    return CurrentUser(
        id=user_id,
        username=username,
        permissions=db_permissions,
        role=db_role,
    )


async def get_optional_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))],
    db: AsyncSession = Depends(get_db),
) -> CurrentUser | None:
    """Get current user if authenticated, otherwise return None."""
    if not credentials:
        return None

    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None
