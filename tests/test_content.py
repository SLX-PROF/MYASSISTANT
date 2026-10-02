from datetime import date

import pytest

from app.content import service as cs
from app.content.tools import content_tools
from app.core.agent import Agent
from app.core.llm.fake import FakeProvider, FakeTurn
from app.services.chat import create_conversation
from app.tools.builtin import build_registry

D = date(2026, 10, 14)
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


async def test_items_move_bank_duplicate_delete(db, tmp_path):
    async with db.session() as s:
        a = await cs.add_item(s, D, title="Nails day vlog", rubric="beauty", icon="heart")
        b = await cs.add_item(s, D, title="Мой вечер после работы", stage="script")
        idea = await cs.add_item(s, None, title="Что в моей сумке")
        assert (a.position, b.position, idea.day) == (0, 1, None)
        with pytest.raises(cs.ContentError):
            await cs.add_item(s, D, title="x", rubric="cars")
        with pytest.raises(cs.ContentError, match="ЧЧ:ММ"):
            await cs.update_item(s, a.id, publish_time="25:00")

        await cs.update_item(s, idea.id, move_to=date(2026, 10, 16), stage="filmed")
        await cs.update_item(s, b.id, move_to=None)  # back to the bank
        assert [i.title for i in await cs.list_items(s, None, None)] == ["Мой вечер после работы"]
        assert [i.title for i in await cs.list_items(s, D, date(2026, 10, 20))] == ["Nails day vlog", "Что в моей сумке"]

        await cs.add_link(s, a.id, "https://www.tiktok.com/@someone/video/1", "свет")
        with pytest.raises(cs.ContentError, match="http"):
            await cs.add_link(s, a.id, "javascript:alert(1)")
        photo = await cs.add_photo(s, a.id, JPEG, tmp_path)
        assert photo.file.endswith(".jpg") and (tmp_path / photo.file).exists()
        with pytest.raises(cs.ContentError, match="JPEG"):
            await cs.add_photo(s, a.id, b"<svg></svg>", tmp_path)

        copy = await cs.duplicate_item(s, a.id, date(2026, 10, 20))
        out = (await cs.items_out(s, [copy]))[0]
        assert out["day"] == "2026-10-20" and [r["kind"] for r in out["refs"]] == ["link"]

        await cs.delete_item(s, a.id, tmp_path)
        assert not (tmp_path / photo.file).exists()
        assert cs.media_path(tmp_path, "../jarvis.db") is None

        for i in range(cs.MAX_PER_DAY):
            await cs.add_item(s, date(2026, 11, 1), title=f"#{i}")
        with pytest.raises(cs.ContentError, match="максимум"):
            await cs.add_item(s, date(2026, 11, 1), title="лишняя")
        await s.commit()


async def test_meta_and_today_message(db):
    async with db.session() as s:
        m = await cs.set_meta(s, goal="+5 000 подписчиков", week_of=date(2026, 10, 15), week_theme="Усиливаем офис")
        assert m["week_themes"] == {"2026-10-12": "Усиливаем офис"} and m["title"] == "Контент-план"
        m = await cs.set_meta(s, week_of=date(2026, 10, 12), week_theme="")
        assert m["week_themes"] == {} and m["goal"] == "+5 000 подписчиков"

        assert await cs.today_message(s, D) is None
        await cs.add_item(s, D, title="Утренняя рутина", publish_time="19:00")
        await cs.add_item(s, D, title="Уже вышло", stage="published")
        await cs.add_item(s, date(2026, 10, 15), title="Офисный мем")
        text = await cs.today_message(s, D)
        assert "Утренняя рутина (идея · публикация 19:00)" in text and "Уже вышло" not in text and "Завтра: Офисный мем" in text


async def test_agent_plans_month(db, settings):
    items = [{"date": f"2026-10-{d:02d}", "title": f"Идея {d}", "rubric": "lifestyle"} for d in range(1, 8)]
    provider = FakeProvider(
        script=[
            FakeTurn(tool_calls=[("content_add", {"items": items})]),
            FakeTurn(text="Готово."),
            FakeTurn(tool_calls=[("content_update", {"id": 3, "date": "2026-10-20", "stage": "filmed"})]),
            FakeTurn(text="Перенёс."),
        ]
    )
    agent = Agent(db, settings, provider, build_registry(content_tools()))
    async with db.session() as s:
        cid = (await create_conversation(s)).id
        await s.commit()
    [e async for e in agent.run(cid, "составь план на неделю")]
    events = [e async for e in agent.run(cid, "перенеси третий день на 20-е, он снят")]
    card = next(e["card"] for e in events if e["type"] == "tool_result")
    assert card == {"type": "content", "text": "Идея 3 — 20.10, снято"}


async def test_content_api(authed):
    c = authed
    r = await c.post("/api/content/items", json={"day": "2026-10-14", "title": "Nails day vlog", "rubric": "beauty", "icon": "heart"})
    assert r.status_code == 200
    item = r.json()
    r = await c.post(f"/api/content/items/{item['id']}/photos", content=JPEG, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200 and r.json()["url"].startswith("/api/content/media/")
    assert (await c.get(r.json()["url"])).content == JPEG
    assert (await c.post(f"/api/content/items/{item['id']}/links", json={"url": "https://pin.it/x"})).status_code == 200
    moved = (await c.patch(f"/api/content/items/{item['id']}", json={"day": "2026-10-16", "stage": "filmed"})).json()
    assert moved["day"] == "2026-10-16" and moved["stage"] == "filmed" and len(moved["refs"]) == 2
    banked = (await c.patch(f"/api/content/items/{item['id']}", json={"to_bank": True})).json()
    assert banked["day"] is None
    week = (await c.get("/api/content?bank=true")).json()
    assert [i["title"] for i in week["items"]] == ["Nails day vlog"]
    assert (await c.put("/api/content/meta", json={"goal": "+5 000"})).json()["goal"] == "+5 000"
    assert (await c.get("/api/content?start=2026-01-01&end=2026-12-31")).status_code == 400
    assert (await c.get("/api/content/media/..%2F..%2Fetc%2Fpasswd")).status_code == 404
    assert (await c.delete(f"/api/content/items/{item['id']}")).status_code == 200


async def test_content_requires_login(client):
    assert (await client.get("/api/content?bank=true")).status_code == 401
    assert (await client.get("/api/finance/summary")).status_code == 401


async def test_platforms(db):
    async with db.session() as s:
        it = await cs.add_item(s, D, title="Reels", platforms=["instagram", "tiktok"])
        assert it.platforms == "tiktok,instagram"  # stored in a stable order
        out = (await cs.items_out(s, [it]))[0]
        assert out["platforms"] == ["tiktok", "instagram"]
        await cs.update_item(s, it.id, platforms=[])
        assert it.platforms == ""
        with pytest.raises(cs.ContentError, match="Площадки"):
            await cs.update_item(s, it.id, platforms=["myspace"])
        copy = await cs.duplicate_item(s, (await cs.add_item(s, D, title="x", platforms=["vk"])).id)
        assert copy.platforms == "vk"
