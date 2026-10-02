"""Mail: account parsing, header parsing, inventory (collect + classify), digest, unsubscribe guard."""

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

import pytest
from pydantic import SecretStr

from app.core.llm.fake import FakeProvider, FakeTurn
from app.db.models import MailService
from app.mail import parse
from app.mail.digest import MailDigest
from app.mail.imap import MailError, parse_accounts
from app.mail.inventory import MailInventory
from app.mail.llm import MailLLM
from app.mail.unsubscribe import UnsubscribeError, one_click

NOW = datetime(2026, 10, 12, 6, 0, tzinfo=timezone.utc)


def msg(sender, subject, days_ago=0, body="Текст письма", unsub=None, one_click=False) -> bytes:
    m = EmailMessage()
    m["From"] = sender
    m["Subject"] = subject
    m["Date"] = format_datetime(NOW - timedelta(days=days_ago))
    if unsub:
        m["List-Unsubscribe"] = f"<{unsub}>"
        if one_click:
            m["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    m.set_content(body)
    return bytes(m)


class FakeBox:
    """Stands in for app.mail.imap.Mailbox (read-only API only)."""

    letters: dict[int, bytes] = {}
    validity = 7

    def __init__(self, account, **_):
        self.account = account

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def history_folders(self):
        return ['"INBOX"']

    def select(self, folder):
        return self.validity

    def uids(self, criteria="ALL"):
        if criteria.startswith("UID "):
            start = int(criteria.split()[1].split(":")[0])
            return [u for u in self.letters if u >= start] or [max(self.letters)]
        return sorted(self.letters)

    def headers(self, uids):
        return [(u, self.letters[u]) for u in uids]

    def partial(self, uids, size=0):
        return [(u, self.letters[u][:size] if size else self.letters[u]) for u in uids]


@pytest.fixture
def mail_settings(settings):
    return settings.model_copy(update={"mail_accounts": SecretStr("me@gmail.com:abcd efgh ijkl mnop"), "mail_exclude": "sber.ru"})


def test_parse_accounts():
    accs = parse_accounts("me@gmail.com:abcd efgh, You@Yandex.ru:x1, a@corp.io:p", "corp.io=mail.corp.io")
    assert [(a.address, a.password, a.host) for a in accs] == [
        ("me@gmail.com", "abcdefgh", "imap.gmail.com"),
        ("you@yandex.ru", "x1", "imap.yandex.ru"),
        ("a@corp.io", "p", "mail.corp.io"),
    ]
    with pytest.raises(MailError):
        parse_accounts("no-password@gmail.com")


def test_parse_header_and_snippet():
    raw = msg('"Ozon" <news@info.ozon.ru>', "=?utf-8?b?0KHQutC40LTQutC4?=", unsub="https://ozon.ru/u?x=1", one_click=True)
    h = parse.parse_header(1, raw)
    assert (h.domain, h.name, h.subject, h.one_click, h.unsubscribe) == ("ozon.ru", "Ozon", "Скидки", True, "https://ozon.ru/u?x=1")
    assert parse.base_domain("mail.shop.co.uk") == "shop.co.uk"
    p = parse.parse_partial(2, msg("Иван <ivan@gmail.com>", "Договор", body="Привет! Смотри https://x.ru/doc договор"))
    assert p.is_person and p.snippet == "Привет! Смотри [ссылка] договор"
    code = parse.parse_partial(3, msg("bank@sber.ru", "Ваш код для входа: 1234", body="1234"))
    assert code.is_secret and code.snippet == ""


async def test_inventory_collect_and_classify(db, mail_settings):
    FakeBox.letters = {
        1: msg("Netflix <info@mailer.netflix.com>", "Ваш чек за подписку 799 ₽", days_ago=40),
        2: msg("Netflix <info@mailer.netflix.com>", "Новинки недели", days_ago=3, unsub="https://netflix.com/u", one_click=True),
        3: msg("Ozon <news@ozon.ru>", "Скидки", days_ago=1, unsub="mailto:u@ozon.ru"),
        4: msg("Иван <ivan@gmail.com>", "Привет", days_ago=2),
        5: msg("me@gmail.com", "сам себе", days_ago=2),
        6: msg("Сбер <info@sber.ru>", "Выписка", days_ago=5),
    }
    provider = FakeProvider(
        script=[
            FakeTurn(
                tool_calls=[
                    (
                        "report",
                        {"items": [
                            {"id": 1, "name": "Netflix", "category": "paid", "suggest": "cancel", "price": "799 ₽", "note": "чеки"},
                            {"id": 2, "name": "Ozon", "category": "weird", "suggest": "unsubscribe"},
                        ]},
                    )
                ],
                usage={"input_tokens": 1000, "output_tokens": 200},
            )
        ]
    )
    inv = MailInventory(db, mail_settings, MailLLM(db, mail_settings, provider), mailbox_factory=FakeBox)
    p = await inv.collect()
    assert p["state"] == "collected" and p["letters"] == 6 and p["people"] == 1 and p["services"] == 3
    assert p["pending"] == 2 and 0 < p["estimate_usd"] < 0.1  # sber is excluded from the model
    async with db.session() as s:
        rows = {r.domain: r for r in (await s.scalars(__import__("sqlalchemy").select(MailService))).all()}
    assert rows["netflix.com"].count == 2 and rows["netflix.com"].payment and rows["netflix.com"].one_click
    assert rows["sber.ru"].category == "private"
    # ids in the prompt are row ids: map them for the scripted answer
    provider.script[0].tool_calls[0][1]["items"][0]["id"] = rows["netflix.com"].id
    provider.script[0].tool_calls[0][1]["items"][1]["id"] = rows["ozon.ru"].id
    p = await inv.classify()
    assert p["state"] == "done" and p["classified"] == 2
    sent = provider.calls[0]["messages"][0]["content"][0]["text"]
    assert "netflix.com" in sent and "sber.ru" not in sent and "ivan" not in sent and "Текст письма" not in sent
    async with db.session() as s:
        n = await s.get(MailService, rows["netflix.com"].id)
        o = await s.get(MailService, rows["ozon.ru"].id)
    assert (n.category, n.suggest, n.price) == ("paid", "cancel", "799 ₽") and o.category == "other"


async def test_digest(db, mail_settings):
    FakeBox.letters = {
        10: msg("Иван <ivan@gmail.com>", "Договор на подпись", days_ago=0, body="Посмотри договор до пятницы"),
        11: msg("Ozon <news@ozon.ru>", "Скидки до 90%", unsub="https://ozon.ru/u"),
        12: msg("Ozon <news@ozon.ru>", "Ещё скидки", unsub="https://ozon.ru/u"),
        13: msg("Хостинг <billing@host.ru>", "Счёт на оплату 690 ₽", unsub="https://host.ru/u"),
        14: msg("Госуслуги <no-reply@gosuslugi.ru>", "Код подтверждения 5555"),
        15: msg("Сбер <info@sber.ru>", "Выписка за месяц"),
    }
    provider = FakeProvider(
        script=[
            FakeTurn(
                tool_calls=[
                    ("report", {"summary": "Договор от Ивана и счёт за хостинг.", "important": [{"id": 1, "why": "подписать до пятницы"}],
                                "bills": [{"id": 2, "what": "Хостинг", "amount": "690 ₽", "due": "15.10"}]})
                ]
            )
        ]
    )
    d = MailDigest(db, mail_settings, MailLLM(db, mail_settings, provider), mailbox_factory=FakeBox)
    text = await d.build(NOW)
    assert text.startswith("Почта за сутки: 6 писем")
    assert "• Иван: «Договор на подпись» — подписать до пятницы" in text
    assert "• Хостинг — 690 ₽ до 15.10" in text
    assert "Рассылки и реклама: 2 — Ozon 2" in text
    assert "коды и входы: 1" in text and "скрытые отправители: 1" in text
    sent = provider.calls[0]["messages"][0]["content"][0]["text"]
    assert "Посмотри договор" in sent and "5555" not in sent and "Выписка" not in sent and "Скидки" not in sent
    # Next run: only letters after the remembered UID.
    FakeBox.letters[16] = msg("Пётр <petr@mail.ru>", "Встреча", days_ago=0)
    d.llm = None
    text = await d.build(NOW)
    assert text.startswith("Почта за сутки: 1 письмо") and "Пётр: «Встреча»" in text
    assert await d.build(NOW) is None


async def test_unsubscribe_guard():
    with pytest.raises(UnsubscribeError, match="https"):
        await one_click("http://example.com/u")
    with pytest.raises(UnsubscribeError, match="внутреннюю"):
        await one_click("https://127.0.0.1/u")
    with pytest.raises(UnsubscribeError, match="внутреннюю"):
        await one_click("https://localhost/u")


async def test_mail_api_disabled(authed):
    assert (await authed.get("/api/mail/status")).json() == {"enabled": False}
    assert (await authed.post("/api/mail/collect")).status_code == 400


class FakeConn:
    """Answers like a real IMAP server (imaplib return shapes)."""

    def __init__(self, *a, **k):
        self.calls = []

    def login(self, user, pw):
        self.calls.append(("login", user))

    def logout(self):
        pass

    def list(self):
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\All) "/" "[Gmail]/&BBIEQQQ1-"',
            b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
        ]

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"3"]

    def status(self, folder, what):
        return "OK", [b'"INBOX" (UIDVALIDITY 42)']

    def uid(self, cmd, *args):
        self.calls.append(("uid", cmd) + args)
        if cmd == "SEARCH":
            return "OK", [b"5 9"]
        return "OK", [
            (b"1 (UID 5 BODY[HEADER.FIELDS (FROM SUBJECT)] {30}", b"From: a@x.ru\r\nSubject: hi\r\n\r\n"),
            b")",
            (b"2 (UID 9 BODY[HEADER.FIELDS (FROM SUBJECT)] {30}", b"From: b@y.ru\r\nSubject: yo\r\n\r\n"),
            b")",
        ]


def test_mailbox_is_read_only_and_parses_server_answers():
    from app.mail.imap import Mailbox

    conn = FakeConn()
    acc = parse_accounts("me@gmail.com:pw")[0]
    with Mailbox(acc, factory=lambda *a, **k: conn) as mb:
        assert mb.history_folders() == ['"[Gmail]/&BBIEQQQ1-"']
        assert mb.select('"INBOX"') == 42
        assert mb.uids() == [5, 9]
        got = mb.headers([5, 9])
    assert [u for u, _ in got] == [5, 9] and parse.parse_header(5, got[0][1]).address == "a@x.ru"
    assert ("select", '"INBOX"', True) in conn.calls
    fetches = [c for c in conn.calls if c[:2] == ("uid", "FETCH")]
    assert fetches and all("PEEK" in c[3] for c in fetches)
    # nothing that could change the mailbox was ever called
    assert not any(c[1] in ("STORE", "COPY", "MOVE", "EXPUNGE") for c in conn.calls if c[0] == "uid")
