"""Create the regular-task schedule (docs/REGULAR_TASKS_SPEC.md). Idempotent:
existing tasks keep their progress; only missing ones are added.

Atlas also runs this automatically at startup when SITE_BASE_URL is set.

Usage:
    docker compose exec atlas python scripts/seed_regular_tasks.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db.session import Database, migrate  # noqa: E402
from app.monitor.regular import RegularTasks  # noqa: E402


async def main() -> int:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await migrate(settings.database_url)
    db = Database(settings.database_url)
    try:
        created, skipped = await RegularTasks(db, settings).seed()
    finally:
        await db.dispose()
    print("Создано:", ", ".join(created) or "ничего (всё уже есть)")
    if skipped:
        print("Пропущено:", ", ".join(skipped), "— для domain задайте DOMAIN_RENEWAL_DATE=ММ-ДД в .env")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
