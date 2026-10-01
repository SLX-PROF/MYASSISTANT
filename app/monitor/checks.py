"""Threshold checks for the site's server status (pure functions, no I/O).

Thresholds follow docs/MONITORING_SPEC.md ("Что проверяется..."). They are
starting values for a small server (1.3 GB RAM, 10 GB disk); tune them here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

OK, WARN, ALARM = "ok", "warn", "alarm"
LEVEL_ORDER = {OK: 0, WARN: 1, ALARM: 2}
LEVEL_RU = {OK: "норма", WARN: "предупреждение", ALARM: "тревога"}


@dataclass(frozen=True)
class CheckResult:
    key: str
    title: str
    level: str
    detail: str = ""


# key: (title, field, warn_at, alarm_at, direction) ; direction "up" = higher is worse
THRESHOLDS = {
    "disk": ("Диск", "diskPercent", 80, 90, "up", "%"),
    "memory": ("Память", "memAvailableMb", 120, 60, "down", " МБ свободно"),
    "swap": ("Swap", "swapUsedMb", 1500, 1900, "up", " МБ"),
    "errors": ("Ошибки в логах за час", "errorsLastHour", 20, 100, "up", ""),
}
BACKUPS = {
    "backup_db": ("Бэкап базы", "dbBackupAgeHours"),
    "backup_media": ("Бэкап медиа", "mediaBackupAgeHours"),
    "backup_offsite": ("Копия бэкапов вне сервера", "offsiteBackupAgeHours"),
}
BACKUP_WARN_H, BACKUP_ALARM_H = 30, 50
CERT_WARN_DAYS, CERT_ALARM_DAYS = 21, 7
STATUS_WARN_MIN, STATUS_ALARM_MIN = 15, 30
REBOOT_WARN_DAYS = 14
HEALTH_SLOW_S = 2.0
HEALTH_FAILS_ALARM = 3
FEED_FAILS_ALARM = 3


def _num(status: dict, field: str) -> float | None:
    v = status.get(field)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _threshold(key: str, status: dict) -> CheckResult | None:
    title, field, warn, alarm, direction, unit = THRESHOLDS[key]
    v = _num(status, field)
    if v is None:
        return None
    if direction == "up":
        level = ALARM if v >= alarm else WARN if v >= warn else OK
    else:
        level = ALARM if v < alarm else WARN if v < warn else OK
    return CheckResult(key, title, level, f"{v:g}{unit}")


def evaluate_status(
    status: dict, now: datetime, reboot_needed_since: datetime | None = None
) -> list[CheckResult]:
    """Evaluate one status.json snapshot. Missing optional fields are skipped."""
    out: list[CheckResult] = []

    generated = _num(status, "generatedAt")
    if generated is not None:
        age_min = (now - datetime.fromtimestamp(generated, timezone.utc)).total_seconds() / 60
        level = ALARM if age_min > STATUS_ALARM_MIN else WARN if age_min > STATUS_WARN_MIN else OK
        out.append(CheckResult("status_fresh", "Свежесть статуса", level, f"{max(age_min, 0):.0f} мин назад"))

    for key in THRESHOLDS:
        r = _threshold(key, status)
        if r:
            out.append(r)

    for key, (title, field) in BACKUPS.items():
        v = _num(status, field)
        if v is None:
            continue
        if v < 0:
            out.append(CheckResult(key, title, ALARM, "файлов не найдено"))
        else:
            level = ALARM if v > BACKUP_ALARM_H else WARN if v > BACKUP_WARN_H else OK
            out.append(CheckResult(key, title, level, f"{v:g} ч назад"))

    days = _num(status, "certDaysLeft")
    if days is not None:
        if days < 0:
            out.append(CheckResult("cert", "Сертификат", ALARM, "не найден"))
        else:
            level = ALARM if days < CERT_ALARM_DAYS else WARN if days < CERT_WARN_DAYS else OK
            out.append(CheckResult("cert", "Сертификат", level, f"осталось {days:g} дн."))

    web, db = _num(status, "webRunning"), _num(status, "dbRunning")
    if web is not None or db is not None:
        down = [n for n, v in (("web", web), ("db", db)) if not v]
        out.append(
            CheckResult(
                "containers",
                "Контейнеры",
                ALARM if down else OK,
                ("остановлен: " + ", ".join(down)) if down else "web и db работают",
            )
        )

    if status.get("rebootNeeded") is True:
        since = reboot_needed_since or now
        days_waiting = (now - since).days
        level = WARN if now - since > timedelta(days=REBOOT_WARN_DAYS) else OK
        out.append(CheckResult("reboot", "Нужна перезагрузка", level, f"ждёт {days_waiting} дн."))
    elif "rebootNeeded" in status:
        out.append(CheckResult("reboot", "Нужна перезагрузка", OK, "не нужна"))

    return out


def health_result(consecutive_failures: int, last_error: str, last_duration_s: float | None) -> CheckResult:
    if consecutive_failures >= HEALTH_FAILS_ALARM:
        return CheckResult("health", "Сайт не отвечает", ALARM, f"{consecutive_failures} неудачи подряд ({last_error})")
    if consecutive_failures == 0 and last_duration_s is not None and last_duration_s > HEALTH_SLOW_S:
        return CheckResult("health", "Сайт отвечает медленно", WARN, f"{last_duration_s:.1f} с")
    detail = f"{last_duration_s:.2f} с" if last_duration_s is not None else ""
    if consecutive_failures:
        detail = f"неудач подряд: {consecutive_failures}"
    return CheckResult("health", "Доступность сайта", OK, detail)


def feed_result(consecutive_failures: int, last_error: str) -> CheckResult:
    if consecutive_failures >= FEED_FAILS_ALARM:
        return CheckResult("leads_feed", "Ошибка опроса заявок", ALARM, f"{consecutive_failures} раза подряд ({last_error})")
    return CheckResult("leads_feed", "Опрос заявок", OK, "")


def status_fetch_result(consecutive_failures: int, last_error: str) -> CheckResult:
    if consecutive_failures >= 3:
        return CheckResult("status_fetch", "Статус сервера недоступен", WARN, f"{consecutive_failures} раза подряд ({last_error})")
    return CheckResult("status_fetch", "Получение статуса", OK, "")


STUB_MARKERS = ("noindex", "сайт в разработке")


def stub_result(html: str | None, error: str = "") -> CheckResult:
    if html is None:
        return CheckResult("stub", "Проверка заглушки", OK, f"пропущено: {error}" if error else "")
    low = html.lower()
    found = [m for m in STUB_MARKERS if m in low]
    if found:
        return CheckResult("stub", "На сайте включена заглушка", ALARM, "найдено: " + ", ".join(found))
    return CheckResult("stub", "Заглушка", OK, "нет")
