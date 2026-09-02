"""
Auth System V2 Migration Script

This script upgrades the authentication system to include:
- Roles and Permissions (RBAC)
- Refresh Tokens (Remember Me)
- Login History
- Password Reset Tokens
- Security Questions

Usage:
    python -m app.migrations.migrate_auth_v2

The script is idempotent - it can be run multiple times safely.
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import text, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine, async_session, Base
from app.models.role import Role
from app.models.permission import Permission, role_permissions
from app.models.user import User
from app.models.login_history import LoginHistory
from app.models.refresh_token import RefreshToken
from app.models.password_reset import PasswordResetToken


# ===== Permission Definitions =====

PERMISSIONS = [
    # Superadmin wildcard
    {"code": "*:*", "name": "Super Admin", "description": "Full access to all resources and actions", "category": "system"},
    # Task permissions
    {"code": "task:*", "name": "All Task Permissions", "description": "Full access to task operations", "category": "task"},
    {"code": "task:create", "name": "Create Task", "description": "Create new build tasks", "category": "task"},
    {"code": "task:view_own", "name": "View Own Tasks", "description": "View own tasks", "category": "task"},
    {"code": "task:view_all", "name": "View All Tasks", "description": "View all users' tasks", "category": "task"},
    {"code": "task:cancel_own", "name": "Cancel Own Tasks", "description": "Cancel own tasks", "category": "task"},
    {"code": "task:cancel_all", "name": "Cancel All Tasks", "description": "Cancel any user's tasks", "category": "task"},
    # User management permissions
    {"code": "user:*", "name": "All User Permissions", "description": "Full access to user management", "category": "user"},
    {"code": "user:view", "name": "View Users", "description": "View user list and details", "category": "user"},
    {"code": "user:manage", "name": "Manage Users", "description": "Update user roles and status", "category": "user"},
    {"code": "user:reset_password", "name": "Reset Passwords", "description": "Reset other users' passwords", "category": "user"},
    # History permissions
    {"code": "history:*", "name": "All History Permissions", "description": "Full access to history", "category": "history"},
    {"code": "history:view_own", "name": "View Own History", "description": "View own task history", "category": "history"},
    {"code": "history:view_all", "name": "View All History", "description": "View all users' task history", "category": "history"},
    {"code": "history:export", "name": "Export History", "description": "Export task history as CSV", "category": "history"},
    # Role management permissions
    {"code": "role:*", "name": "All Role Permissions", "description": "Full access to role management", "category": "role"},
    {"code": "role:view", "name": "View Roles", "description": "View role list and details", "category": "role"},
    {"code": "role:manage", "name": "Manage Roles", "description": "Create, update, delete roles", "category": "role"},
]

# Role definitions with their permissions
ROLES = {
    "admin": {
        "display_name": "Administrator",
        "description": "Full system access",
        "permissions": ["*:*"],  # Superadmin wildcard - all permissions
        "is_system": True,
        "parent_role": None,
    },
    "user": {
        "display_name": "User",
        "description": "Standard user access",
        "permissions": [
            "task:create",
            "task:view_own",
            "task:cancel_own",
            "history:view_own",
            "history:export",
        ],
        "is_system": True,
        "parent_role": None,
    },
}


async def check_table_exists(conn, table_name: str) -> bool:
    """Check if a table exists in the database."""
    result = await conn.execute(
        text(f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '{table_name}')")
    )
    return result.scalar()


async def check_column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    result = await conn.execute(
        text(f"""
            SELECT EXISTS (
                SELECT FROM information_schema.columns
                WHERE table_name = '{table_name}' AND column_name = '{column_name}'
            )
        """)
    )
    return result.scalar()


async def add_column_if_not_exists(conn, table_name: str, column_name: str, column_def: str):
    """Add a column to a table if it doesn't exist."""
    exists = await check_column_exists(conn, table_name, column_name)
    if not exists:
        print(f"  Adding column {column_name} to {table_name}...")
        await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"))
        return True
    return False


