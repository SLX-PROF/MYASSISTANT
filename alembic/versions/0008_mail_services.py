"""Mail inventory (services found in the mailbox history)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-02 23:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mail_services",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=200), nullable=False),
        sa.Column("accounts", sa.String(length=500), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("suggest", sa.String(length=20), nullable=False),
        sa.Column("price", sa.String(length=60), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column("subjects", sa.JSON(), nullable=False),
        sa.Column("unsubscribe", sa.String(length=1000), nullable=False),
        sa.Column("one_click", sa.Boolean(), nullable=False),
        sa.Column("welcome", sa.Boolean(), nullable=False),
        sa.Column("payment", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain"),
    )
    op.create_index("ix_mail_services_category", "mail_services", ["category"], unique=False)
    op.create_index("ix_mail_services_last_seen", "mail_services", ["last_seen"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_mail_services_last_seen", table_name="mail_services")
    op.drop_index("ix_mail_services_category", table_name="mail_services")
    op.drop_table("mail_services")
