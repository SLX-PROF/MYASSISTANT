"""Authentication, sessions, CSRF, login rate limiting and security headers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from collections import deque
from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request
from sqlalchemy import delete, select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import Settings
from app.db.models import AuthSession, utcnow

SESSION_COOKIE = "atlas_session"
# API reachable with a content-only session.
CONTENT_SCOPE_PATHS = ("/api/content", "/api/auth/")
CSRF_HEADER = "x-csrf-token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# ------------------------------------------------------------------- TOTP


def totp_code(secret_b32: str, for_time: float | None = None, step: int = 30, digits: int = 6) -> str:
    """RFC 6238 time-based one-time password (SHA-1), as used by authenticator apps."""
    key = base64.b32decode(secret_b32.replace(" ", "").upper() + "=" * (-len(secret_b32.replace(" ", "")) % 8))
    counter = int((time.time() if for_time is None else for_time) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def verify_totp(secret_b32: str, code: str, for_time: float | None = None, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not secret_b32 or not code.isdigit() or len(code) != 6:
        return False
    now = time.time() if for_time is None else for_time
    return any(hmac.compare_digest(totp_code(secret_b32, now + i * 30), code) for i in range(-window, window + 1))


# --------------------------------------------------------- Telegram Mini App

TELEGRAM_INIT_MAX_AGE = 24 * 3600


def verify_telegram_init_data(init_data: str, bot_token: str, now: float | None = None, max_age: int = TELEGRAM_INIT_MAX_AGE) -> dict:
    """Check the signed launch data Telegram passes to a Mini App; returns the user.

    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        raise ValueError("bad format") from None
    received = data.pop("hash", "")
    if not received or not bot_token:
        raise ValueError("no hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise ValueError("bad signature")
    if (time.time() if now is None else now) - int(data.get("auth_date", "0") or 0) > max_age:
        raise ValueError("expired")
    try:
        user = json.loads(data.get("user", "{}"))
    except ValueError:
        raise ValueError("bad user") from None
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise ValueError("no user")
    return user


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------ rate limit


class LoginRateLimiter:
    """Sliding-window limit on failed logins, per client IP and globally.

    The global limit protects against distributed guessing; it is generous
    enough (x4 per-IP) not to lock out the owner after a couple of typos.
    """

    def __init__(self, max_attempts: int, window_seconds: int, clock=time.monotonic):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self.clock = clock
        self._per_ip: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()

    def _trim(self, q: deque[float], now: float) -> None:
        while q and now - q[0] > self.window:
            q.popleft()

    def retry_after(self, ip: str) -> int:
        """Seconds until another attempt is allowed (0 = allowed now)."""
        now = self.clock()
        q = self._per_ip.get(ip, deque())
        self._trim(q, now)
        self._trim(self._global, now)
        waits = []
        if len(q) >= self.max_attempts:
            waits.append(self.window - (now - q[0]))
        if len(self._global) >= self.max_attempts * 4:
            waits.append(self.window - (now - self._global[0]))
        return int(max(waits)) + 1 if waits else 0

    def record_failure(self, ip: str) -> None:
        now = self.clock()
        self._per_ip.setdefault(ip, deque()).append(now)
        self._global.append(now)
        if len(self._per_ip) > 10_000:  # bound memory
            self._per_ip.clear()

    def reset(self, ip: str) -> None:
        self._per_ip.pop(ip, None)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# -------------------------------------------------------------------- sessions


async def create_session(request: Request, settings: Settings, scope: str = "all") -> tuple[str, AuthSession]:
    db = request.app.state.db
    token = secrets.token_urlsafe(32)
    now = utcnow()
    sess = AuthSession(
        token_hash=_token_hash(token),
        csrf_token=secrets.token_urlsafe(24),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
        user_agent=(request.headers.get("user-agent") or "")[:200],
        scope=scope,
    )
    async with db.session() as s:
        await s.execute(delete(AuthSession).where(AuthSession.expires_at < now))
        s.add(sess)
        await s.commit()
    return token, sess


async def load_session(request: Request) -> AuthSession | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    db = request.app.state.db
    now = utcnow()
    async with db.session() as s:
        sess = await s.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
        if sess is None or sess.expires_at < now:
            return None
        if (now - sess.last_seen_at) > timedelta(minutes=5):
            sess.last_seen_at = now
            await s.commit()
        return sess


async def destroy_session(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return
    async with request.app.state.db.session() as s:
        await s.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
        await s.commit()


def set_session_cookie(response, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


async def require_session(request: Request) -> AuthSession:
    """FastAPI dependency: authenticated session + CSRF check for writes."""
    sess = await load_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="Требуется вход")
    if sess.scope == "content" and not request.url.path.startswith(CONTENT_SCOPE_PATHS):
        raise HTTPException(status_code=403, detail="Доступен только контент-план")
    if request.method in UNSAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent, sess.csrf_token):
            raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    return sess


# ------------------------------------------------------------------ middleware

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "manifest-src 'self'",
        "worker-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


class SecurityMiddleware(BaseHTTPMiddleware):
    """Strict headers on every response; same-origin check for writes."""

    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        if request.method in UNSAFE_METHODS and not self._same_origin(request):
            return JSONResponse({"detail": "Запрос с чужого сайта отклонён"}, status_code=403)
        response = await call_next(request)
        h = response.headers
        h.setdefault("Content-Security-Policy", CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", "camera=(), geolocation=(), payment=(), usb=(), microphone=(self)")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if self.settings.cookie_secure:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
        return response

    def _same_origin(self, request: Request) -> bool:
        origin = request.headers.get("origin") or request.headers.get("referer")
        if not origin:
            # Non-browser clients (curl, tests) send no Origin; CSRF token still applies.
            return True
        parts = urlsplit(origin)
        if f"{parts.scheme}://{parts.netloc}".rstrip("/") in self.settings.extra_origins:
            return True
        host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
        return parts.netloc == host