async def migrate_schema(conn):
    """Migrate database schema - create new tables and add new columns."""
    print("\n=== Schema Migration ===\n")

    # Create new tables using SQLAlchemy's create_all
    # This will create tables that don't exist
    print("Creating new tables if needed...")
    await conn.run_sync(Base.metadata.create_all)

    # Add new columns to users table if they don't exist
    print("Checking users table columns...")

    columns_to_add = [
        ("email", "VARCHAR(255) UNIQUE"),
        ("role_id", "INTEGER REFERENCES roles(id) ON DELETE SET NULL"),
        ("is_active", "BOOLEAN DEFAULT TRUE NOT NULL"),
        ("created_at", "TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL"),
        ("updated_at", "TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL"),
        ("last_login", "TIMESTAMP WITH TIME ZONE"),
        ("security_question", "TEXT"),
        ("security_answer_hash", "VARCHAR(255)"),
    ]

    for col_name, col_def in columns_to_add:
        added = await add_column_if_not_exists(conn, "users", col_name, col_def)
        if added:
            print(f"  + Added column: {col_name}")

    await conn.commit()
    print("Schema migration complete.")


async def seed_permissions(db: AsyncSession):
    """Create all permissions."""
    print("\n=== Seeding Permissions ===\n")

    for perm_data in PERMISSIONS:
        result = await db.execute(select(Permission).where(Permission.code == perm_data["code"]))
        existing = result.scalar_one_or_none()

        if not existing:
            permission = Permission(**perm_data)
            db.add(permission)
            print(f"  + Created permission: {perm_data['code']}")
        else:
            print(f"  - Permission exists: {perm_data['code']}")

    await db.commit()
    print("Permissions seeded.")


async def seed_roles(db: AsyncSession):
    """Create roles and assign permissions."""
    print("\n=== Seeding Roles ===\n")

    # Get all permissions
    result = await db.execute(select(Permission))
    all_permissions = {p.code: p for p in result.scalars().all()}

    for role_name, role_data in ROLES.items():
        result = await db.execute(select(Role).where(Role.name == role_name))
        existing = result.scalar_one_or_none()

        if not existing:
            role = Role(
                name=role_name,
                display_name=role_data["display_name"],
                description=role_data["description"],
                is_system=role_data.get("is_system", False),
                is_active=True,
            )
            # Add permissions
            for perm_code in role_data["permissions"]:
                if perm_code in all_permissions:
                    role.permissions.append(all_permissions[perm_code])
            db.add(role)
            print(f"  + Created role: {role_name} with {len(role_data['permissions'])} permissions")
        else:
            print(f"  - Role exists: {role_name}")
            # Optionally update permissions for existing roles
            # existing.permissions = [all_permissions[p] for p in role_data["permissions"] if p in all_permissions]

    await db.commit()
    print("Roles seeded.")


async def migrate_users(db: AsyncSession):
    """Migrate existing users - assign roles."""
    print("\n=== Migrating Users ===\n")

    # Get roles
    result = await db.execute(select(Role).where(Role.name == "admin"))
    admin_role = result.scalar_one_or_none()

    result = await db.execute(select(Role).where(Role.name == "user"))
    user_role = result.scalar_one_or_none()

    if not admin_role or not user_role:
        print("  ! Roles not found - run seed_roles first")
        return

    # Get all users without a role
    result = await db.execute(select(User).where(User.role_id.is_(None)).order_by(User.id))
    users = result.scalars().all()

    if not users:
        print("  - No users need migration")
        return

    for i, user in enumerate(users):
        # First user becomes admin
        if i == 0:
            user.role_id = admin_role.id
            print(f"  + Assigned admin role to: {user.username} (first user)")
        else:
            user.role_id = user_role.id
            print(f"  + Assigned user role to: {user.username}")

    await db.commit()
    print("User migration complete.")


async def run_migration():
    """Run the full migration."""
    print("=" * 60)
    print("Build Center - Auth System V2 Migration")
    print("=" * 60)

    # Step 1: Schema migration (create tables, add columns)
    async with engine.begin() as conn:
        await migrate_schema(conn)

    # Step 2: Seed data (permissions, roles)
    async with async_session() as db:
        await seed_permissions(db)
        await seed_roles(db)
        await migrate_users(db)

    print("\n" + "=" * 60)
    print("Migration completed successfully!")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Restart the backend server")
    print("2. Existing users will need to re-login")
    print("3. The first user has been assigned the admin role")
    print()


if __name__ == "__main__":
    asyncio.run(run_migration())
