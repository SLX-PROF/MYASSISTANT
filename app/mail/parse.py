"""Turning raw headers and message starts into small, safe records."""

from __future__ import annotations

import email
import email.policy
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime

# Mail from these domains is from people, not services: grouped by address and
# never sent to the model during the inventory.
FREE_MAIL = {
    "gmail.com", "googlemail.com", "yandex.ru", "ya.ru", "yandex.com", "yandex.by", "yandex.kz", "yandex.ua", "narod.ru",
    "mail.ru", "bk.ru", "inbox.ru", "list.ru", "internet.ru", "rambler.ru", "icloud.com", "me.com", "mac.com",
    "outlook.com", "hotmail.com", "live.com", "yahoo.com", "proton.me", "protonmail.com", "gmx.com", "gmx.de",
}  # fmt: skip
SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu", "msk", "spb"}

WELCOME = ("добро пожаловать", "welcome", "подтвердите", "подтверждение регистрац", "регистрац", "confirm your", "verify your",
           "activate your", "аккаунт создан", "account created", "your account", "ваш аккаунт", "учётн", "учетн")  # fmt: skip
PAYMENT = ("чек", "оплат", "receipt", "invoice", "payment", "счёт", "счет на", "списан", "продлен", "продлён", "подписк",
           "subscription", "renew", "trial", "пробн", "billing", "заказ", "order")  # fmt: skip
SECRET = ("код", "code", "парол", "password", "одноразов", "otp", "2fa", "вход в", "sign in", "login", "войти")
_TAGS = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)
_URL = re.compile(r"https?://\S+")


def dec(value: str | None) -> str:
    if not value:
        return ""
    try:
        return " ".join(str(make_header(decode_header(value))).split())
    except (ValueError, LookupError, UnicodeError):
        return " ".join(value.split())


def base_domain(domain: str) -> str:
    parts = domain.lower().strip(".").split(".")
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def has_any(text: str, words) -> bool:
    low = text.casefold()
    return any(w in low for w in words)


@dataclass
class Header:
    uid: int
    name: str
    address: str
    domain: str  # base domain of the sender
    subject: str
    date: datetime | None
    unsubscribe: str = ""  # best https link, else mailto
    one_click: bool = False
    snippet: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def is_person(self) -> bool:
        return self.domain in FREE_MAIL

    @property
    def is_secret(self) -> bool:
        return has_any(self.subject, SECRET)


def _unsubscribe(raw: str) -> str:
    links = re.findall(r"<([^>]+)>", raw or "")
    https = [x for x in links if x.lower().startswith("https://")]
    mailto = [x for x in links if x.lower().startswith("mailto:")]
    return (https or mailto or [""])[0][:1000]


def parse_header(uid: int, raw: bytes) -> Header:
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    return _from_message(uid, msg)


def _from_message(uid: int, msg) -> Header:
    addrs = getaddresses([msg.get("From", "")])
    name, address = addrs[0] if addrs else ("", "")
    address = address.lower()
    domain = base_domain(address.split("@", 1)[1]) if "@" in address else ""
    try:
        date = parsedate_to_datetime(msg.get("Date", "")) if msg.get("Date") else None
        if date and date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        date = None
    lu = msg.get("List-Unsubscribe", "")
    return Header(
        uid=uid,
        name=dec(name)[:100],
        address=address[:200],
        domain=domain,
        subject=dec(msg.get("Subject", ""))[:300],
        date=date,
        unsubscribe=_unsubscribe(dec(lu) if lu else ""),
        one_click="one-click" in (msg.get("List-Unsubscribe-Post", "") or "").lower(),
    )


def _text_of(msg) -> str:
    plain, rich = "", ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, ValueError):
            continue
        if ctype == "text/plain" and not plain:
            plain = text
        elif ctype == "text/html" and not rich:
            rich = html.unescape(_TAGS.sub(" ", text))
        if plain:
            break
    return plain or rich


def snippet_of(text: str, size: int = 300) -> str:
    text = _URL.sub("[ссылка]", text)
    text = " ".join(text.split())
    return text[:size]


def parse_partial(uid: int, raw: bytes, snippet_size: int = 300) -> Header:
    """Headers plus the first lines of the text (raw may be cut off mid-message)."""
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    h = _from_message(uid, msg)
    if not h.is_secret:
        try:
            h.snippet = snippet_of(_text_of(msg), snippet_size)
        except Exception:  # noqa: BLE001 - a broken message just has no snippet
            h.snippet = ""
    return h
