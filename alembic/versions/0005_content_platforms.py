"""Content item platforms

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-02 17:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("content_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("platforms", sa.String(length=100), server_default="", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("content_items", schema=None) as batch_op:
        batch_op.drop_column("platforms")
