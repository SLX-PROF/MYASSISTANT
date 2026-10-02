"""One-click unsubscribe (RFC 8058).

The link comes from a letter, i.e. from a stranger, so before Atlas makes the
request it checks that the address is HTTPS and resolves only to public
internet addresses (not this server, not Tailscale, not a local network).
Redirects are not followed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)


class UnsubscribeError(Exception):
    """Safe to show (Russian)."""


async def _public_only(host: str) -> None:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as e:
        raise UnsubscribeError("Адрес отписки не найден.") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsubscribeError("Ссылка отписки ведёт во внутреннюю сеть — не открываю.")


async def one_click(url: str, client: httpx.AsyncClient | None = None, check_host: bool = True) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise UnsubscribeError("Для отписки в один клик нужна https-ссылка.")
    if check_host:
        await _public_only(parts.hostname)
    own = client is None
    client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)
    try:
        r = await client.post(url, data={"List-Unsubscribe": "One-Click"})
    except httpx.HTTPError as e:
        raise UnsubscribeError("Сервис отписки не ответил. Попробуйте ссылкой вручную.") from e
    finally:
        if own:
            await client.aclose()
    if r.status_code >= 400:
        raise UnsubscribeError(f"Сервис отписки ответил ошибкой {r.status_code}. Попробуйте ссылкой вручную.")
