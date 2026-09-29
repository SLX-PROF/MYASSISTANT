import json

from tests.conftest import PASSWORD


async def test_health_and_security_headers(client):
    r = await client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


async def test_protected_endpoints_require_login(client):
    assert (await client.get("/api/conversations")).status_code == 401
    assert (await client.get("/api/tasks")).status_code == 401
    me = (await client.get("/api/auth/me")).json()
    assert me["authenticated"] is False


async def test_login_sets_httponly_cookie(client):
    r = await client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert (await client.get("/api/auth/me")).json()["authenticated"] is True


async def test_wrong_password_and_rate_limit(client):
    for _ in range(5):
        r = await client.post("/api/auth/login", json={"password": "nope"})
        assert r.status_code == 401
    r = await client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 429  # locked even with the right password
    assert "retry-after" in r.headers


async def test_csrf_required_for_writes(client):
    r = await client.post("/api/auth/login", json={"password": PASSWORD})
    csrf = r.json()["csrf_token"]
    assert (await client.post("/api/tasks", json={"title": "x"})).status_code == 403
    ok = await client.post("/api/tasks", json={"title": "x"}, headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 200


async def test_cross_origin_write_rejected(authed):
    r = await authed.post("/api/tasks", json={"title": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = await authed.post("/api/tasks", json={"title": "x"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200


async def test_logout_invalidates_session(authed):
    assert (await authed.post("/api/auth/logout")).status_code == 200
    assert (await authed.get("/api/conversations")).status_code == 401


async def _chat(client, cid, text):
    events = []
    async with client.stream("POST", f"/api/conversations/{cid}/messages", json={"text": text}) as r:
        assert r.status_code == 200
        async for line in r.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


async def test_chat_creates_task_visible_on_tasks_page(authed):
    """Acceptance: 'добавь задачу купить продукты на завтра' -> visible in Tasks."""
    cid = (await authed.post("/api/conversations", json={})).json()["id"]
    events = await _chat(authed, cid, "Добавь задачу купить продукты на завтра")
    assert events[-1]["type"] == "done"
    tasks = (await authed.get("/api/tasks")).json()
    assert [t["title"] for t in tasks] == ["купить продукты"]
    assert tasks[0]["due_at"] is not None and tasks[0]["due_has_time"] is False

    page = (await authed.get(f"/api/conversations/{cid}/messages")).json()
    roles = [m["role"] for m in page["messages"]]
    assert roles[0] == "user" and "tool" in roles
    convs = (await authed.get("/api/conversations")).json()
    assert convs[0]["title"].startswith("Добавь задачу")


async def test_chat_reminder_flow_and_notification(authed, app):
    cid = (await authed.post("/api/conversations", json={})).json()["id"]
    await _chat(authed, cid, "напомни через 2 минуты выпить воды")
    rems = (await authed.get("/api/reminders")).json()
    assert len(rems) == 1 and rems[0]["text"] == "выпить воды"

    # Fast-forward: fire as if 2 minutes passed.
    from datetime import datetime, timedelta, timezone

    fired = await app.state.scheduler.fire_due(now=datetime.now(timezone.utc) + timedelta(minutes=3))
    assert fired == 1
    unread = (await authed.get("/api/notifications?unread_only=true")).json()
    assert len(unread) == 1 and unread[0]["body"] == "выпить воды"
    convs = (await authed.get("/api/conversations")).json()
    assert convs[0]["unread"] == 1
    await authed.post("/api/notifications/read", json={"conversation_id": cid})
    assert (await authed.get("/api/notifications?unread_only=true")).json() == []


async def test_manual_crud(authed):
    r = await authed.post("/api/reminders", json={"text": "Звонок", "when": "2099-01-01T10:00:00+03:00"})
    assert r.status_code == 200
    rid = r.json()["id"]
    assert (await authed.post(f"/api/reminders/{rid}/cancel")).json()["status"] == "cancelled"
    bad = await authed.post("/api/reminders", json={"text": "x", "when": "вчера"})
    assert bad.status_code == 400

    t = (await authed.post("/api/tasks", json={"title": "Отчёт", "due": "2099-01-01"})).json()
    assert (await authed.patch(f"/api/tasks/{t['id']}", json={"done": True})).json()["status"] == "done"
    assert (await authed.get("/api/tasks?status=done")).json()[0]["id"] == t["id"]
    assert (await authed.delete(f"/api/tasks/{t['id']}")).status_code == 200

    f = (await authed.post("/api/facts", json={"text": "Живёт в Москве"})).json()
    assert (await authed.delete(f"/api/facts/{f['id']}")).status_code == 200
    assert (await authed.get("/api/facts")).json() == []

    ui = (await authed.put("/api/settings/ui", json={"theme": "light", "accent": "#a855f7"})).json()
    assert (await authed.get("/api/settings/ui")).json() == ui
