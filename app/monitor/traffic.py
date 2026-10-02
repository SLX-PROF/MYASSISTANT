"""Outgoing traffic of the Atlas server against the hosting plan's monthly limit.

The container cannot see the server's network counters, so a one-line cron
job on the server copies /proc/net/dev into ~/atlas/host/net_dev every few
minutes; that folder is mounted read-only at /host. Counters reset on reboot,
so Atlas adds up the differences between samples itself.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from app.config import Settings
from app.db.models import utcnow
from app.db.session import Database
from app.services import kv

log = logging.getLogger(__name__)

STATE_KEY = "traffic"
SKIP = ("lo", "docker", "br-", "veth", "tailscale", "virbr", "wg")
GB = 1024**3
LEVELS = (100, 95, 80)


def parse_net_dev(text: str) -> dict[str, tuple[int, int]]:
    """{iface: (rx_bytes, tx_bytes)} from /proc/net/dev."""
    out = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) >= 9 and fields[0].isdigit():
            out[name.strip()] = (int(fields[0]), int(fields[8]))
    return out


def pick_interface(counters: dict[str, tuple[int, int]], wanted: str = "") -> str | None:
    if wanted:
        return wanted if wanted in counters else None
    real = {k: v for k, v in counters.items() if not k.startswith(SKIP)}
    return max(real, key=lambda k: sum(real[k]), default=None)


def period_start(today: date, reset_day: int) -> date:
    day = max(1, min(reset_day, 28))
    if today.day >= day:
        return today.replace(day=day)
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return date(y, m, day)


def gb(n: int) -> str:
    v = n / GB
    return f"{v:.1f} ГБ" if v < 100 else f"{v:.0f} ГБ"


class TrafficMonitor:
    def __init__(self, db: Database, settings: Settings, messenger, path: Path = Path("/host/net_dev"), clock=utcnow):
        self.db = db
        self.settings = settings
        self.messenger = messenger
        self.path = path
        self.clock = clock

    @property
    def enabled(self) -> bool:
        return self.settings.traffic_limit_gb > 0

    def _read(self) -> dict[str, tuple[int, int]] | None:
        try:
            return parse_net_dev(self.path.read_text())
        except OSError:
            return None

    async def sample(self) -> dict | None:
        """Add the traffic since the previous sample; send 80/95/100% warnings once per period."""
        counters = self._read()
        now = self.clock()
        today = now.astimezone(self.settings.tz).date()
        start = period_start(today, self.settings.traffic_reset_day).isoformat()
        async with self.db.session() as s:
            st = await kv.get(s, STATE_KEY) or {}
            if counters is None:
                if self.enabled and not st.get("missing_warned"):
                    st["missing_warned"] = True
                    await kv.put(s, STATE_KEY, st)
                    await s.commit()
                    await self.messenger.send(
                        "Не вижу счётчик трафика сервера: добавьте строку в crontab (инструкция в docs/DEPLOY.md, раздел «Трафик»).",
                        title="Сервер",
                    )
                return None
            iface = pick_interface(counters, self.settings.traffic_interface)
            if iface is None:
                return None
            rx, tx = counters[iface]
            if st.get("period") != start:
                st = {"period": start, "iface": iface, "rx": 0, "tx": 0, "warned": [], "last": [rx, tx]}
            last_rx, last_tx = st.get("last") or [rx, tx]
            # After a reboot the counters start from zero again.
            st["rx"] += rx - last_rx if rx >= last_rx else rx
            st["tx"] += tx - last_tx if tx >= last_tx else tx
            st["last"], st["iface"], st["at"] = [rx, tx], iface, now.isoformat()
            st.pop("missing_warned", None)
            to_send = []
            if self.enabled:
                used = self._used(st)
                limit = self.settings.traffic_limit_gb * GB
                for level in LEVELS:
                    if used >= limit * level / 100:
                        if level not in st["warned"]:
                            st["warned"] = sorted(set(st["warned"]) | {lv for lv in LEVELS if lv <= level})
                            to_send.append(
                                f"Трафик сервера: использовано {level}% месячного лимита — {gb(used)} из {self.settings.traffic_limit_gb:g} ГБ "
                                f"(с {date.fromisoformat(start):%d.%m})."
                                + (" Дальше хостинг может ограничить скорость или выставить доплату." if level == 100 else "")
                            )
                        break
            await kv.put(s, STATE_KEY, st)
            await s.commit()
        for text in to_send:
            await self.messenger.send(text, title="Сервер")
        return st

    def _used(self, st: dict) -> int:
        return st["tx"] if self.settings.traffic_direction == "out" else st["tx"] + st["rx"]

    async def report(self) -> str:
        async with self.db.session() as s:
            st = await kv.get(s, STATE_KEY)
        if not st or "period" not in st:
            if self._read() is None:
                return "Счётчик трафика не подключён: нужна строка в crontab сервера (docs/DEPLOY.md, раздел «Трафик»)."
            return "Трафик ещё не посчитан, загляните через 5 минут."
        start = date.fromisoformat(st["period"])
        today = self.clock().astimezone(self.settings.tz).date()
        days = max(1, (today - start).days + 1)
        nxt = date(start.year + (start.month == 12), start.month % 12 + 1, start.day)  # day <= 28, always valid
        total_days = max(days, (nxt - start).days)
        used = self._used(st)
        what = "исходящий" if self.settings.traffic_direction == "out" else "входящий + исходящий"
        lines = [f"Трафик сервера с {start:%d.%m} ({st['iface']}):", f"Исходящий: {gb(st['tx'])}", f"Входящий: {gb(st['rx'])}"]
        if self.enabled:
            limit = self.settings.traffic_limit_gb
            lines.append(f"По лимиту ({what}): {gb(used)} из {limit:g} ГБ — {used / (limit * GB) * 100:.0f}%")
            lines.append(f"Прогноз к концу периода: {gb(int(used / days * total_days))}")
        else:
            lines.append("Лимит не задан (TRAFFIC_LIMIT_GB).")
        return "\n".join(lines)

