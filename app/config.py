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
        out = set()
        for part in self.telegram_allowed_chat_ids.split(","):
            part = part.strip()
            if part.lstrip("-").isdigit():
                out.add(int(part))
        return out

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Allow tests to build settings explicitly.
def make_settings(**overrides) -> Settings:
    return Settings(**overrides)


__all__ = ["Settings", "get_settings", "make_settings"]
