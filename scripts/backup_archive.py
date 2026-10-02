"""Make the weekly archive (database + uploaded photos) right now.

Atlas also does this automatically every week (Sunday 04:30 by default) and
sends it to your Telegram.

Usage (Docker):
    docker compose exec atlas python scripts/backup_archive.py
    docker compose cp atlas:/data/backups/weekly ./weekly
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.services.backup import archive  # noqa: E402

if __name__ == "__main__":
    settings = get_settings()
    print(archive(settings.data_dir, settings.backup_weekly_keep))
