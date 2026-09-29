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
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def _app_info(request: Request) -> dict:
    st = request.app.state
    return {
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
    if not settings.password_hash.get_secret_value():
        raise HTTPException(status_code=503, detail="Пароль не настроен: задайте PASSWORD_HASH в .env")
    if not verify_password(body.password, settings.password_hash.get_secret_value()):
        limiter.record_failure(ip)
        log.warning("failed login from %s", ip)
        raise HTTPException(status_code=401, detail="Неверный пароль")
    limiter.reset(ip)
    token, sess = await create_session(request, settings)
    set_session_cookie(response, token, settings)
    log.info("login ok from %s", ip)
    return {"csrf_token": sess.csrf_token, **_app_info(request)}


@router.post("/logout")
async def logout(request: Request, response: Response, _=Depends(require_session)):
    await destroy_session(request)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(request: Request):
    sess = await load_session(request)
    if sess is None:
        return {"authenticated": False, "assistant_name": request.app.state.settings.assistant_name}
    return {"authenticated": True, "csrf_token": sess.csrf_token, **_app_info(request)}
