"""Distinguishable default category colors (for the pie chart)

Only categories that still have their original default color are changed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-02 21:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# name: (old default, new)
CHANGES = {
    "Продукты": ("#fbbf24", "#c98500"),
    "Кафе и рестораны": ("#fb7185", "#d95926"),
    "Транспорт": ("#22d3ee", "#3987e5"),
    "Дом и связь": ("#60a5fa", "#9085e9"),
    "Подписки и сервисы": ("#a78bfa", "#d55181"),
    "Здоровье": ("#34d399", "#199e70"),
    "Красота и одежда": ("#f472b6", "#e66767"),
    "Развлечения": ("#f97316", "#008300"),
    "Подарки": ("#e879f9", "#199e70"),
    "Зарплата": ("#5eead4", "#3987e5"),
    "Другие доходы": ("#86efac", "#199e70"),
}


def _apply(direction: int) -> None:
    t = sa.table("fin_categories", sa.column("name", sa.String), sa.column("color", sa.String))
    for name, pair in CHANGES.items():
        old, new = pair[::direction]
        op.execute(t.update().where(t.c.name == name, t.c.color == old).values(color=new))


def upgrade() -> None:
    _apply(1)


def downgrade() -> None:
    _apply(-1)
