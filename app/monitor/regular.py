"""Regular chores for the site (docs/REGULAR_TASKS_SPEC.md).

A task stays open until confirmed with /done. Escalation for an unconfirmed
task (stages are notified once each):
    1 at the due time, 2 after 24 h, 3 after 72 h ("просрочено N дней"),
    4 after 7 days (warning), 5 after 14 days (alarm, high-stakes tasks only).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app.config import Settings
from app.db.models import RegularTask, utcnow
from app.db.session import Database
from app.services import recurrence as rec
from app.services.timeparse import to_local_iso

log = logging.getLogger(__name__)

STAGE_AT = [timedelta(0), timedelta(hours=24), timedelta(hours=72), timedelta(days=7), timedelta(days=14)]
MAX_SNOOZE = timedelta(days=14)
EARLY_DONE_WINDOW = timedelta(days=3)


@dataclass(frozen=True)
class Seed:
    key: str
    title: str
    kind: str  # weekly | monthly | yearly
    time: str  # HH:MM local
    weekday: int | None = None  # weekly
    days: tuple[int, ...] = ()  # monthly
    interval: int = 1  # monthly
    anchor: str | None = None  # YYYY-MM-DD of the first occurrence (monthly interval / yearly)
    mode: str = "reminder"
    high_stakes: bool = False
    runbook: str = ""
    initial_done: str | None = None  # YYYY-MM-DD


RUNBOOKS = {
    "billing": """Консоль Yandex Cloud, раздел Биллинг, платёжный аккаунт, детализация по сервисам.
Сравни расход за неделю с бюджетом (создан 01.10.2026).
Расход заметно выше ожидаемого: уменьши CHAT_GLOBAL_DAILY_LIMIT в .env сайта (сейчас 600 в сутки),
пересоздай контейнер: cd {APP} && docker compose -f Docker-compose.yml up -d --force-recreate web
и напиши разработчику.""",
    "dialogs": """Админка {ADMIN_URL}, раздел «Диалоги с ботом»: просмотри последние диалоги. Ищи выдуманные цифры, цены, обещания сроков.
Ошибки за неделю:
cd {APP} && docker compose -f Docker-compose.yml logs web --since 7d | grep -ci error""",
    "docker": """cd {APP}
docker image prune -f
docker builder prune -f
df -h /
# тома не трогать: не использовать docker volume prune и system prune --volumes""",
    "offsite": """rclone ls gdrive:forbsa-backups | tail -5
# должны быть файлы со свежими датами, бэкап базы создаётся каждый день""",
    "reboot": """dnf needs-restarting -r
journalctl -u dnf-automatic --since "30 days ago" | tail -30
# если «Reboot is required»: сделать бэкап, перезагрузить в тихие часы, затем проверить
reboot
# после ребута:
docker ps --format '{{.Names}} {{.Status}}'
curl -s https://{DOMAIN}/api/health
sshd -T | grep -E "^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin)\"""",
    "restore": """cd {APP}
docker compose -f Docker-compose.yml exec -T db psql -U forbsa -d postgres -c "CREATE DATABASE restore_test;"
gunzip -c backups/$(ls -1t backups | grep "sql.gz" | head -1) | docker compose -f Docker-compose.yml exec -T db psql -U forbsa -d restore_test
docker compose -f Docker-compose.yml exec -T db psql -U forbsa -d restore_test -c "SELECT count(*) FROM products;"
docker compose -f Docker-compose.yml exec -T db psql -U forbsa -d postgres -c "DROP DATABASE restore_test;"
# ожидаемо: в таблице есть записи (8 товаров), ошибок загрузки нет, тестовая база удалена""",
    "audit": """# на своём Mac в папке проекта
npm audit --omit=dev
npm outdated
for f in access recommend ratelimit notifications kp; do node scripts/check-$f.mjs; done
# критичные уязвимости: пришли вывод разработчику, обновим точечно и проверим сборку""",
    "limits": """Сверь с реальным расходом за квартал: бюджет Yandex Cloud и CHAT_GLOBAL_DAILY_LIMIT (расход бота на сайте),
расход Claude API Jarvis и месячный лимит Jarvis (/cost).
Подгони пороги под реальный трафик.""",
    "domain": """Проверь у регистратора: срок оплаты домена {DOMAIN} и записи DNS.
Записи A для {DOMAIN} и www должны указывать на {SITE_IP}.
Домен ведёт директор или бухгалтерия: при необходимости перешли им это напоминание.""",
    "policy": """Открой {SITE}/privacy и сверь с реальностью: обработчики (хостинг, Метрика, YandexGPT), сроки хранения,
реквизиты, адрес для запросов. Изменились данные или обработчики: сначала обнови политику, потом остальное.""",
    "access": """Проверь: пользователи админки и их роли, список chat ID у Jarvis (TELEGRAM_ALLOWED_CHAT_IDS),
ключи доступа к серверам (authorized_keys). Лишнее удали.""",
}

