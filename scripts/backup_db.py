"""Consistent online backup of the SQLite database (safe while the app runs).

Jarvis also does this automatically every night at 04:15.

Usage (Docker):
    docker compose exec jarvis python scripts/backup_db.py
    docker compose cp jarvis:/data/backups ./backups
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.backup import backup_database  # noqa: E402


def main() -> int:
    data_dir = Path(os.environ.get("DATA_DIR", "./data"))
    try:
        dst = backup_database(data_dir, keep=int(os.environ.get("BACKUP_KEEP", "14")))
    except FileNotFoundError as e:
        print(f"База не найдена: {e}", file=sys.stderr)
        return 1
    print(f"Готово: {dst} ({dst.stat().st_size // 1024} КБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
