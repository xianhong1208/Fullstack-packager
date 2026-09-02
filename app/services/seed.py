"""Idempotent RBAC seeding: ensure the built-in permissions and roles exist.

Runs on startup so a fresh database (SQLite or PostgreSQL) is usable immediately:
the first user to register is given the 'admin' role, which must already carry the
wildcard permission. Safe to run on every boot — existing rows are left untouched.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.migrations.migrate_auth_v2 import PERMISSIONS, ROLES
from app.models.permission import Permission
from app.models.role import Role


async def seed_rbac(db: AsyncSession) -> None:
    """Create any missing permissions and roles, then link role permissions."""
    result = await db.execute(select(Permission))
    existing_perms = {p.code: p for p in result.scalars().all()}
    for perm_data in PERMISSIONS:
        if perm_data["code"] not in existing_perms:
            perm = Permission(**perm_data)
            db.add(perm)
            existing_perms[perm_data["code"]] = perm
    await db.flush()

    result = await db.execute(select(Role))
    existing_roles = {r.name for r in result.scalars().all()}
    for role_name, role_data in ROLES.items():
        if role_name in existing_roles:
            continue
        role = Role(
            name=role_name,
            display_name=role_data["display_name"],
            description=role_data["description"],
            is_system=role_data.get("is_system", False),
            is_active=True,
        )
        for perm_code in role_data["permissions"]:
            if perm_code in existing_perms:
                role.permissions.append(existing_perms[perm_code])
        db.add(role)
    await db.commit()