SEEDS = [
    Seed("billing", "Проверить расход Yandex Cloud за неделю", "weekly", "10:00", weekday=0, high_stakes=True, initial_done="2026-10-01"),
    Seed("dialogs", "Просмотреть диалоги бота в админке и ошибки в логах за неделю", "weekly", "10:05", weekday=0),
    Seed("docker", "Очистить Docker на сервере сайта", "monthly", "10:00", days=(1, 15), initial_done="2026-10-01"),
    Seed("offsite", "Проверить копию бэкапов вне сервера", "monthly", "10:10", days=(1,)),
    Seed("reboot", "Проверить лог автообновлений и нужна ли перезагрузка", "monthly", "10:20", days=(1,), initial_done="2026-10-01"),
    Seed("restore", "Тест восстановления бэкапа", "monthly", "10:00", days=(1,), interval=3, anchor="2027-01-01", high_stakes=True),
    Seed("audit", "Проверить зависимости и проверки проекта", "monthly", "10:30", days=(1,), interval=3, anchor="2027-01-01"),
    Seed("limits", "Пересмотреть лимиты и бюджет бота", "monthly", "10:45", days=(1,), interval=3, anchor="2027-01-01"),
    Seed("domain", "Оплата и DNS домена", "yearly", "10:00", mode="external"),
    Seed("policy", "Сверить политику конфиденциальности с реальностью", "yearly", "10:00", anchor="2027-09-01"),
    Seed("access", "Ревизия доступов", "yearly", "10:00", anchor="2027-03-01"),
]


def render_runbook(key: str, settings: Settings) -> str:
    domain = settings.site_url.split("://")[-1] or "forbsa.ru"
    text = RUNBOOKS.get(key, "")
    for k, v in {
        "{APP}": settings.site_app_dir,
        "{ADMIN_URL}": settings.admin_url or "(адрес админки)",
        "{DOMAIN}": domain,
        "{SITE}": settings.site_url or f"https://{domain}",
        "{SITE_IP}": settings.site_ip or "(IP сервера сайта)",
    }.items():
        text = text.replace(k, v)
    return text


def _rule_for(seed: Seed, settings: Settings, now: datetime) -> dict | None:
    tz = settings.tz
    hh, mm = (int(x) for x in seed.time.split(":"))
    if seed.kind == "weekly":
        local = now.astimezone(tz)
        start = local + timedelta(days=(seed.weekday - local.weekday()) % 7)
        first = datetime.combine(start.date(), time(hh, mm)).replace(tzinfo=tz)
        return rec.build_rule("weekly", first, tz, weekdays=[seed.weekday])
    if seed.kind == "monthly":
        anchor = date.fromisoformat(seed.anchor) if seed.anchor else now.astimezone(tz).date().replace(day=1)
        first = datetime.combine(anchor, time(hh, mm)).replace(tzinfo=tz)
        return rec.build_rule("monthly", first, tz, interval=seed.interval, days=list(seed.days) or None)
    if seed.kind == "yearly":
        if seed.key == "domain":
            if not settings.domain_renewal_date:
                return None
            month, day = (int(x) for x in settings.domain_renewal_date.split("-"))
            reminder = date(2024, month, day) - timedelta(days=30)
            anchor = reminder
        else:
            anchor = date.fromisoformat(seed.anchor)
        first = datetime.combine(anchor, time(hh, mm)).replace(tzinfo=tz)
        return rec.build_rule("yearly", first, tz)
    raise ValueError(seed.kind)


