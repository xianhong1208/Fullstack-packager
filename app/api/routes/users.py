"""User management API routes (Admin)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.user import User
from app.models.role import Role
from app.models.login_history import LoginHistory
from app.schemas.user import (
    UserListResponse,
    UserResponse,
    UserRoleUpdate,
    UserStatusUpdate,
    AdminPasswordReset,
    LoginHistoryResponse,
    LoginHistoryList,
)
from app.schemas.role import RoleResponse, RoleCreate, RoleUpdate, PermissionResponse
from app.services.audit import AuditAction, AuditStatus, ResourceType, log_action
from app.services.auth import admin_reset_password, get_login_history
from app.services.permission import (
    get_current_user,
    CurrentUser,
    require_permission,
    PermissionCode,
    get_all_roles,
    get_all_permissions,
)

router = APIRouter(prefix="/api/users", tags=["users"])


# ===== User List & Details =====


@router.get("", response_model=list[UserListResponse])
async def list_users(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_permission(PermissionCode.USER_VIEW)),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: str | None = None,
    is_active: bool | None = None,
    role_id: int | None = None,
):
    """Get list of all users (requires user:view permission)."""
    query = select(User).options(selectinload(User.role))

    # Apply filters
    if search:
        safe_search = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(User.username.ilike(f"%{safe_search}%"))
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    if role_id is not None:
        query = query.where(User.role_id == role_id)

    query = query.order_by(User.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    users = result.scalars().all()

    return [
        UserListResponse(
            id=u.id,
            username=u.username,
            email=u.email,
            is_active=u.is_active,
            created_at=u.created_at,
            last_login=u.last_login,
            role=u.role,
        )
        for u in users
    ]


@router.get("/count")
async def get_users_count(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_permission(PermissionCode.USER_VIEW)),
    search: str | None = None,
    is_active: bool | None = None,
    role_id: int | None = None,
):
    """Get total count of users (with filters)."""
    query = select(func.count()).select_from(User)

    if search:
        safe_search = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(User.username.ilike(f"%{safe_search}%"))
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    if role_id is not None:
        query = query.where(User.role_id == role_id)

    result = await db.execute(query)
    return {"count": result.scalar() or 0}


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_permission(PermissionCode.USER_VIEW)),
):
    """Get user details by ID (requires user:view permission)."""
    result = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.role))
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        role=user.role,
        has_security_question=bool(user.security_question),
    )


# ===== User Management =====


@router.put("/{user_id}/role")
async def update_user_role(
    user_id: int,
    data: UserRoleUpdate,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_permission(PermissionCode.USER_MANAGE)),
):
    """Update user's role (requires user:manage permission)."""
    # Prevent self-demotion for safety
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role",
        )

    # Verify user exists
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Verify role exists (permissions eager-loaded for the escalation check)
    result = await db.execute(
        select(Role)
        .where(Role.id == data.role_id)
        .options(selectinload(Role.permissions))
    )
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role not found",
        )

    # Privilege-escalation guard: a user:manage holder must not hand out
    # permissions they don't hold themselves (e.g. assigning admin to a
    # second account they control). role:manage holders may assign freely.
    if not current_user.has_permission(PermissionCode.ROLE_MANAGE):
        # Walk the inheritance chain iteratively — each hop is an explicit
        # eager-loaded query (lazy-loading role.parent would blow up under
        # the async session).
        role_perms = {p.code for p in role.permissions}
        seen_ids = {role.id}
        parent_id = role.parent_role_id
        while parent_id and parent_id not in seen_ids:
            seen_ids.add(parent_id)
            parent_result = await db.execute(
                select(Role)
                .where(Role.id == parent_id)
                .options(selectinload(Role.permissions))
            )
            parent = parent_result.scalar_one_or_none()
            if not parent or not parent.is_active:
                break
            role_perms.update(p.code for p in parent.permissions)
            parent_id = parent.parent_role_id

        if not role_perms.issubset(set(current_user.permissions)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Cannot assign a role with permissions beyond your own "
                    "(requires role:manage)"
                ),
            )

    previous_role_id = user.role_id
    user.role_id = data.role_id
    await db.commit()

    # Promoting an account is the single most consequential thing this API can
    # do, and it left no trace: a stolen user:manage session could grant itself
    # admin via a second account and the only evidence would be the current
    # value of users.role_id, which carries no history (updated_at is not
    # touched by a role change either).
    await log_action(
        db,
        action=AuditAction.ROLE_ASSIGN,
        resource_type=ResourceType.USER,
        status=AuditStatus.SUCCESS,
        resource_id=user.id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        details={
            "target_user": user.username,
            "from_role_id": previous_role_id,
            "to_role_id": data.role_id,
        },
    )

    return {"message": f"User role updated to {role.display_name}"}


