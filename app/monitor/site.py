"""HTTP client for the monitored site. Only fixed, configured URLs are used;
the model never builds URLs."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app.config import Settings

LEAD_TYPES = {"dealer", "architect", "developer", "individual", "other"}
LEAD_SOURCES = {"form", "chat", "dealer-form", "magnet"}


class SiteError(Exception):
    pass


@dataclass
class LeadItem:
    id: int
    created_at: str
    type: str
    source: str


@dataclass
class HealthProbe:
    ok: bool
    duration_s: float
    error: str = ""


class SiteClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.base = settings.site_url
        self.key = settings.site_feed_key.get_secret_value()
        self.status_path = settings.site_status_path.strip()
        self.client = client or httpx.AsyncClient(timeout=15.0, follow_redirects=False)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def leads(self, after: int) -> tuple[int | None, list[LeadItem]]:
        """Fetch leads with id > after. Returns (latestId, items).

        Only id / createdAt / type / source are kept, whatever else the
        endpoint might send: personal data must never reach Jarvis.
        """
        try:
            r = await self.client.get(
                f"{self.base}/api/internal/leads-feed", params={"after": after}, headers={"x-feed-key": self.key}
            )
        except httpx.HTTPError as e:
            raise SiteError(f"нет связи: {e.__class__.__name__}") from e
        if r.status_code != 200:
            raise SiteError(f"HTTP {r.status_code}")
        try:
            data = r.json()
            raw = data.get("items") or []
            latest = data.get("latestId")
        except (ValueError, AttributeError) as e:
            raise SiteError("неверный JSON") from e
        items = []
        for it in raw:
            try:
                lid = int(it["id"])
            except (KeyError, TypeError, ValueError):
                continue
            t = str(it.get("type", "other"))
            src = str(it.get("source", "form"))
            items.append(
                LeadItem(
                    id=lid,
                    created_at=str(it.get("createdAt", "")),
                    type=t if t in LEAD_TYPES else "other",
                    source=src if src in LEAD_SOURCES else "other",
                )
            )
        items.sort(key=lambda x: x.id)
        return (int(latest) if isinstance(latest, int) else None), items

    async def health(self) -> HealthProbe:
        start = time.monotonic()
        try:
            r = await self.client.get(f"{self.base}/api/health")
            dur = time.monotonic() - start
            if r.status_code != 200:
                return HealthProbe(False, dur, f"HTTP {r.status_code}")
            try:
                ok = r.json().get("ok") is True
            except (ValueError, AttributeError):
                ok = False
            return HealthProbe(ok, dur, "" if ok else "ответ без ok:true")
        except httpx.HTTPError as e:
            return HealthProbe(False, time.monotonic() - start, e.__class__.__name__)

    async def status(self) -> dict:
        if not self.status_path:
            raise SiteError("SITE_STATUS_PATH не задан")
        try:
            r = await self.client.get(f"{self.base}{self.status_path}", headers={"x-feed-key": self.key})
        except httpx.HTTPError as e:
            raise SiteError(f"нет связи: {e.__class__.__name__}") from e
        if r.status_code != 200:
            raise SiteError(f"HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise SiteError("неверный JSON") from e
        if not isinstance(data, dict):
            raise SiteError("неверный формат")
        return data

    async def homepage(self) -> str:
        try:
            r = await self.client.get(f"{self.base}/")
        except httpx.HTTPError as e:
            raise SiteError(e.__class__.__name__) from e
        return r.text[:200_000]
