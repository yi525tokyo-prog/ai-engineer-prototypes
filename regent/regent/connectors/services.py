"""Service connectors: email, calendar, maps/places, search, commerce, GitHub.

Each connector defines the real integration interface and a local backend.
When credentials for the real backend are missing, the local backend is used
and the tool registry reports the tool as degraded with the missing credential
named explicitly. Nothing here fakes success: local backends perform real,
persisted state changes against a local store.
"""

from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx

from regent.config import settings
from regent.connectors import store
from regent.ids import utcnow
from regent.tools.base import MissingCredential

# ------------------------------------------------------------------- email


class LocalMailbox:
    """Outbox/drafts persisted locally. Sending records a durable 'sent' message
    that the world model observes via a ``message_sent`` event."""

    name = "local-mailbox"

    def draft(self, to: list[str], subject: str, body: str, in_reply_to: str | None = None) -> dict:
        return store.put("mail", "draft", {
            "to": to, "subject": subject, "body": body, "in_reply_to": in_reply_to,
            "status": "draft", "created_at": utcnow().isoformat(),
        })

    def send(self, draft_id: str | None = None, **msg: Any) -> dict:
        if draft_id:
            d = store.get(draft_id)
            if d is None:
                raise ValueError(f"draft {draft_id} not found")
            msg = {k: d.get(k) for k in ("to", "subject", "body", "in_reply_to")}
            store.put("mail", "draft", {"status": "sent"}, rid=draft_id)
        if not msg.get("to"):
            raise ValueError("no recipients")
        sent = store.put("mail", "sent", {**msg, "status": "sent", "sent_at": utcnow().isoformat(),
                                          "draft_id": draft_id})
        return sent

    def outbox(self) -> list[dict]:
        return store.find("mail", "sent")


# ----------------------------------------------------------------- calendar


class LocalCalendar:
    name = "local-calendar"

    def list(self) -> list[dict]:
        return store.find("calendar", "event")

    def hold(self, title: str, start: str, end: str, notes: str = "") -> dict:
        return store.put("calendar", "event", {"title": title, "start": start, "end": end,
                                                "notes": notes, "type": "hold"})

    def create_invite(self, title: str, start: str, end: str, attendees: list[str]) -> dict:
        return store.put("calendar", "event", {"title": title, "start": start, "end": end,
                                                "attendees": attendees, "type": "invite"})


# --------------------------------------------------------------------- maps


class LocalMaps:
    """Fixture-backed places + transit estimates (haversine on known coordinates).
    Real backend: Google Maps / Places (GOOGLE_MAPS_API_KEY)."""

    name = "local-maps"

    def _places(self) -> dict[str, dict]:
        return {p["place_id"]: p for p in store.find("maps", "place")}

    def lookup(self, place_id: str) -> dict:
        p = self._places().get(place_id)
        if p is None:
            raise ValueError(f"unknown place {place_id}")
        return p

    def route(self, origin: str, destination: str, mode: str = "transit") -> dict:
        places = self._places()
        a, b = places.get(origin), places.get(destination)
        if a is None or b is None:
            raise ValueError(f"unknown place(s): {origin}, {destination}")
        km = _haversine(a["lat"], a["lng"], b["lat"], b["lng"])
        speed = {"transit": 22.0, "walk": 4.5, "bike": 14.0}.get(mode, 22.0)
        overhead = {"transit": 12, "walk": 0, "bike": 3}.get(mode, 10)
        minutes = round(km / speed * 60 + overhead)
        fare = round(170 + km * 18) if mode == "transit" else 0
        return {"origin": origin, "destination": destination, "mode": mode, "distance_km": round(km, 2),
                "minutes": minutes, "fare": fare}


def _haversine(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ------------------------------------------------------------------- search


class LocalSearchIndex:
    """Keyword search over a locally indexed corpus (seeded public documents).
    Real backend: Brave Search API (REGENT_SEARCH_API_KEY)."""

    name = "local-index"

    def index(self, url: str, title: str, text: str, tags: list[str] | None = None) -> dict:
        return store.put("search", "doc", {"url": url, "title": title, "text": text, "tags": tags or []})

    def search(self, query: str, limit: int = 5) -> list[dict]:
        terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 2]
        scored = []
        for d in store.find("search", "doc"):
            hay = f"{d['title']} {d['text']} {' '.join(d.get('tags', []))}".lower()
            s = sum(hay.count(t) for t in terms)
            if s:
                scored.append((s, d))
        scored.sort(key=lambda x: -x[0])
        return [{"url": d["url"], "title": d["title"], "snippet": d["text"][:280], "score": s}
                for s, d in scored[:limit]]


class BraveSearch:
    name = "brave"

    def __init__(self) -> None:
        if not settings.search_api_key:
            raise MissingCredential("REGENT_SEARCH_API_KEY", "web search API key not set")

    def search(self, query: str, limit: int = 5) -> list[dict]:
        r = httpx.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": limit},
                      headers={"X-Subscription-Token": settings.search_api_key}, timeout=15)
        r.raise_for_status()
        return [{"url": x["url"], "title": x["title"], "snippet": x.get("description", "")}
                for x in r.json().get("web", {}).get("results", [])]


# ----------------------------------------------------------------- commerce


class LocalCommerce:
    """Purchases/bookings recorded against a local ledger. Real backend: Stripe or
    vendor APIs (credentials per vendor)."""

    name = "local-commerce"

    def purchase(self, vendor: str, item: str, amount: float, currency: str) -> dict:
        return store.put("commerce", "order", {"vendor": vendor, "item": item, "amount": amount,
                                                "currency": currency, "status": "confirmed",
                                                "at": utcnow().isoformat()})


# ------------------------------------------------------------------- github


class GitHubConnector:
    """Live GitHub REST API when GITHUB_TOKEN is present; otherwise inspects local
    git repositories in the workspace (degraded, read-only)."""

    def __init__(self) -> None:
        self.live = bool(settings.github_token)

    def repo_status(self, repo: str) -> dict:
        if self.live and "/" in repo and not Path(repo).exists():
            r = httpx.get(f"https://api.github.com/repos/{repo}",
                          headers={"Authorization": f"Bearer {settings.github_token}"}, timeout=15)
            r.raise_for_status()
            j = r.json()
            return {"repo": repo, "default_branch": j["default_branch"], "open_issues": j["open_issues_count"],
                    "pushed_at": j["pushed_at"], "backend": "github"}
        path = Path(repo) if Path(repo).is_absolute() else settings.workspace / repo
        if not (path / ".git").exists():
            raise ValueError(f"no local git repository at {path}")
        log = subprocess.run(["git", "-C", str(path), "log", "--oneline", "-5"], capture_output=True,
                             text=True, timeout=10).stdout.strip().splitlines()
        status = subprocess.run(["git", "-C", str(path), "status", "--porcelain"], capture_output=True,
                                text=True, timeout=10).stdout.strip().splitlines()
        return {"repo": str(path), "recent_commits": log, "dirty_files": len(status), "backend": "local-git"}

    def create_issue(self, repo: str, title: str, body: str) -> dict:
        if not self.live:
            raise MissingCredential("GITHUB_TOKEN", "creating issues requires GitHub API access")
        r = httpx.post(f"https://api.github.com/repos/{repo}/issues", json={"title": title, "body": body},
                       headers={"Authorization": f"Bearer {settings.github_token}"}, timeout=15)
        r.raise_for_status()
        return {"number": r.json()["number"], "url": r.json()["html_url"]}


