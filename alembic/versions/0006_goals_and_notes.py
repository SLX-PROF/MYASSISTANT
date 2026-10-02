"""Savings goals and notes

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-02 20:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fin_goals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("target", sa.Integer(), nullable=False),
        sa.Column("saved", sa.Integer(), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("color", sa.String(length=9), nullable=False),
        sa.Column("done_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("tags", sa.String(length=300), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notes_created_at", "notes", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_notes_created_at", table_name="notes")
    op.drop_table("notes")
    op.drop_table("fin_goals")
