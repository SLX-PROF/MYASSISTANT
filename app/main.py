"""FastAPI application: wiring, lifespan, static frontend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from app.api import auth, chat, items, notifications
from app.channels.base import CompositeNotifier
from app.channels.telegram import TelegramAPI, TelegramBot, TelegramNotifier
from app.channels.web import WebNotifier
from app.config import Settings, get_settings
from app.core.agent import Agent
from app.core.llm import create_provider
from app.core.llm.base import LLMError, LLMProvider
from app.core.llm.fake import FakeProvider
from app.db.session import Database, migrate
from app.events import EventBus
from app.monitor.alerts import AlertManager
from app.monitor.commands import CommandHandler
from app.monitor.explain import Explainer
from app.monitor.messenger import Messenger
from app.monitor.regular import RegularTasks
from app.monitor.service import MonitorService
from app.monitor.site import SiteClient
from app.monitor.tools import monitor_tools
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
        messenger = Messenger(settings, db, bus, telegram)
        explainer = Explainer(db, settings, llm) if not isinstance(llm, FakeProvider) else None
        alerts = AlertManager(db, messenger, explainer)
        regular = RegularTasks(db, settings)
        site = SiteClient(settings) if settings.monitoring_enabled else None
        monitor = MonitorService(db, settings, messenger, alerts, regular, site)
        extra_tools = monitor_tools(monitor, regular) if settings.monitoring_enabled else []

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
        bot = None
        if telegram:
            bot = TelegramBot(telegram, settings, db, CommandHandler(db, monitor, regular, st.agent))
            bot.start()
        log.info(
            "Atlas started (llm=%s, model=%s, tz=%s, telegram=%s, monitoring=%s)",
            llm.name,
            llm.model,
            settings.timezone,
            "on" if telegram else "off",
            "on" if site else "off",
        )
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
            await llm.aclose()
            await db.dispose()

    app = FastAPI(title="Atlas", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityMiddleware, settings=settings)

    for r in (auth.router, chat.router, items.router, notifications.router):
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