@router.put("/{user_id}/status")
async def update_user_status(
    user_id: int,
    data: UserStatusUpdate,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_permission(PermissionCode.USER_MANAGE)),
):
    """Enable or disable a user account (requires user:manage permission)."""
    # Prevent self-deactivation
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own status",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    previous_active = user.is_active
    user.is_active = data.is_active
    await db.commit()

    # Activation is the approval gate the whole trust model rests on — a new
    # account is inert until someone flips this, and task:create is effectively
    # shell access on the build host. Who approved whom needs to be answerable.
    await log_action(
        db,
        action=AuditAction.USER_ACTIVATE if data.is_active else AuditAction.USER_DEACTIVATE,
        resource_type=ResourceType.USER,
        status=AuditStatus.SUCCESS,
        resource_id=user.id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        details={
            "target_user": user.username,
            "from_active": previous_active,
            "to_active": data.is_active,
        },
    )

    status_text = "activated" if data.is_active else "deactivated"
    return {"message": f"User account {status_text}"}


@router.post("/{user_id}/reset-password")
async def reset_user_password(
    user_id: int,
    data: AdminPasswordReset,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_permission(PermissionCode.USER_RESET_PASSWORD)),
):
    """Admin reset user's password (requires user:reset_password permission)."""
    success = await admin_reset_password(db, user_id, data.new_password)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # admin_reset_password also revokes every refresh token, so this both
    # changes the credential and terminates all of that user's sessions.
    await log_action(
        db,
        action=AuditAction.ADMIN_PASSWORD_RESET,
        resource_type=ResourceType.USER,
        status=AuditStatus.SUCCESS,
        resource_id=user_id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        details={"sessions_revoked": True},
    )

    return {"message": "Password reset successfully. User will need to log in with the new password."}


