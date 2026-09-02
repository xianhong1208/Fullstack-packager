"""add user oauth identity columns

For MCP Center single sign-on: link a local account to an external identity
(provider + subject). See app/models/user.py and app/services/mcp_oauth.py.

Revision ID: d8e2f3a4b5c6
Revises: c7f1a2b3d4e5
Create Date: 2026-09-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c7f1a2b3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("oauth_provider", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("oauth_subject", sa.String(length=255), nullable=True))
    op.create_index("ix_users_oauth_subject", "users", ["oauth_subject"])


def downgrade() -> None:
    op.drop_index("ix_users_oauth_subject", table_name="users")
    op.drop_column("users", "oauth_subject")
    op.drop_column("users", "oauth_provider")
