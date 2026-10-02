"""Application settings loaded from environment variables / `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Network ---------------------------------------------------------
    # Loopback by default. Exposing the app requires an explicit change.
    host: str = "127.0.0.1"
    port: int = 8000
    # IPs of reverse proxies whose X-Forwarded-For is trusted (comma separated, "*" = any).
    forwarded_allow_ips: str = "127.0.0.1"

    # --- Storage ---------------------------------------------------------
    data_dir: Path = Path("./data")

    # --- Auth ------------------------------------------------------------
    # argon2 hash produced by `python scripts/hash_password.py`
    password_hash: SecretStr = SecretStr("")
    session_ttl_hours: int = 24 * 30
    # Set to true when the app is served over HTTPS (behind a reverse proxy).
    cookie_secure: bool = False
    login_max_attempts: int = 5
    login_window_minutes: int = 15
    # Extra origins allowed for state-changing requests (comma separated).
    allowed_origins: str = ""

    # --- LLM -------------------------------------------------------------
    llm_provider: str = "anthropic"  # anthropic | fake | openai_compat
    llm_model: str = "claude-sonnet-5-5"
    # Thinking depth / cost lever: low | medium | high | xhigh | max
    llm_effort: str = "low"
    llm_max_tokens: int = 16000
    anthropic_api_key: SecretStr = SecretStr("")
    # Server-side retry on another model if the main one declines a request.
    llm_refusal_fallback: bool = True
    agent_max_iterations: int = 8
    # How many recent messages of a conversation are sent to the model.
    history_max_messages: int = 80

    # --- Assistant -------------------------------------------------------
    timezone: str = "Europe/Moscow"
    assistant_name: str = "Атлас"
    facts_max_chars: int = 2000

    # --- Scheduler -------------------------------------------------------
    # Reminders older than this at startup are delivered as "overdue".
    reminder_grace_seconds: int = 120
    scheduler_sweep_seconds: int = 30

    # --- Spending --------------------------------------------------------
    # Hard monthly cap for Claude API spend in USD (0 = no cap). When reached,
    # model calls stop; Telegram, checks and reminders keep working.
    llm_monthly_budget_usd: float = 0.0

    # --- Second factor (TOTP) ---------------------------------------------
    # Base32 secret from `python scripts/totp_setup.py`; empty = password only.
    totp_secret: SecretStr = SecretStr("")

    # --- Telegram ---------------------------------------------------------
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_allowed_chat_ids: str = ""  # comma separated numeric chat ids
    telegram_api_base: str = "https://api.telegram.org"
    # Address of the Atlas web UI that the bot opens as a Telegram Mini App,
    # e.g. https://s1824923.tailXXXX.ts.net/ (reachable only with Tailscale on).
    telegram_miniapp_url: str = ""
    # Work bot for the Forbsa site: new leads, site alerts, weekly lead report,
    # regular site chores and the work commands (/status, /leads, /due, ...).
    # The main bot then stays personal. Empty = everything goes through the main bot.
    work_telegram_bot_token: SecretStr = SecretStr("")
    work_telegram_chat_ids: str = ""  # comma separated
    # Older names of the same settings (still accepted).
    leads_telegram_bot_token: SecretStr = SecretStr("")
    leads_telegram_chat_ids: str = ""

    # --- Content calendar and finances -----------------------------------
    # Daily "today in the content plan" message and payment reminders (HH:MM, empty = off).
    content_reminder_time: str = "10:00"
    # Content-plan-only access for another person (e.g. a partner who runs the
    # plan): her Telegram chat ids and/or a separate web password. She sees only
    # the content calendar and gets only its notifications.
    content_telegram_chat_ids: str = ""
    # Public HTTPS address that serves ONLY the content calendar (no Tailscale
    # needed for her), e.g. https://plan.example.dpdns.org. Empty = off.
    public_base_url: str = ""
    content_password_hash: SecretStr = SecretStr("")
    content_publish_remind_minutes: int = 30  # 0 = off
    content_evening_time: str = "20:00"  # "tomorrow is not filmed yet"; empty = off
    finance_reminder_time: str = "10:05"
    # Monday-morning finance digest for the past week (HH:MM, empty = off).
    finance_weekly_time: str = "09:05"

    # --- Morning brief ---------------------------------------------------
    # Daily message: tasks, reminders, payments, leads, budget, weather. Empty = off.
    morning_brief_time: str = "08:30"
    weather_city: str = "Москва"
    weather_lat: float | None = 55.7558  # empty = no weather
    weather_lon: float | None = 37.6173

    # --- Voice messages ---------------------------------------------------
    # Telegram voice messages are transcribed on the server (faster-whisper).
    voice_enabled: bool = True
    # tiny | base | small | medium: bigger is more accurate and slower. small ≈ 0.5 GB.
    voice_model: str = "small"
    voice_threads: int = 0  # 0 = automatic

    # --- Mail (read-only IMAP) ----------------------------------------------
    # "me@gmail.com:app-password, me@yandex.ru:app-password". Empty = mail off.
    mail_accounts: SecretStr = SecretStr("")
    # Hosts for other domains: "example.com=imap.example.com" (known providers are built in).
    mail_imap_hosts: str = ""
    # Daily digest of new mail (HH:MM, empty = off).
    mail_digest_time: str = "08:45"
    # Send the first lines of letters to the model (better summary). False = senders and subjects only.
    mail_snippets: bool = True
    # Senders the model never sees (only counted): banks, government, etc.
    mail_exclude: str = ""
    # Model for mail analysis; empty = LLM_MODEL.
    mail_llm_model: str = ""

    # --- Server health and backups ------------------------------------------
    # Warn when free disk space or memory falls below these values.
    disk_min_free_percent: int = 10
    memory_min_free_percent: int = 8
    # Weekly archive of the database and uploaded photos (day 0 = Monday, HH:MM).
    backup_weekly_weekday: int = 6  # Sunday
    backup_weekly_time: str = "04:30"
    backup_weekly_keep: int = 6
    # Also send the weekly archive to your Telegram (private chat with the bot).
    backup_to_telegram: bool = True

    # --- Site monitoring (empty SITE_BASE_URL = monitoring off) ------------
    site_base_url: str = ""
    site_feed_key: SecretStr = SecretStr("")
    site_status_path: str = ""
    admin_url: str = ""
    # Enable the "stub page / noindex" check once the site is launched.
    site_launched: bool = False
    site_app_dir: str = "/opt/forbsa-site"
    site_ip: str = ""
    # Domain renewal date (MM-DD) for the yearly reminder 30 days before it.
    domain_renewal_date: str = ""
    monitor_llm_max_tokens: int = 1200
    monitor_llm_daily_max: int = 10
    heartbeat_url: str = ""
    weekly_summary_weekday: int = 0  # Monday
    weekly_summary_time: str = "09:00"
    regular_tasks_check_seconds: int = 300

    # --- Logging ---------------------------------------------------------
    log_level: str = "INFO"
    # Log message texts and tool arguments. Off by default for privacy.
    log_verbose: bool = False

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        ZoneInfo(v)  # raises for unknown zones
        return v

    @field_validator("domain_renewal_date")
    @classmethod
    def _valid_mmdd(cls, v: str) -> str:
        v = v.strip()
        if v:
            from datetime import date

            month, day = (int(x) for x in v.split("-"))
            date(2024, month, day)  # validates (leap year allows 02-29)
        return v

    @field_validator("telegram_miniapp_url")
    @classmethod
    def _valid_miniapp_url(cls, v: str) -> str:
        v = v.strip()
        if v and not v.startswith("https://"):
            raise ValueError("TELEGRAM_MINIAPP_URL must start with https:// (Telegram requires HTTPS)")
        return v

    @field_validator(
        "content_reminder_time", "finance_reminder_time", "weekly_summary_time", "content_evening_time",
        "finance_weekly_time", "morning_brief_time", "backup_weekly_time", "mail_digest_time",
    )  # fmt: skip
    @classmethod
    def _valid_hhmm(cls, v: str) -> str:
        v = v.strip()
        if v:
            hh, mm = (int(x) for x in v.split(":"))
            if not (0 <= hh < 24 and 0 <= mm < 60):
                raise ValueError("expected HH:MM")
        return v

    @field_validator("weather_lat", "weather_lon", mode="before")
    @classmethod
    def _empty_coord(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("voice_model")
    @classmethod
    def _valid_voice_model(cls, v: str) -> str:
        allowed = {"tiny", "base", "small", "medium"}
        if v not in allowed:
            raise ValueError(f"voice_model must be one of {sorted(allowed)}")
        return v

    @field_validator("public_base_url")
    @classmethod
    def _valid_public_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if v and not v.startswith("https://"):
            raise ValueError("PUBLIC_BASE_URL must start with https://")
        return v

    @field_validator("llm_effort")
    @classmethod
    def _valid_effort(cls, v: str) -> str:
        allowed = {"low", "medium", "high", "xhigh", "max"}
        if v not in allowed:
            raise ValueError(f"llm_effort must be one of {sorted(allowed)}")
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "atlas.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.database_path}"

    @property
    def telegram_chat_ids(self) -> set[int]:
        return _chat_ids(self.telegram_allowed_chat_ids)

    @property
    def miniapp_url(self) -> str:
        """Mini App needs the bot (it vouches for the user) and an HTTPS address."""
        return self.telegram_miniapp_url if self.telegram_enabled else ""

    @property
    def content_chat_ids(self) -> set[int]:
        return _chat_ids(self.content_telegram_chat_ids) - self.telegram_chat_ids

    @property
    def content_miniapp_url(self) -> str:
        """Where her calendar opens: the public address if set, else the Tailscale one."""
        if not self.telegram_enabled:
            return ""
        if self.public_base_url:
            return f"{self.public_base_url}/content"
        return f"{self.miniapp_url.rstrip('/')}/content" if self.miniapp_url else ""

    @property
    def public_host(self) -> str:
        from urllib.parse import urlsplit

        return urlsplit(self.public_base_url).netloc.lower() if self.public_base_url else ""

    @property
    def mail_enabled(self) -> bool:
        return bool(self.mail_accounts.get_secret_value().strip())

    @property
    def mail_excluded(self) -> set[str]:
        return {d.strip().lower().lstrip("@") for d in self.mail_exclude.split(",") if d.strip()}

    @property
    def work_token(self) -> str:
        return (self.work_telegram_bot_token.get_secret_value() or self.leads_telegram_bot_token.get_secret_value()).strip()

    @property
    def work_chat_ids(self) -> set[int]:
        return _chat_ids(self.work_telegram_chat_ids or self.leads_telegram_chat_ids)

    @property
    def work_bot_enabled(self) -> bool:
        return bool(self.work_token and self.work_chat_ids)

    # older names
    @property
    def leads_chat_ids(self) -> set[int]:
        return self.work_chat_ids

    @property
    def leads_bot_enabled(self) -> bool:
        return self.work_bot_enabled

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token.get_secret_value() and self.telegram_chat_ids)

    @property
    def monitoring_enabled(self) -> bool:
        return bool(self.site_base_url.strip())

    @property
    def site_url(self) -> str:
        return self.site_base_url.strip().rstrip("/")

    @property
    def extra_origins(self) -> set[str]:
        return {o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()}


def _chat_ids(raw: str) -> set[int]:
    out = set()
    for part in raw.split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Allow tests to build settings explicitly.
def make_settings(**overrides) -> Settings:
    return Settings(**overrides)


__all__ = ["Settings", "get_settings", "make_settings"]
