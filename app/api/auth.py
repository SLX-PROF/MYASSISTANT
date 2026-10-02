"""Login / logout / current session."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.security import (
    SESSION_COOKIE,
    client_ip,
    create_session,
    destroy_session,
    load_session,
    require_session,
    set_session_cookie,
    verify_password,
    is_public,
    verify_telegram_init_data,
    verify_totp,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)
    code: str | None = Field(default=None, max_length=10)


def _app_info(request: Request, scope: str = "all") -> dict:
    st = request.app.state
    if scope == "content":
        return {"assistant_name": st.settings.assistant_name, "timezone": st.settings.timezone, "scope": "content"}
    return {
        "scope": "all",
        "assistant_name": st.settings.assistant_name,
        "timezone": st.settings.timezone,
        "llm_provider": st.agent.provider.name,
        "llm_warning": st.llm_warning,
    }


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    settings = request.app.state.settings
    limiter = request.app.state.login_limiter
    ip = client_ip(request)
    wait = limiter.retry_after(ip)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Слишком много попыток. Попробуйте через {max(1, wait // 60)} мин.",
            headers={"Retry-After": str(wait)},
        )
    public = is_public(request, settings)
    if not settings.password_hash.get_secret_value() and not public:
        raise HTTPException(status_code=503, detail="Пароль не настроен: задайте PASSWORD_HASH в .env")
    # Through the public address only the content password works.
    if public or not verify_password(body.password, settings.password_hash.get_secret_value()):
        if verify_password(body.password, settings.content_password_hash.get_secret_value()):
            limiter.reset(ip)
            token, sess = await create_session(request, settings, scope="content")
            set_session_cookie(response, token, settings)
            log.info("content-only login from %s", ip)
            return {"csrf_token": sess.csrf_token, **_app_info(request, "content")}
        limiter.record_failure(ip)
        log.warning("failed login from %s", ip)
        raise HTTPException(status_code=401, detail="Неверный пароль")
    totp = settings.totp_secret.get_secret_value()
    if totp and not verify_totp(totp, body.code or ""):
        limiter.record_failure(ip)
        log.warning("failed second factor from %s", ip)
        raise HTTPException(status_code=401, detail="Неверный код из приложения" if body.code else "Введите код из приложения")
    limiter.reset(ip)
    token, sess = await create_session(request, settings)
    set_session_cookie(response, token, settings)
    log.info("login ok from %s", ip)
    return {"csrf_token": sess.csrf_token, **_app_info(request)}


class TelegramLoginIn(BaseModel):
    init_data: str = Field(min_length=1, max_length=8192)


@router.post("/telegram")
async def telegram_login(body: TelegramLoginIn, request: Request, response: Response):
    """Sign-in inside the Telegram Mini App: Telegram vouches for the user's account.

    Only chat ids from TELEGRAM_ALLOWED_CHAT_IDS are accepted.
    """
    settings = request.app.state.settings
    if not (settings.miniapp_url or settings.content_miniapp_url):
        raise HTTPException(status_code=404, detail="Вход через Telegram не настроен")
    limiter = request.app.state.login_limiter
    ip = client_ip(request)
    wait = limiter.retry_after(ip)
    if wait:
        raise HTTPException(status_code=429, detail="Слишком много попыток. Попробуйте позже.", headers={"Retry-After": str(wait)})
    try:
        user = verify_telegram_init_data(body.init_data, settings.telegram_bot_token.get_secret_value())
    except ValueError as e:
        limiter.record_failure(ip)
        log.warning("telegram login rejected (%s) from %s", e, ip)
        raise HTTPException(status_code=401, detail="Откройте Атлас заново из бота.") from None
    if user["id"] in settings.telegram_chat_ids and not is_public(request, settings):
        scope = "all"
    elif user["id"] in settings.content_chat_ids or user["id"] in settings.telegram_chat_ids:
        scope = "content"
    else:
        limiter.record_failure(ip)
        log.warning("telegram login: user not allowed")
        raise HTTPException(status_code=403, detail="Нет доступа.")
    limiter.reset(ip)
    token, sess = await create_session(request, settings, scope=scope)
    set_session_cookie(response, token, settings)
    log.info("telegram login ok (%s)", scope)
    return {"csrf_token": sess.csrf_token, **_app_info(request, scope)}


@router.post("/logout")
async def logout(request: Request, response: Response, _=Depends(require_session)):
    await destroy_session(request)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(request: Request):
    sess = await load_session(request)
    public = is_public(request, request.app.state.settings)
    if sess is None:
        return {
            "authenticated": False,
            "assistant_name": request.app.state.settings.assistant_name,
            # The second factor belongs to the owner's login, never asked on the public address.
            "totp_required": bool(request.app.state.settings.totp_secret.get_secret_value()) and not public,
        }
    return {"authenticated": True, "csrf_token": sess.csrf_token, **_app_info(request, "content" if public else sess.scope)}
