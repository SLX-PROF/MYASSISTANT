"""Site on-call duty: wires the checks, polling jobs and reports together."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db.models import Lead, utcnow
from app.db.session import Database
from app.monitor import checks
from app.monitor.alerts import AlertManager
from app.monitor.checks import LEVEL_RU, OK, CheckResult
from app.monitor.messenger import Messenger
from app.monitor.regular import RegularTasks
from app.monitor.site import SiteClient, SiteError
from app.services import backup, kv, usage
from app.services.timeparse import to_local_iso

log = logging.getLogger(__name__)

TYPE_RU = {"dealer": "дилер", "architect": "архитектор", "developer": "застройщик", "individual": "частное лицо", "other": "другое"}
SOURCE_RU = {"form": "форма", "chat": "чат", "dealer-form": "форма дилера", "magnet": "лид-магнит", "other": "другое"}
LEADS_BATCH = 50


class MonitorService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        messenger: Messenger,
        alerts: AlertManager,
        regular: RegularTasks,
        site: SiteClient | None,
        clock=utcnow,
    ):
        self.db = db
        self.settings = settings
        self.messenger = messenger
        self.alerts = alerts
        self.regular = regular
        self.site = site
        self.clock = clock
        self.health_fails = 0
        self.health_error = ""
        self.feed_fails = 0
        self.feed_error = ""
        self.status_fails = 0
        self.status_error = ""
        self.last: dict[str, CheckResult] = {}
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- jobs

    def register_jobs(self, add_job) -> None:
        tz = self.settings.tz
        if self.site is not None:
            add_job(self.poll_minutely, IntervalTrigger(seconds=60), "monitor-minutely")
            add_job(self.poll_status, IntervalTrigger(seconds=300), "monitor-status")
            if self.settings.site_launched:
                add_job(self.check_stub, IntervalTrigger(seconds=3600), "monitor-stub")
        add_job(self.check_regular, IntervalTrigger(seconds=self.settings.regular_tasks_check_seconds), "regular-tasks")
        hh, mm = (int(x) for x in self.settings.weekly_summary_time.split(":"))
        add_job(
            self.weekly_summary,
            CronTrigger(day_of_week=self.settings.weekly_summary_weekday, hour=hh, minute=mm, timezone=tz),
            "weekly-summary",
        )
        if self.settings.heartbeat_url:
            add_job(self.heartbeat, CronTrigger(hour=12, minute=7, timezone=tz), "heartbeat")
        add_job(self.backup, CronTrigger(hour=4, minute=15, timezone=tz), "atlas-backup")

    async def poll_minutely(self) -> None:
        await self.poll_leads()
        await self.poll_health()

    # --------------------------------------------------------------- leads

    async def poll_leads(self) -> int:
        """Fetch new leads, store them, send the unsent ones. Returns count sent."""
        assert self.site is not None
        async with self.db.session() as s:
            last_id = await kv.get(s, "leads_last_id")
        try:
            if last_id is None:
                # First run ever: remember where the feed is now, don't replay history.
                latest, items = await self.site.leads(0)
                start = max([latest or 0] + [i.id for i in items])
                async with self.db.session() as s:
                    await kv.put(s, "leads_last_id", start)
                    await s.commit()
                log.info("leads feed initialised at id %s", start)
            else:
                for _ in range(20):  # at most 1000 leads per poll
                    _, items = await self.site.leads(int(last_id))
                    if not items:
                        break
                    async with self.db.session() as s:
                        for it in items:
                            if await s.get(Lead, it.id) is None:
                                s.add(Lead(id=it.id, created_at=_parse_ts(it.created_at), type=it.type, source=it.source))
                        last_id = max(int(last_id), items[-1].id)
                        await kv.put(s, "leads_last_id", last_id)
                        try:
                            await s.commit()
                        except IntegrityError:
                            await s.rollback()
                    if len(items) < LEADS_BATCH:
                        break
            self.feed_fails, self.feed_error = 0, ""
        except SiteError as e:
            self.feed_fails += 1
            self.feed_error = str(e)
            log.warning("leads feed failed (%s in a row): %s", self.feed_fails, e)
        await self._apply([checks.feed_result(self.feed_fails, self.feed_error)])
        return await self._send_pending_leads()

    async def _send_pending_leads(self) -> int:
        async with self.db.session() as s:
            pending = (await s.scalars(select(Lead).where(Lead.notified.is_(False)).order_by(Lead.id))).all()
        sent = 0
        for lead in pending:
            await self.messenger.send(self.lead_text(lead), title="Новая заявка")
            async with self.db.session() as s:
                row = await s.get(Lead, lead.id)
                row.notified = True
                await s.commit()
            sent += 1
        return sent

    def lead_text(self, lead: Lead) -> str:
        text = f"Новая заявка\n№{lead.id}, тип: {TYPE_RU.get(lead.type, lead.type)}, источник: {SOURCE_RU.get(lead.source, lead.source)}"
        if self.settings.admin_url:
            text += f"\n{self.settings.admin_url}"
        return text

    # -------------------------------------------------------------- health

    async def poll_health(self) -> CheckResult:
        assert self.site is not None
        probe = await self.site.health()
        if probe.ok:
            self.health_fails, self.health_error = 0, ""
        else:
            self.health_fails += 1
            self.health_error = probe.error
        await self._count_health(probe.ok)
        r = checks.health_result(self.health_fails, self.health_error, probe.duration_s)
        await self._apply([r])
        return r

    async def _count_health(self, ok: bool) -> None:
        key = "health:" + self.clock().astimezone(self.settings.tz).date().isoformat()
        async with self.db.session() as s:
            v = await kv.get(s, key, {"total": 0, "ok": 0})
            v = {"total": v["total"] + 1, "ok": v["ok"] + (1 if ok else 0)}
            await kv.put(s, key, v)
            await s.commit()

    async def availability(self, days: int = 7) -> float | None:
        today = self.clock().astimezone(self.settings.tz).date()
        total = good = 0
        async with self.db.session() as s:
            for i in range(days):
                v = await kv.get(s, f"health:{(today - timedelta(days=i)).isoformat()}")
                if v:
                    total += v["total"]
                    good += v["ok"]
        return (good / total * 100) if total else None

    # -------------------------------------------------------------- status

    async def poll_status(self) -> list[CheckResult]:
        assert self.site is not None
        now = self.clock()
        try:
            status = await self.site.status()
            self.status_fails, self.status_error = 0, ""
            async with self.db.session() as s:
                await kv.put(s, "last_status", status)
                since = await kv.get(s, "reboot_needed_since")
                if status.get("rebootNeeded") is True and not since:
                    since = now.isoformat()
                    await kv.put(s, "reboot_needed_since", since)
                elif status.get("rebootNeeded") is False and since:
                    since = None
                    await kv.put(s, "reboot_needed_since", None)
                await s.commit()
            results = checks.evaluate_status(status, now, datetime.fromisoformat(since) if since else None)
        except SiteError as e:
            self.status_fails += 1
            self.status_error = str(e)
            log.warning("status fetch failed (%s in a row): %s", self.status_fails, e)
            results = []
            # Stale status still matters: re-evaluate freshness of the last snapshot.
            async with self.db.session() as s:
                last = await kv.get(s, "last_status")
            if last:
                results = [r for r in checks.evaluate_status(last, now) if r.key == "status_fresh"]
        results.append(checks.status_fetch_result(self.status_fails, self.status_error))
        await self._apply(results)
        return results

    async def check_stub(self) -> CheckResult:
        assert self.site is not None
        try:
            r = checks.stub_result(await self.site.homepage())
        except SiteError as e:
            r = checks.stub_result(None, str(e))
        await self._apply([r])
        return r

    async def _apply(self, results: list[CheckResult]) -> None:
        async with self._lock:
            for r in results:
                self.last[r.key] = r
            await self.alerts.process(results)

    # ---------------------------------------------------- regular tasks

    async def check_regular(self) -> None:
        for text in await self.regular.check():
            await self.messenger.send(text, title="Регулярная задача")

    # ------------------------------------------------------------ reports

    async def status_report(self) -> str:
        if self.site is None:
            return "Мониторинг сайта не настроен (SITE_BASE_URL пуст)."
        lines = [f"Сайт {self.settings.site_url.split('://')[-1]}"]
        if not self.last:
            lines.append("Проверки ещё не выполнялись, подождите минуту.")
        mark = {"ok": "ок", "warn": "!", "alarm": "!!"}
        for r in sorted(self.last.values(), key=lambda x: (-checks.LEVEL_ORDER[x.level], x.key)):
            lines.append(f"[{mark[r.level]}] {r.title}{': ' + r.detail if r.detail else ''}")
        mute = await self.alerts.mute_until()
        if mute and mute > self.clock():
            lines.append(f"Предупреждения заглушены до {to_local_iso(mute, self.settings.tz)[11:16]}")
        return "\n".join(lines)

    def checks_snapshot(self) -> dict:
        return {
            "site": self.settings.site_url,
            "checks": [{"check": r.key, "title": r.title, "level": LEVEL_RU[r.level], "detail": r.detail} for r in self.last.values()],
        }

    async def leads_report(self, n: int = 5) -> str:
        async with self.db.session() as s:
            rows = (await s.scalars(select(Lead).order_by(Lead.id.desc()).limit(n))).all()
        if not rows:
            return "Заявок пока нет."
        tz = self.settings.tz
        out = ["Последние заявки:"]
        for l in rows:
            when = l.created_at.astimezone(tz).strftime("%d.%m %H:%M")
            out.append(f"№{l.id} · {when} · {TYPE_RU.get(l.type, l.type)} · {SOURCE_RU.get(l.source, l.source)}")
        return "\n".join(out)

    async def cost_report(self) -> str:
        async with self.db.session() as s:
            spent = await usage.month_spend_usd(s)
            parts = await usage.month_by_purpose(s)
        budget = self.settings.llm_monthly_budget_usd
        names = {"chat": "чат", "telegram": "Telegram", "monitor": "дежурство"}
        lines = [f"Расход Claude API в этом месяце: ${spent:.2f}" + (f" из ${budget:.2f}" if budget > 0 else " (лимит не задан)")]
        for k, v in sorted(parts.items(), key=lambda x: -x[1]):
            lines.append(f"- {names.get(k, k)}: ${v:.2f}")
        lines.append("Оценка по ценам модели; точные цифры в консоли Anthropic.")
        return "\n".join(lines)

    async def weekly_summary(self) -> str:
        now = self.clock()
        avail = await self.availability(7)
        async with self.db.session() as s:
            leads = await s.scalar(select(func.count()).select_from(Lead).where(Lead.created_at >= now - timedelta(days=7)))
            spent = await usage.month_spend_usd(s)
            last = await kv.get(s, "last_status") or {}
        budget = self.settings.llm_monthly_budget_usd
        overdue = await self.regular.overdue()
        active = await self.alerts.active()
        lines = ["Сводка за неделю"]
        if self.site is not None:
            lines.append(f"Доступность: {avail:.2f}%" if avail is not None else "Доступность: нет данных")
            lines.append(f"Заявок: {leads or 0}")
            reboot = last.get("rebootNeeded")
            if reboot is not None:
                lines.append("Перезагрузка сервера: " + ("нужна" if reboot else "не нужна"))
            if last.get("sshBanned") is not None:
                lines.append(f"Заблокировано fail2ban: {last.get('sshBanned')}")
        lines.append(f"Расход Claude: ${spent:.2f}" + (f" из ${budget:.2f}" if budget > 0 else ""))
        if active:
            lines.append("Открытые проблемы: " + "; ".join(a.title for a in active))
        if overdue:
            lines.append("Просрочено: " + "; ".join(f"{t.title} ({(now - t.next_due_at).days} дн.)" for t in overdue))
        else:
            lines.append("Просроченных регулярных задач нет.")
        text = "\n".join(lines)
        await self.messenger.send(text, title="Сводка за неделю")
        return text

    # ---------------------------------------------------------- operations

    async def heartbeat(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(self.settings.heartbeat_url)
            return r.status_code < 400
        except httpx.HTTPError as e:
            log.warning("heartbeat failed: %s", e.__class__.__name__)
            return False

    async def backup(self) -> None:
        try:
            path = await asyncio.to_thread(backup.backup_database, self.settings.data_dir)
            log.info("database backup written: %s", path.name)
        except Exception:  # noqa: BLE001
            log.exception("database backup failed")
            await self.messenger.send("Предупреждение: не удалось сделать резервную копию базы Атласа. См. логи.")

    async def budget_watch(self) -> None:
        """After each model call: one message at 70% and one at 100% per month."""
        budget = self.settings.llm_monthly_budget_usd
        if budget <= 0:
            return
        month = self.clock().astimezone(timezone.utc).strftime("%Y-%m")
        async with self.db.session() as s:
            spent = await usage.month_spend_usd(s)
            for pct, text in (
                (100, f"Тревога: месячный лимит Claude исчерпан (${spent:.2f} из ${budget:.2f}). Модель отключена до следующего месяца; заявки и тревоги продолжают приходить."),
                (70, f"Предупреждение: израсходовано {spent / budget * 100:.0f}% месячного лимита Claude (${spent:.2f} из ${budget:.2f})."),
            ):
                key = f"budget_notified:{month}:{pct}"
                if spent >= budget * pct / 100:
                    if not await kv.get(s, key):
                        await kv.put(s, key, True)
                        await s.commit()
                        await self.messenger.send(text, title="Расход Claude")
                    break


def _parse_ts(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return utcnow()


__all__ = ["MonitorService", "OK"]
