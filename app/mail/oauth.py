"""Sign-in with Microsoft for Outlook.com / Hotmail mailboxes (IMAP over OAuth).

Microsoft no longer accepts passwords (even app passwords) for IMAP on personal
accounts, so Atlas uses the device-code flow: it shows a short code, you enter
it at microsoft.com/devicelogin and allow read access to mail. Atlas then keeps
a refresh token and gets fresh access tokens by itself.

The refresh token is stored in DATA_DIR/secrets (file mode 600), outside the
database, so it is not part of backups sent to Telegram.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.mail.imap import Account, MailError

log = logging.getLogger(__name__)

AUTHORITY = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
SCOPE = "offline_access https://outlook.office.com/IMAP.AccessAsUser.All"
MS_DOMAINS = {"outlook.com", "hotmail.com", "live.com", "msn.com", "outlook.ru", "hotmail.ru"}


def needs_oauth(account: Account) -> bool:
    return account.password.lower() == "oauth" or account.address.split("@", 1)[1] in MS_DOMAINS


@dataclass
class Pending:
    user_code: str
    verification_uri: str
    expires_at: float
    task: asyncio.Task | None = None
    error: str = ""


@dataclass
class MicrosoftAuth:
    client_id: str
    secrets_dir: Path
    client: httpx.AsyncClient | None = None
    pending: dict[str, Pending] = field(default_factory=dict)
    _cache: dict[str, tuple[str, float]] = field(default_factory=dict)  # address -> (access token, expires at)

    # ----------------------------------------------------------- storage

    def _path(self, address: str) -> Path:
        return self.secrets_dir / f"oauth-{address.replace('@', '_at_')}.json"

    def _load(self, address: str) -> dict | None:
        try:
            return json.loads(self._path(address).read_text())
        except (OSError, ValueError):
            return None

    def _save(self, address: str, refresh_token: str) -> None:
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(address)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"refresh_token": refresh_token}, f)
        tmp.replace(path)

    def connected(self, address: str) -> bool:
        return bool((self._load(address) or {}).get("refresh_token"))

    def forget(self, address: str) -> None:
        self._path(address).unlink(missing_ok=True)
        self._cache.pop(address, None)

    # ------------------------------------------------------------- http

    async def _post(self, url: str, data: dict) -> dict:
        own = self.client is None
        client = self.client or httpx.AsyncClient(timeout=20)
        try:
            r = await client.post(url, data=data)
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise MailError("Нет связи с сервером входа Microsoft.") from e
        finally:
            if own:
                await client.aclose()

    def _require_client(self) -> None:
        if not self.client_id:
            raise MailError("Для Outlook нужен OUTLOOK_CLIENT_ID в .env (регистрация приложения Microsoft, см. инструкцию).")

    # ------------------------------------------------------ device flow

    async def start(self, address: str) -> Pending:
        """Begin sign-in: returns the code to enter at the Microsoft page."""
        self._require_client()
        old = self.pending.get(address)
        if old and old.expires_at > time.time() and old.task and not old.task.done():
            return old
        res = await self._post(f"{AUTHORITY}/devicecode", {"client_id": self.client_id, "scope": SCOPE})
        if "device_code" not in res:
            raise MailError(f"Microsoft отказал во входе: {res.get('error_description', res.get('error', 'неизвестная ошибка'))[:200]}")
        p = Pending(res["user_code"], res.get("verification_uri", "https://microsoft.com/devicelogin"), time.time() + int(res.get("expires_in", 900)))
        p.task = asyncio.create_task(self._wait(address, res["device_code"], int(res.get("interval", 5)), p), name=f"ms-login-{address}")
        self.pending[address] = p
        return p

    async def _wait(self, address: str, device_code: str, interval: int, p: Pending) -> None:
        while time.time() < p.expires_at:
            await asyncio.sleep(interval)
            res = await self._post(
                f"{AUTHORITY}/token",
                {"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": self.client_id, "device_code": device_code},
            )
            err = res.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            if err or "refresh_token" not in res:
                p.error = {"authorization_declined": "Вход отклонён.", "expired_token": "Код устарел, начните заново."}.get(
                    err, f"Ошибка входа: {res.get('error_description', err)}"[:200]
                )
                return
            self._save(address, res["refresh_token"])
            if "access_token" in res:
                self._cache[address] = (res["access_token"], time.time() + int(res.get("expires_in", 3600)) - 120)
            self.pending.pop(address, None)
            log.info("outlook sign-in completed for one mailbox")
            return
        p.error = "Код устарел, начните заново."

    def state(self, address: str) -> dict:
        p = self.pending.get(address)
        out = {"connected": self.connected(address), "pending": None, "error": ""}
        if p:
            if p.error:
                out["error"] = p.error
            elif p.expires_at > time.time():
                out["pending"] = {"user_code": p.user_code, "verification_uri": p.verification_uri}
        return out

    # ------------------------------------------------------ access token

    async def token(self, address: str) -> str:
        self._require_client()
        cached = self._cache.get(address)
        if cached and cached[1] > time.time():
            return cached[0]
        saved = self._load(address)
        if not saved or not saved.get("refresh_token"):
            raise MailError(f"{address}: нужно войти через Microsoft (раздел «Почта» → «Войти в Outlook»).")
        res = await self._post(
            f"{AUTHORITY}/token",
            {"grant_type": "refresh_token", "client_id": self.client_id, "refresh_token": saved["refresh_token"], "scope": SCOPE},
        )
        if "access_token" not in res:
            if res.get("error") == "invalid_grant":
                self.forget(address)
                raise MailError(f"{address}: вход в Outlook истёк или отозван — войдите заново.")
            raise MailError(f"{address}: Microsoft не выдал доступ ({res.get('error', 'ошибка')}).")
        if res.get("refresh_token"):
            self._save(address, res["refresh_token"])  # Microsoft rotates refresh tokens
        self._cache[address] = (res["access_token"], time.time() + int(res.get("expires_in", 3600)) - 120)
        return res["access_token"]


async def token_for(auth: MicrosoftAuth | None, account: Account) -> str | None:
    """Access token for an OAuth mailbox, None for password mailboxes."""
    if not needs_oauth(account):
        return None
    if auth is None:
        raise MailError(f"{account.address}: Outlook требует входа через Microsoft, а он не настроен (OUTLOOK_CLIENT_ID).")
    return await auth.token(account.address)
