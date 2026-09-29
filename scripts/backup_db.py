"""Consistent online backup of the SQLite database (safe while the app runs).

Usage (Docker):
    docker compose exec jarvis python scripts/backup_db.py
    docker compose cp jarvis:/data/backups ./backups

Keeps the newest KEEP files in <DATA_DIR>/backups.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

KEEP = int(os.environ.get("BACKUP_KEEP", "14"))


def main() -> int:
    data_dir = Path(os.environ.get("DATA_DIR", "./data"))
    src = data_dir / "jarvis.db"
    if not src.exists():
        print(f"База не найдена: {src}", file=sys.stderr)
        return 1
    out_dir = data_dir / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"jarvis-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
        s.backup(d)
    backups = sorted(out_dir.glob("jarvis-*.db"))
    for old in backups[:-KEEP]:
        old.unlink()
    print(f"Готово: {dst} ({dst.stat().st_size // 1024} КБ), хранится копий: {min(len(backups), KEEP)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
