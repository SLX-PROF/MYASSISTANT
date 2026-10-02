"""FastAPI application: wiring, lifespan, static frontend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.api import auth, chat, content, finance, items, mail, notes, notifications
from app.briefing import morning_brief, weather
from app.channels.base import CompositeNotifier
from app.channels.telegram import TelegramAPI, TelegramBot, TelegramNotifier
from app.channels.web import WebNotifier
from app.config import Settings, get_settings
from app.content import service as content_service
from app.content.tools import content_tools
from app.finance import service as finance_service
from app.finance.tools import finance_tools
from app.core.agent import Agent
from app.core.llm import create_provider
from app.core.llm.base import LLMError, LLMProvider
from app.core.llm.fake import FakeProvider
from app.db.models import utcnow
from app.db.session import Database, migrate
from app.events import EventBus
from app.monitor.alerts import AlertManager
from app.monitor.commands import CommandHandler
from app.monitor.explain import Explainer
from app.monitor.host import HostMonitor
from app.monitor.messenger import Messenger
from app.monitor.regular import RegularTasks
from app.monitor.service import MonitorService
from app.monitor.site import SiteClient
from app.monitor.tools import monitor_tools
from app.notes import service as notes_service
from app.notes.tools import notes_tools
from app.scheduler import ReminderScheduler
from app.security import LoginRateLimiter, SecurityMiddleware
from app.tools.builtin import build_registry

log = logging.getLogger("atlas")

WEB_DIR = Path(__file__).parent / "web"


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # The HTTP client logs full request URLs/bodies at DEBUG; keep it quiet.
    for noisy in ("httpx", "httpcore", "anthropic", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def create_app(settings: Settings | None = None, provider: LLMProvider | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        await migrate(settings.database_url)
        db = Database(settings.database_url)
        bus = EventBus()
        telegram = (
            TelegramAPI(settings.telegram_bot_token.get_secret_value(), settings.telegram_api_base)
            if settings.telegram_enabled
            else None
        )
        leads_telegram = (
            TelegramAPI(settings.leads_telegram_bot_token.get_secret_value(), settings.telegram_api_base)
            if settings.leads_bot_enabled
            else None
        )
        notifiers = [WebNotifier(bus)]
        if telegram:
            notifiers.append(TelegramNotifier(telegram, settings.telegram_chat_ids))
        scheduler = ReminderScheduler(db, settings, CompositeNotifier(notifiers))

        llm_warning = None
        llm = provider
        if llm is None:
            try:
                llm = create_provider(settings)
            except LLMError as e:
                log.error("%s Starting in demo mode.", e.user_message)
                llm_warning = e.user_message
                llm = FakeProvider(tz=settings.tz)

        # Site duty, regular tasks, Telegram
        messenger = Messenger(settings, db, bus, telegram, leads_telegram)
        explainer = Explainer(db, settings, llm) if not isinstance(llm, FakeProvider) else None
        alerts = AlertManager(db, messenger, explainer)
        regular = RegularTasks(db, settings)
        site = SiteClient(settings) if settings.monitoring_enabled else None
        monitor = MonitorService(db, settings, messenger, alerts, regular, site)
        extra_tools = monitor_tools(monitor, regular) if settings.monitoring_enabled else []
        extra_tools += content_tools() + finance_tools() + notes_tools()
        host = HostMonitor(db, settings, messenger, alerts, telegram)
        st_mail = None
        if settings.mail_enabled:
            from app.mail.digest import MailDigest
            from app.mail.imap import MailError, parse_accounts
            from app.mail.inventory import MailInventory
            from app.mail.llm import MailLLM

            try:
                mail_accounts = parse_accounts(settings.mail_accounts.get_secret_value(), settings.mail_imap_hosts)
                mail_provider = llm
                if settings.mail_llm_model and not isinstance(llm, FakeProvider):
                    mail_provider = create_provider(settings.model_copy(update={"llm_model": settings.mail_llm_model}))
                mail_llm = None if isinstance(mail_provider, FakeProvider) else MailLLM(db, settings, mail_provider)
                st_mail = {"inventory": MailInventory(db, settings, mail_llm), "digest": MailDigest(db, settings, mail_llm)}
                log.info("mail: %s mailbox(es)", len(mail_accounts))
            except (MailError, LLMError) as e:
                log.error("mail disabled: %s", e)

        st = app.state
        st.settings = settings
        st.db = db
        st.bus = bus
        st.scheduler = scheduler
        st.monitor = monitor
        st.agent = Agent(db, settings, llm, build_registry(extra_tools), scheduler)
        st.agent.after_usage = monitor.budget_watch
        st.llm_warning = llm_warning
        st.login_limiter = LoginRateLimiter(settings.login_max_attempts, settings.login_window_minutes * 60)
        st.active_runs = set()
        st.background_tasks = set()

        await scheduler.start()
        if settings.monitoring_enabled:
            created, skipped = await regular.seed()
            if created:
                log.info("regular tasks created: %s", ", ".join(created))
            if skipped:
                log.info("regular tasks skipped (not configured): %s", ", ".join(skipped))
        monitor.register_jobs(scheduler.add_job)
        async with db.session() as s:
            await finance_service.ensure_defaults(s)
            await s.commit()

        def local_today():
            return utcnow().astimezone(settings.tz).date()

        async def content_nudge() -> None:
            async with db.session() as s:
                text = await content_service.today_message(s, local_today())
            if text:
                await messenger.send_content(text)

        async def content_evening() -> None:
            async with db.session() as s:
                text = await content_service.evening_message(s, local_today())
            if text:
                await messenger.send_content(text)

        async def content_publish_soon() -> None:
            async with db.session() as s:
                texts = await content_service.publish_soon(s, utcnow().astimezone(settings.tz), settings.content_publish_remind_minutes)
                await s.commit()
            for text in texts:
                await messenger.send_content(text)

        async def finance_weekly() -> None:
            async with db.session() as s:
                text = await finance_service.weekly_message(s, local_today())
            if text:
                await messenger.send(text, title="Финансы за неделю")

        async def brief() -> None:
            text = await morning_brief(db, settings, utcnow(), await weather(settings), leads_on=site is not None)
            await messenger.send(text, title="Утро")

        async def mail_digest() -> None:
            text = await st_mail["digest"].build(utcnow())
            if text:
                await messenger.send(text, title="Почта")

        async def payment_reminders() -> None:
            async with db.session() as s:
                texts = await finance_service.due_reminders(s, local_today())
                await s.commit()
            for text in texts:
                await messenger.send(text, title="Платёж")

        for job, at, job_id in (
            (content_nudge, settings.content_reminder_time, "content-nudge"),
            (payment_reminders, settings.finance_reminder_time, "payment-reminders"),
            (content_evening, settings.content_evening_time, "content-evening"),
        ):
            if at:
                hh, mm = (int(x) for x in at.split(":"))
                scheduler.add_job(job, CronTrigger(hour=hh, minute=mm, timezone=settings.tz), job_id)
        if settings.morning_brief_time:
            hh, mm = (int(x) for x in settings.morning_brief_time.split(":"))
            scheduler.add_job(brief, CronTrigger(hour=hh, minute=mm, timezone=settings.tz), "morning-brief")
        if st_mail and settings.mail_digest_time:
            hh, mm = (int(x) for x in settings.mail_digest_time.split(":"))
            scheduler.add_job(mail_digest, CronTrigger(hour=hh, minute=mm, timezone=settings.tz), "mail-digest")
        if settings.finance_weekly_time:
            hh, mm = (int(x) for x in settings.finance_weekly_time.split(":"))
            scheduler.add_job(finance_weekly, CronTrigger(day_of_week=0, hour=hh, minute=mm, timezone=settings.tz), "finance-weekly")
        hh, mm = (int(x) for x in settings.backup_weekly_time.split(":"))
        scheduler.add_job(
            host.weekly_archive,
            CronTrigger(day_of_week=settings.backup_weekly_weekday, hour=hh, minute=mm, timezone=settings.tz),
            "weekly-archive",
        )
        scheduler.add_job(host.check, IntervalTrigger(seconds=600), "host-check")
        scheduler.add_job(host.stamp, IntervalTrigger(seconds=60), "alive-stamp")
        await host.startup_notice()
        if settings.content_publish_remind_minutes:
            scheduler.add_job(content_publish_soon, IntervalTrigger(seconds=60), "content-publish-soon")
        bot = None
        if telegram:
            transcriber = None
            if settings.voice_enabled:
                from app.services.voice import Transcriber

                transcriber = Transcriber(settings.voice_model, settings.data_dir / "models", settings.voice_threads)

            async def save_forward(text: str, url: str) -> str:
                async with db.session() as s:
                    n = await notes_service.add_note(s, text=text, url=url, source="forward")
                    if n.url and not n.title:
                        n.title = await notes_service.fetch_title(n.url)
                    await s.commit()
                    return f"Сохранил в заметки: {notes_service.short(n)}\nНайти потом: «что я сохранял про …»"

            bot = TelegramBot(
                telegram, settings, db, CommandHandler(db, monitor, regular, st.agent, mail=st_mail), transcriber=transcriber, note_saver=save_forward
            )
            bot.start()
        log.info(
            "Atlas started (llm=%s, model=%s, tz=%s, telegram=%s, monitoring=%s, leads bot=%s, mini app=%s)",
            llm.name,
            llm.model,
            settings.timezone,
            "on" if telegram else "off",
            "on" if site else "off",
            "on" if leads_telegram else "off",
            "on" if settings.miniapp_url else "off",
        )
        st.host = host
        st.mail = st_mail
        try:
            yield
        finally:
            if bot:
                await bot.stop()
            await scheduler.shutdown()
            if site:
                await site.aclose()
            if telegram:
                await telegram.aclose()
            if leads_telegram:
                await leads_telegram.aclose()
            await llm.aclose()
            await db.dispose()

    app = FastAPI(title="Atlas", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityMiddleware, settings=settings)

    for r in (auth.router, chat.router, items.router, notifications.router, content.router, finance.router, notes.router, mail.router):
        app.include_router(r)

    @app.api_route("/api/health", methods=["GET", "HEAD"])
    async def health():
        return {"status": "ok"}

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def api_not_found(path: str):
        raise HTTPException(404, "Не найдено")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def frontend(path: str, request: Request):
        """Serve the built SPA; unknown paths fall back to index.html."""
        if not WEB_DIR.exists():
            return JSONResponse(
                {"detail": "Фронтенд не собран. Выполните: cd frontend && npm ci && npm run build"},
                status_code=503,
            )
        root = WEB_DIR.resolve()
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            headers = {}
            if path.startswith("assets/"):
                headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif path in ("sw.js", "manifest.webmanifest"):
                headers["Cache-Control"] = "no-cache"
            return FileResponse(candidate, headers=headers)
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def run() -> None:
    import uvicorn

    settings = get_settings()
    configure_logging(settings)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