class RegularTasks:
    def __init__(self, db: Database, settings: Settings, clock=utcnow):
        self.db = db
        self.settings = settings
        self.clock = clock

    # ------------------------------------------------------------- seeding

    async def seed(self) -> tuple[list[str], list[str]]:
        """Create missing tasks (idempotent). Returns (created, skipped)."""
        now = self.clock()
        created, skipped = [], []
        async with self.db.session() as s:
            for sd in SEEDS:
                row = await s.get(RegularTask, sd.key)
                rule = _rule_for(sd, self.settings, now)
                if row is not None:
                    # Keep progress; refresh the texts (they live in code).
                    row.title = sd.title
                    row.runbook = render_runbook(sd.key, self.settings)
                    continue
                if rule is None:
                    skipped.append(sd.key)
                    continue
                initial = None
                if sd.initial_done:
                    initial = datetime.combine(date.fromisoformat(sd.initial_done), time(10, 0)).replace(
                        tzinfo=self.settings.tz
                    ).astimezone(timezone.utc)
                s.add(
                    RegularTask(
                        key=sd.key,
                        title=sd.title,
                        mode=sd.mode,
                        high_stakes=sd.high_stakes,
                        schedule=rule,
                        runbook=render_runbook(sd.key, self.settings),
                        next_due_at=rec.next_occurrence(rule, self._start_after(sd, now)),
                        last_done_at=initial,
                    )
                )
                created.append(sd.key)
            await s.commit()
        return created, skipped

    def _start_after(self, sd: Seed, now: datetime) -> datetime:
        """First due is never before the seed's anchor date (e.g. restore test on 2027-01-01)."""
        start = now - timedelta(seconds=1)
        if sd.anchor:
            hh, mm = (int(x) for x in sd.time.split(":"))
            anchor = datetime.combine(date.fromisoformat(sd.anchor), time(hh, mm)).replace(tzinfo=self.settings.tz)
            start = max(start, anchor.astimezone(timezone.utc) - timedelta(seconds=1))
        if sd.initial_done:
            # Done that day already (per the spec): the next due comes after it.
            done_day = datetime.combine(date.fromisoformat(sd.initial_done), time(23, 59)).replace(tzinfo=self.settings.tz)
            start = max(start, done_day.astimezone(timezone.utc))
        return start

    # ---------------------------------------------------------- escalation

    async def check(self) -> list[str]:
        """Notification texts that are due now (and mark them as sent)."""
        now = self.clock()
        out: list[str] = []
        async with self.db.session() as s:
            rows = (
                await s.scalars(
                    select(RegularTask).where(RegularTask.enabled.is_(True), RegularTask.next_due_at <= now)
                )
            ).all()
            for t in rows:
                elapsed = now - t.next_due_at
                target = sum(1 for at in STAGE_AT if elapsed >= at)
                if target == 5 and not t.high_stakes:
                    target = 4
                if target > t.stage:
                    out.append(self._text(t, target, elapsed))
                    t.stage = target
            await s.commit()
        return out

    def _text(self, t: RegularTask, stage: int, elapsed: timedelta) -> str:
        days = elapsed.days
        hint = f"Подсказка: /how {t.key}. Готово: /done {t.key}. Отложить: /snooze {t.key} 3d"
        action = f"Передайте ответственному: {t.title}" if t.mode == "external" else t.title
        if stage == 1:
            return f"Регулярная задача: {action}\n{hint}"
        if stage == 2:
            return f"Напоминаю: {action}\n{hint}"
        if stage == 3:
            return f"Просрочено {days} дн.: {action}\n{hint}"
        if stage == 4:
            return f"Предупреждение: задача просрочена {days} дн.: {action}\n{hint}"
        return f"Тревога: важная задача просрочена {days} дн.: {action}\n{hint}"

    # ------------------------------------------------------------ commands

    async def get(self, key: str) -> RegularTask | None:
        async with self.db.session() as s:
            return await s.get(RegularTask, key.strip().lower())

    async def done(self, key: str) -> str:
        now = self.clock()
        async with self.db.session() as s:
            t = await s.get(RegularTask, key.strip().lower())
            if t is None:
                return f"Нет задачи «{key}». Список: /due"
            t.last_done_at = now
            due = t.next_due_at
            if due is None or due - now <= EARLY_DONE_WINDOW:
                base = max(now, t.snoozed_from or due or now, due or now)
                t.next_due_at = rec.next_occurrence(t.schedule, base)
            t.snoozed_from = None
            t.stage = 0
            await s.commit()
            nxt = to_local_iso(t.next_due_at, self.settings.tz) if t.next_due_at else "—"
            return f"Отмечено: {t.title}. Следующий срок: {_fmt(nxt)}."

    async def snooze(self, key: str, delta: timedelta) -> str:
        now = self.clock()
        async with self.db.session() as s:
            t = await s.get(RegularTask, key.strip().lower())
            if t is None:
                return f"Нет задачи «{key}». Список: /due"
            base = t.snoozed_from or t.next_due_at or now
            new_due = now + delta
            if new_due - base > MAX_SNOOZE:
                return "Нельзя отложить больше чем на 14 дней от исходного срока."
            t.snoozed_from = base
            t.next_due_at = new_due
            t.stage = 0
            await s.commit()
            return f"Отложено до {_fmt(to_local_iso(new_due, self.settings.tz))}: {t.title}"

    async def due_report(self, days: int = 14) -> str:
        now = self.clock()
        horizon = now + timedelta(days=days)
        async with self.db.session() as s:
            rows = (
                await s.scalars(
                    select(RegularTask)
                    .where(RegularTask.enabled.is_(True), RegularTask.next_due_at <= horizon)
                    .order_by(RegularTask.next_due_at)
                )
            ).all()
        if not rows:
            return f"На ближайшие {days} дней регулярных задач нет."
        lines = []
        for t in rows:
            when = _fmt(to_local_iso(t.next_due_at, self.settings.tz))
            if t.next_due_at <= now:
                lines.append(f"! {t.key}: {t.title} (просрочено {(now - t.next_due_at).days} дн., срок {when})")
            else:
                lines.append(f"- {t.key}: {t.title} ({when})")
        return "Регулярные задачи:\n" + "\n".join(lines)

    async def lastdone_report(self) -> str:
        async with self.db.session() as s:
            rows = (await s.scalars(select(RegularTask).order_by(RegularTask.key))).all()
        if not rows:
            return "Регулярных задач нет."
        lines = []
        for t in rows:
            last = _fmt(to_local_iso(t.last_done_at, self.settings.tz)) if t.last_done_at else "ещё не выполнялась"
            lines.append(f"- {t.key}: {last}")
        return "Последнее выполнение:\n" + "\n".join(lines)

    async def overdue(self) -> list[RegularTask]:
        now = self.clock()
        async with self.db.session() as s:
            return list(
                (
                    await s.scalars(
                        select(RegularTask).where(RegularTask.enabled.is_(True), RegularTask.next_due_at <= now)
                    )
                ).all()
            )


def _fmt(iso: str | None) -> str:
    """'2026-10-05T10:00+03:00' -> '05.10.2026 10:00'."""
    if not iso:
        return "—"
    d, t = iso[:10], iso[11:16]
    return f"{d[8:10]}.{d[5:7]}.{d[0:4]} {t}"
