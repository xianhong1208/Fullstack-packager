"""add git_credentials table

Stores per-host Git access tokens managed in the console (encrypted at rest),
replacing the single GITLAB_TOKEN env var. See app/models/git_credential.py.

Revision ID: c7f1a2b3d4e5
Revises: a1c4e9f27b30
Create Date: 2026-09-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7f1a2b3d4e5"
down_revision: Union[str, Sequence[str], None] = "a1c4e9f27b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "git_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("token_hint", sa.String(length=8), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("host", name="uq_git_credentials_host"),
    )
    op.create_index("ix_git_credentials_host", "git_credentials", ["host"])


def downgrade() -> None:
    op.drop_index("ix_git_credentials_host", table_name="git_credentials")
    op.drop_table("git_credentials")
