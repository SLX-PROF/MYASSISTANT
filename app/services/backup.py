"""Consistent online backup of the SQLite database."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def backup_database(data_dir: Path, keep: int = 14) -> Path:
    src = Path(data_dir) / "atlas.db"
    if not src.exists():
        raise FileNotFoundError(src)
    out_dir = Path(data_dir) / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"atlas-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
        s.backup(d)
    for old in sorted(out_dir.glob("atlas-*.db"))[:-keep]:
        old.unlink()
    return dst