@router.get("/{user_id}/login-history", response_model=LoginHistoryList)
async def get_user_login_history(
    user_id: int,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_permission(PermissionCode.USER_VIEW)),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Get login history for a specific user (requires user:view permission)."""
    # Verify user exists
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    history = await get_login_history(db, user_id, limit=limit, offset=offset)

    # Get total count
    result = await db.execute(
        select(func.count()).select_from(LoginHistory).where(LoginHistory.user_id == user_id)
    )
    total = result.scalar() or 0

    return LoginHistoryList(
        items=[LoginHistoryResponse.model_validate(h) for h in history],
        total=total,
        limit=limit,
        offset=offset,
    )


# ===== Roles & Permissions =====


@router.get("/roles/list", response_model=list[RoleResponse])
async def list_roles(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_permission(PermissionCode.USER_VIEW)),
):
    """Get list of all roles (requires user:view permission)."""
    roles = await get_all_roles(db)
    return [RoleResponse.model_validate(r) for r in roles]


@router.get("/permissions/list")
async def list_permissions(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_permission(PermissionCode.USER_VIEW)),
):
    """Get list of all permissions grouped by category (requires user:view permission)."""
    permissions = await get_all_permissions(db)

    # Group by category
    categories: dict[str, list] = {}
    for p in permissions:
        if p.category not in categories:
            categories[p.category] = []
        categories[p.category].append({
            "id": p.id,
            "code": p.code,
            "name": p.name,
            "description": p.description,
        })

    return [
        {"category": cat, "permissions": perms}
        for cat, perms in categories.items()
    ]


# ===== Role Management (CRUD) =====

from app.models.permission import Permission


@router.post("/roles", response_model=RoleResponse)
async def create_role(
    data: RoleCreate,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_permission(PermissionCode.ROLE_MANAGE)),
):
    """Create a new role (requires role:manage permission)."""
    # Check if role name already exists
    result = await db.execute(select(Role).where(Role.name == data.name))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role name already exists",
        )

    # Verify parent role exists if specified
    if data.parent_role_id:
        result = await db.execute(select(Role).where(Role.id == data.parent_role_id))
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent role not found",
            )

    # Get permissions by codes
    permissions = []
    if data.permission_codes:
        result = await db.execute(
            select(Permission).where(Permission.code.in_(data.permission_codes))
        )
        permissions = list(result.scalars().all())

    # Create role
    role = Role(
        name=data.name,
        display_name=data.display_name,
        description=data.description,
        parent_role_id=data.parent_role_id,
        is_system=False,
        is_active=True,
    )
    role.permissions = permissions

    db.add(role)
    await db.commit()
    await db.refresh(role)

    # Reload with permissions
    result = await db.execute(
        select(Role).where(Role.id == role.id).options(selectinload(Role.permissions))
    )
    role = result.scalar_one()

    await log_action(
        db,
        action=AuditAction.ROLE_CREATE,
        resource_type=ResourceType.ROLE,
        status=AuditStatus.SUCCESS,
        resource_id=role.id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        details={
            "role_name": role.name,
            "parent_role_id": role.parent_role_id,
            "permissions": sorted(p.code for p in role.permissions),
        },
    )

    return RoleResponse.model_validate(role)


@router.get("/roles/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: int,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_permission(PermissionCode.ROLE_VIEW)),
):
    """Get role details by ID (requires role:view permission)."""
    result = await db.execute(
        select(Role).where(Role.id == role_id).options(selectinload(Role.permissions))
    )
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
        )

    return RoleResponse.model_validate(role)


@router.put("/roles/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: int,
    data: RoleUpdate,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_permission(PermissionCode.ROLE_MANAGE)),
):
    """Update a role (requires role:manage permission)."""
    result = await db.execute(
        select(Role).where(Role.id == role_id).options(selectinload(Role.permissions))
    )
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
        )

    # Prevent modifying system roles' core properties
    if role.is_system and data.is_active is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate system roles",
        )

    # Update fields
    if data.display_name is not None:
        role.display_name = data.display_name
    if data.description is not None:
        role.description = data.description
    if data.is_active is not None and not role.is_system:
        role.is_active = data.is_active
    if data.parent_role_id is not None:
        # Verify parent role exists and prevent circular reference
        if data.parent_role_id == role_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Role cannot be its own parent",
            )
        if data.parent_role_id != 0:
            result = await db.execute(select(Role).where(Role.id == data.parent_role_id))
            if not result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Parent role not found",
                )
            role.parent_role_id = data.parent_role_id
        else:
            role.parent_role_id = None

    # Captured before the assignment below replaces the collection: an audit
    # entry saying "permissions changed" without saying *from what* cannot
    # answer the only question anyone asks it afterwards.
    previous_permissions = sorted(p.code for p in role.permissions)

    # Update permissions if provided
    if data.permission_codes is not None:
        result = await db.execute(
            select(Permission).where(Permission.code.in_(data.permission_codes))
        )
        role.permissions = list(result.scalars().all())

    await db.commit()
    await db.refresh(role)

    # Reload with permissions
    result = await db.execute(
        select(Role).where(Role.id == role.id).options(selectinload(Role.permissions))
    )
    role = result.scalar_one()

    current_permissions = sorted(p.code for p in role.permissions)
    await log_action(
        db,
        action=AuditAction.ROLE_UPDATE,
        resource_type=ResourceType.ROLE,
        status=AuditStatus.SUCCESS,
        resource_id=role.id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        details={
            "role_name": role.name,
            "permissions_added": sorted(
                set(current_permissions) - set(previous_permissions)
            ),
            "permissions_removed": sorted(
                set(previous_permissions) - set(current_permissions)
            ),
        },
    )

    return RoleResponse.model_validate(role)


@router.delete("/roles/{role_id}")
async def delete_role(
    role_id: int,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_permission(PermissionCode.ROLE_MANAGE)),
):
    """Delete a role (requires role:manage permission)."""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
        )

    # Prevent deleting system roles
    if role.is_system:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete system roles",
        )

    # Check if any users have this role
    result = await db.execute(select(func.count()).select_from(User).where(User.role_id == role_id))
    user_count = result.scalar() or 0

    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete role: {user_count} users are assigned to this role",
        )

    await db.delete(role)
    await db.commit()

    await log_action(
        db,
        action=AuditAction.ROLE_DELETE,
        resource_type=ResourceType.ROLE,
        status=AuditStatus.SUCCESS,
        resource_id=role_id,
        actor_id=current_user.id,
        actor_name=current_user.username,
        details={"role_name": role.name},
    )

    return {"message": f"Role '{role.name}' deleted successfully"}
