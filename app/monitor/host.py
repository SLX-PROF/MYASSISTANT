"""Health of the Atlas server itself: disk, memory, restarts, weekly archive.

Inside Docker, the data volume lives on the host disk and /proc/meminfo shows
the host's memory, so these numbers describe the whole server.

"Server is down" cannot be reported by the server itself. Two layers cover it:
* after a restart Atlas says how long it was unavailable (from a heartbeat
  timestamp written every minute);
* HEARTBEAT_URL (e.g. healthchecks.io) gets a ping every 5 minutes, and that
  outside service messages you in Telegram when the pings stop.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.db.models import utcnow
from app.db.session import Database
from app.monitor.alerts import AlertManager, human_duration
from app.monitor.checks import ALARM, OK, WARN, CheckResult
from app.monitor.messenger import Messenger
from app.services import backup, kv

log = logging.getLogger(__name__)

ALIVE_KEY = "atlas_alive_at"
DOWN_NOTICE_AFTER = timedelta(minutes=5)
TELEGRAM_FILE_LIMIT = 45 * 1024 * 1024  # bots may send up to 50 MB


def meminfo(path: str = "/proc/meminfo") -> tuple[int, int] | None:
    """(total_kb, available_kb) or None where /proc is not available."""
    try:
        values = {}
        for line in Path(path).read_text().splitlines():
            k, _, rest = line.partition(":")
            values[k] = int(rest.split()[0])
        return values["MemTotal"], values["MemAvailable"]
    except (OSError, KeyError, ValueError, IndexError):
        return None


def disk_result(total: int, free: int, min_pct: int) -> CheckResult:
    pct = free * 100 / total if total else 100
    gb = free / 1024**3
    detail = f"Свободно {gb:.1f} ГБ ({pct:.0f}%)."
    if pct < min_pct / 2:
        return CheckResult("atlas_disk", "На сервере Атласа кончается место", ALARM, detail + " Почистите: docker image prune -f; docker builder prune -f")
    if pct < min_pct:
        return CheckResult("atlas_disk", "На сервере Атласа мало места", WARN, detail + " Почистите: docker image prune -f; docker builder prune -f")
    return CheckResult("atlas_disk", "Диск сервера Атласа", OK, detail)


def memory_result(total_kb: int, avail_kb: int, min_pct: int) -> CheckResult:
    pct = avail_kb * 100 / total_kb if total_kb else 100
    detail = f"Свободно {avail_kb // 1024} МБ из {total_kb // 1024} МБ ({pct:.0f}%)."
    if pct < min_pct:
        return CheckResult("atlas_memory", "На сервере Атласа мало памяти", WARN, detail)
    return CheckResult("atlas_memory", "Память сервера Атласа", OK, detail)


class HostMonitor:
    def __init__(self, db: Database, settings: Settings, messenger: Messenger, alerts: AlertManager, telegram=None, clock=utcnow):
        self.db = db
        self.settings = settings
        self.messenger = messenger
        self.alerts = alerts
        self.telegram = telegram
        self.clock = clock

    async def check(self) -> list[CheckResult]:
        results = []
        try:
            du = shutil.disk_usage(self.settings.data_dir)
            results.append(disk_result(du.total, du.free, self.settings.disk_min_free_percent))
        except OSError as e:
            log.warning("disk usage failed: %s", e)
        mem = meminfo()
        if mem:
            results.append(memory_result(*mem, self.settings.memory_min_free_percent))
        await self.alerts.process(results)
        return results

    async def stamp(self) -> None:
        async with self.db.session() as s:
            await kv.put(s, ALIVE_KEY, self.clock().isoformat())
            await s.commit()

    async def startup_notice(self) -> str | None:
        """After a restart: tell how long Atlas was unavailable (only if noticeably long)."""
        now = self.clock()
        async with self.db.session() as s:
            last = await kv.get(s, ALIVE_KEY)
        await self.stamp()
        if not last:
            return None
        gap = now - datetime.fromisoformat(last)
        if gap < DOWN_NOTICE_AFTER:
            return None
        since = datetime.fromisoformat(last).astimezone(self.settings.tz)
        text = (
            f"Атлас снова работает. Был недоступен примерно {human_duration(gap)} "
            f"(с {since:%d.%m %H:%M}). Напоминания за это время придут как просроченные."
        )
        await self.messenger.send(text, title="Сервер")
        return text

    async def weekly_archive(self) -> Path | None:
        """Archive the database and photos; optionally send it to the owner's Telegram."""
        keep = max(1, self.settings.backup_weekly_keep)
        try:
            path = await asyncio.to_thread(backup.archive, self.settings.data_dir, keep)
        except Exception:  # noqa: BLE001
            log.exception("weekly archive failed")
            await self.messenger.send("Предупреждение: не удалось сделать еженедельный архив Атласа. См. логи.", title="Бэкап")
            return None
        log.info("weekly archive written: %s (%s bytes)", path.name, path.stat().st_size)
        if not (self.settings.backup_to_telegram and self.telegram and self.settings.telegram_chat_ids):
            return path
        send, note = path, ""
        if path.stat().st_size > TELEGRAM_FILE_LIMIT:
            send = await asyncio.to_thread(backup.archive, self.settings.data_dir, keep, False)
            note = " Фото в архив не влезли (лимит Telegram 50 МБ) — полный архив лежит на сервере."
        size_mb = send.stat().st_size / 1024 / 1024
        caption = f"Еженедельная копия Атласа ({size_mb:.1f} МБ): база и фото.{note} Храните этот чат закрытым."
        data = await asyncio.to_thread(send.read_bytes)
        for chat in self.settings.telegram_chat_ids:
            try:
                await self.telegram.send_document(chat, send.name, data, caption)
            except Exception as e:  # noqa: BLE001
                log.warning("backup to telegram failed: %s", e)
        if send is not path:
            send.unlink(missing_ok=True)
        return path
