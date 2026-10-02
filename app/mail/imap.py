"""Read-only IMAP access.

Mailboxes are opened with SELECT ... READONLY (EXAMINE) and messages are read
with BODY.PEEK, so nothing is marked as read, moved or deleted. Atlas has no
code path that writes to a mailbox.

imaplib is blocking: everything here runs in a worker thread.
"""

from __future__ import annotations

import imaplib
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

HEADER_FIELDS = "FROM SUBJECT DATE LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST"
BATCH = 400

# Known providers: domain -> IMAP host. Everything else: MAIL_IMAP_HOSTS or imap.<domain>.
HOSTS = {
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "yandex.ru": "imap.yandex.ru",
    "ya.ru": "imap.yandex.ru",
    "yandex.com": "imap.yandex.ru",
    "yandex.by": "imap.yandex.ru",
    "yandex.kz": "imap.yandex.ru",
    "narod.ru": "imap.yandex.ru",
    "mail.ru": "imap.mail.ru",
    "bk.ru": "imap.mail.ru",
    "inbox.ru": "imap.mail.ru",
    "list.ru": "imap.mail.ru",
    "internet.ru": "imap.mail.ru",
    "rambler.ru": "imap.rambler.ru",
    "icloud.com": "imap.mail.me.com",
    "me.com": "imap.mail.me.com",
    "zoho.com": "imap.zoho.com",
    "zohomail.com": "imap.zoho.com",
    "zoho.eu": "imap.zoho.eu",
    "zohomail.eu": "imap.zoho.eu",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com",
}

_LIST = re.compile(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>"(?:[^"\\]|\\.)*"|\S+)')
_UID = re.compile(rb"UID (\d+)")


class MailError(Exception):
    """Safe to show to the user (Russian)."""


@dataclass(frozen=True)
class Account:
    address: str
    password: str
    host: str

    @property
    def label(self) -> str:
        return self.address


def parse_accounts(raw: str, hosts_override: str = "") -> list[Account]:
    """'me@gmail.com:apppass, me@yandex.ru:apppass' -> accounts. Spaces in app passwords are dropped."""
    overrides = {}
    for part in hosts_override.split(","):
        if "=" in part:
            d, h = part.split("=", 1)
            overrides[d.strip().lower()] = h.strip()
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        address, sep, password = part.partition(":")
        address = address.strip().lower()
        domain = address.split("@", 1)[1] if "@" in address else ""
        if not sep and domain in ("outlook.com", "hotmail.com", "live.com", "msn.com"):
            sep, password = ":", "oauth"  # Outlook signs in through Microsoft, no password
        if not sep or "@" not in address or not password.strip():
            raise MailError(f"MAIL_ACCOUNTS: ожидается адрес:пароль_приложения, а не «{address or part[:20]}»")
        domain = address.split("@", 1)[1]
        host = overrides.get(domain) or HOSTS.get(domain) or f"imap.{domain}"
        out.append(Account(address, password.replace(" ", "").strip(), host))
    return out


def _quote(name: bytes) -> str:
    s = name.decode("utf-8", "replace")
    return s if s.startswith('"') else f'"{s}"'


@dataclass
class Folder:
    name: str  # as the server lists it (quoted, modified UTF-7)
    flags: set[str]


class Mailbox:
    """One IMAP connection. Use as a context manager inside a thread."""

    def __init__(self, account: Account, timeout: int = 60, factory=imaplib.IMAP4_SSL, token: str | None = None):
        self.token = token  # OAuth access token (Outlook); None = password login
        self.account = account
        self.timeout = timeout
        self.factory = factory
        self.conn = None

    def __enter__(self) -> Mailbox:
        try:
            self.conn = self.factory(self.account.host, 993, timeout=self.timeout)
            if self.token:
                auth = f"user={self.account.address}\x01auth=Bearer {self.token}\x01\x01".encode()
                self.conn.authenticate("XOAUTH2", lambda _: auth)
            else:
                self.conn.login(self.account.address, self.account.password)
        except imaplib.IMAP4.error as e:
            if self.token:
                raise MailError(f"{self.account.address}: Outlook не принял вход — войдите через Microsoft заново.") from e
            raise MailError(
                f"{self.account.address}: почта не пустила. Нужен пароль приложения и включённый IMAP в настройках ящика."
            ) from e
        except OSError as e:
            raise MailError(f"{self.account.address}: нет связи с {self.account.host} ({e.__class__.__name__}).") from e
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self.conn is not None:
                self.conn.logout()
        except Exception:  # noqa: BLE001
            pass

    def folders(self) -> list[Folder]:
        typ, data = self.conn.list()
        out = []
        for line in data or []:
            if not isinstance(line, bytes):
                continue
            m = _LIST.search(line)
            if m:
                out.append(Folder(_quote(m.group("name")), {f.lower() for f in m.group("flags").decode().split()}))
        return out

    def history_folders(self) -> list[str]:
        """Where the whole history is: Gmail's «All Mail», otherwise INBOX plus archive folders."""
        fs = self.folders()
        every = [f.name for f in fs if "\\all" in f.flags]
        if every:
            return every[:1]
        names = ['"INBOX"'] + [f.name for f in fs if "\\archive" in f.flags]
        return list(dict.fromkeys(names))

    def select(self, folder: str) -> int:
        """Open read-only; returns UIDVALIDITY."""
        typ, _ = self.conn.select(folder, readonly=True)
        if typ != "OK":
            raise MailError(f"{self.account.address}: не открылась папка {folder}.")
        typ, data = self.conn.status(folder, "(UIDVALIDITY)")
        m = re.search(rb"UIDVALIDITY (\d+)", data[0] if data and data[0] else b"")
        return int(m.group(1)) if m else 0

    def uids(self, criteria: str = "ALL") -> list[int]:
        typ, data = self.conn.uid("SEARCH", None, criteria)
        if typ != "OK" or not data or not data[0]:
            return []
        return [int(x) for x in data[0].split()]

    def fetch(self, uids: list[int], what: str) -> list[tuple[int, bytes]]:
        """[(uid, raw bytes)] for one FETCH item (a header or a partial body)."""
        out = []
        for i in range(0, len(uids), BATCH):
            chunk = ",".join(str(u) for u in uids[i : i + BATCH])
            typ, data = self.conn.uid("FETCH", chunk, f"({what})")
            if typ != "OK":
                continue
            for part in data or []:
                if isinstance(part, tuple) and len(part) == 2:
                    m = _UID.search(part[0])
                    if m:
                        out.append((int(m.group(1)), part[1]))
        return out

    def headers(self, uids: list[int]) -> list[tuple[int, bytes]]:
        return self.fetch(uids, f"BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})]")

    def partial(self, uids: list[int], size: int = 40000) -> list[tuple[int, bytes]]:
        """Headers and the beginning of each message (enough for a snippet)."""
        return self.fetch(uids, f"BODY.PEEK[]<0.{size}>")
