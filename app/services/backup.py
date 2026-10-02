"""Backups: a consistent online copy of the SQLite database, and a weekly
archive of the database together with uploaded photos."""

from __future__ import annotations

import sqlite3
import tarfile
import tempfile
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


def archive(data_dir: Path, keep: int = 6, with_media: bool = True) -> Path:
    """data/backups/weekly/atlas-YYYYMMDD-HHMMSS.tar.gz with atlas.db and media/.

    Restore: stop Atlas, unpack into the data volume (atlas.db and media/), start.
    Without media the name ends in -db.tar.gz and is not counted for rotation.
    """
    data_dir = Path(data_dir)
    src = data_dir / "atlas.db"
    if not src.exists():
        raise FileNotFoundError(src)
    out_dir = data_dir / "backups" / "weekly"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    tail = ".tar.gz" if with_media else "-db.tar.gz"
    dst, n = out_dir / f"atlas-{stamp}{tail}", 1
    while dst.exists():  # two archives within one second
        n += 1
        dst = out_dir / f"atlas-{stamp}-{n}{tail}"
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        copy = Path(tmp) / "atlas.db"
        with sqlite3.connect(src) as s, sqlite3.connect(copy) as d:
            s.backup(d)
        with tarfile.open(dst, "w:gz") as tar:
            tar.add(copy, arcname="atlas.db")
            media = data_dir / "media"
            if with_media and media.is_dir():
                tar.add(media, arcname="media")
    if with_media:
        for old in sorted(p for p in out_dir.glob("atlas-*.tar.gz") if not p.name.endswith("-db.tar.gz"))[:-keep]:
            old.unlink()
    return dst
