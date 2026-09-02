"""add history.result

Brings alembic back in sync with app/models/task.py.

The `result` column was added to the Task model after the initial schema was
cut, but no migration went with it — instead `init_db()` carried a hand-written
`ALTER TABLE history ADD COLUMN IF NOT EXISTS result JSON` that ran on every
startup. That worked, but it meant `alembic upgrade head` alone produced a
database the application could not query, and it set the precedent that model
changes do not need migrations.

This migration is written to be idempotent (IF NOT EXISTS / IF EXISTS) so it
is safe to run against databases that already have the column from the old
startup path.

Revision ID: a1c4e9f27b30
Revises: 838782876161
Create Date: 2026-08-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1c4e9f27b30'
down_revision: Union[str, Sequence[str], None] = '838782876161'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    """Add history.result if it is not already present (dialect-agnostic)."""
    if not _has_column("history", "result"):
        op.add_column("history", sa.Column("result", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drop history.result if present."""
    if _has_column("history", "result"):
        with op.batch_alter_table("history") as batch_op:
            batch_op.drop_column("result")
