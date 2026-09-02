"""Audit logging service for security tracking."""

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


class AuditAction(str, Enum):
    """Predefined audit actions."""

    # Authentication
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    TOKEN_REFRESH = "token_refresh"

    # Password management
    PASSWORD_CHANGE = "password_change"
    PASSWORD_RESET_REQUEST = "password_reset_request"
    PASSWORD_RESET_COMPLETE = "password_reset_complete"
    ADMIN_PASSWORD_RESET = "admin_password_reset"

    # User management
    USER_CREATE = "user_create"
    USER_UPDATE = "user_update"
    USER_DELETE = "user_delete"
    USER_ACTIVATE = "user_activate"
    USER_DEACTIVATE = "user_deactivate"

    # Role management
    ROLE_CREATE = "role_create"
    ROLE_UPDATE = "role_update"
    ROLE_DELETE = "role_delete"
    ROLE_ASSIGN = "role_assign"

    # Permission changes
    PERMISSION_GRANT = "permission_grant"
    PERMISSION_REVOKE = "permission_revoke"

    # Session management
    SESSION_REVOKE = "session_revoke"


class AuditStatus(str, Enum):
    """Audit log status values."""

    SUCCESS = "success"
    FAILED = "failed"
    DENIED = "denied"


class ResourceType(str, Enum):
    """Resource types for audit logs."""

    USER = "user"
    ROLE = "role"
    PERMISSION = "permission"
    SESSION = "session"
    AUTH = "auth"
    TASK = "task"


async def log_action(
    db: AsyncSession,
    action: str | AuditAction,
    resource_type: str | ResourceType,
    status: str | AuditStatus,
    resource_id: str | int | None = None,
    actor_id: int | None = None,
    actor_name: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    error_message: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Record an audit log entry.

    Args:
        db: Database session
        action: The action being performed
        resource_type: Type of resource being acted upon
        status: Result status (success, failed, denied)
        resource_id: ID of the specific resource (optional)
        actor_id: User ID of the person performing the action
        actor_name: Username of the actor
        ip_address: Client IP address
        user_agent: Client user agent string
        error_message: Error message if action failed
        details: Additional context as a dictionary

    Returns:
        The created AuditLog entry
    """
    # Convert enums to strings
    if isinstance(action, AuditAction):
        action = action.value
    if isinstance(resource_type, ResourceType):
        resource_type = resource_type.value
    if isinstance(status, AuditStatus):
        status = status.value
    if resource_id is not None:
        resource_id = str(resource_id)

    # Serialize details to JSON
    details_json = None
    if details:
        details_json = json.dumps(details, default=str)

    audit_log = AuditLog(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_id=actor_id,
        actor_name=actor_name,
        ip_address=ip_address,
        user_agent=user_agent,
        status=status,
        error_message=error_message,
        details=details_json,
    )

    db.add(audit_log)
    await db.commit()
    await db.refresh(audit_log)

    return audit_log


async def get_audit_logs(
    db: AsyncSession,
    action: str | None = None,
    resource_type: str | None = None,
    actor_id: int | None = None,
    status: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditLog]:
    """Query audit logs with filters.

    Args:
        db: Database session
        action: Filter by action type
        resource_type: Filter by resource type
        actor_id: Filter by actor user ID
        status: Filter by status
        start_time: Filter logs after this time
        end_time: Filter logs before this time
        limit: Maximum number of results
        offset: Number of results to skip

    Returns:
        List of matching audit log entries
    """
    query = select(AuditLog)

    if action:
        query = query.where(AuditLog.action == action)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if actor_id:
        query = query.where(AuditLog.actor_id == actor_id)
    if status:
        query = query.where(AuditLog.status == status)
    if start_time:
        query = query.where(AuditLog.created_at >= start_time)
    if end_time:
        query = query.where(AuditLog.created_at <= end_time)

    query = query.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_user_audit_trail(
    db: AsyncSession,
    user_id: int,
    limit: int = 50,
) -> list[AuditLog]:
    """Get audit logs for actions performed by or on a specific user.

    Args:
        db: Database session
        user_id: User ID to get audit trail for
        limit: Maximum number of results

    Returns:
        List of audit log entries involving the user
    """
    query = (
        select(AuditLog)
        .where(
            (AuditLog.actor_id == user_id)
            | ((AuditLog.resource_type == "user") & (AuditLog.resource_id == str(user_id)))
        )
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )

    result = await db.execute(query)
    return list(result.scalars().all())
