"""Global Brain: three information domains.

* PRIVATE  -- user-specific (messages, money, relationships, private files)
* SHARED   -- relationship/group information shared with specific principals
* GLOBAL   -- generalized public knowledge and reusable skills

Nothing private flows to GLOBAL by default. ``publish_fact``/``publish_skill``
to the global domain pass through ``PrivacyFilter``, which rejects content
that references private entities, contact details or amounts tied to the
principal, and generalizes skills by replacing concrete values with
placeholders.

``LocalGlobalBrain`` stores the global domain in the same database (event
sourced). ``RemoteGlobalBrain`` is the synchronization interface for a future
distributed network (REGENT_GLOBAL_BRAIN_URL).
"""

from __future__ import annotations

import re
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.config import settings
from regent.core.memory.memory import HashEmbedder
from regent.core.observe.events import EventStore
from regent.db import Entity, GlobalFact, Skill
from regent.ids import new_id
from regent.tools.base import MissingCredential

DOMAINS = ("private", "shared", "global")


class PrivacyViolation(ValueError):
    pass


class PrivacyFilter:
    EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
    PHONE = re.compile(r"\+?\d[\d\s-]{8,}\d")
    MONEY = re.compile(r"(¥|\$|€|JPY|USD)\s?[\d,]{3,}")

    def __init__(self, db: Session):
        self.db = db
        self._private_terms: set[str] | None = None

    def private_terms(self) -> set[str]:
        if self._private_terms is None:
            terms: set[str] = set()
            for e in self.db.scalars(select(Entity).where(Entity.domain != "global")):
                terms.add(e.id.lower())
                if e.kind in ("person", "organization", "account", "contract", "message") and len(e.name) > 3:
                    terms.add(e.name.lower())
            self._private_terms = terms
        return self._private_terms

    def violations(self, text: str) -> list[str]:
        low = text.lower()
        out = []
        if self.EMAIL.search(text):
            out.append("email address")
        if self.PHONE.search(text):
            out.append("phone number")
        if self.MONEY.search(text):
            out.append("monetary amount")
        for t in self.private_terms():
            if t and re.search(rf"\b{re.escape(t)}\b", low):
                out.append(f"private entity '{t}'")
        return out

    def generalize(self, text: str) -> str:
        text = self.EMAIL.sub("<email>", text)
        text = self.PHONE.sub("<phone>", text)
        text = self.MONEY.sub("<amount>", text)
        text = re.sub(r"https?://[^\s\"']+", "<url>", text)
        for t in sorted(self.private_terms(), key=len, reverse=True):
            if t:
                text = re.sub(rf"\b{re.escape(t)}\b", "<entity>", text, flags=re.I)
        return text


class GlobalBrain(Protocol):
    def publish_fact(self, subject: str, statement: str, value: Any = None, *, confidence: float = 0.5,
                     provenance: list | None = None, domain: str = "global") -> GlobalFact: ...

    def publish_skill(self, skill: dict[str, Any], *, domain: str = "global") -> Skill: ...

    def query_world(self, query: str, k: int = 5) -> list[dict[str, Any]]: ...

    def query_skill(self, task: str, k: int = 5) -> list[dict[str, Any]]: ...


class LocalGlobalBrain:
    def __init__(self, db: Session):
        self.db = db
        self.events = EventStore(db)
        self.filter = PrivacyFilter(db)
        self.embedder = HashEmbedder()

    def _check(self, text: str, domain: str) -> None:
        if domain == "global":
            v = self.filter.violations(text)
            if v:
                raise PrivacyViolation(f"refusing to publish private data to global domain: {', '.join(sorted(set(v)))}")

    def publish_fact(self, subject, statement, value=None, *, confidence=0.5, provenance=None, domain="global"):
        self._check(f"{subject} {statement} {value}", domain)
        fid = new_id("gf")
        self.events.append("global_fact_published", {"id": fid, "domain": domain, "subject": subject,
                                                     "statement": statement, "value": value,
                                                     "confidence": confidence, "provenance": provenance or []},
                           source="global_brain", domain=domain)
        return self.db.get(GlobalFact, fid)

    def publish_skill(self, skill, *, domain="global"):
        payload = dict(skill)
        if domain == "global":
            gen = lambda x: self.filter.generalize(x) if isinstance(x, str) else x  # noqa: E731
            payload["task_pattern"] = gen(payload.get("task_pattern", ""))
            payload["name"] = gen(payload.get("name", ""))
            payload["procedure"] = [{k: gen(v) for k, v in step.items()} for step in payload.get("procedure", [])]
            payload["failure_modes"] = [gen(f) for f in payload.get("failure_modes", [])]
            payload["preconditions"] = [gen(p) for p in payload.get("preconditions", [])]
            payload["provenance"] = [{k: v for k, v in p.items() if k in ("kind", "at", "outcome")}
                                     for p in payload.get("provenance", [])]
            self._check(str({k: payload.get(k) for k in ("name", "task_pattern", "procedure", "failure_modes")}), domain)
        sid = payload.get("id") or new_id("skill")
        if domain == "global" and not sid.startswith("global:"):
            sid = f"global:{sid}"
        payload.update({"id": sid, "domain": domain})
        self.events.append("skill_published", payload, source="global_brain", domain=domain)
        return self.db.get(Skill, sid)

    def query_world(self, query, k=5):
        qv = self.embedder.embed(query)
        facts = list(self.db.scalars(select(GlobalFact)))
        scored = sorted(((sum(a * b for a, b in zip(qv, self.embedder.embed(f"{f.subject} {f.statement}"))), f)
                         for f in facts), key=lambda x: -x[0])
        return [{"id": f.id, "subject": f.subject, "statement": f.statement, "value": f.value,
                 "confidence": f.confidence, "domain": f.domain, "score": round(s, 3)} for s, f in scored[:k] if s > 0]

    def query_skill(self, task, k=5, domains: tuple[str, ...] = DOMAINS):
        qv = self.embedder.embed(task)
        skills = list(self.db.scalars(select(Skill).where(Skill.domain.in_(domains))))
        scored = sorted(((sum(a * b for a, b in zip(qv, self.embedder.embed(f"{s.name} {s.task_pattern}"))), s)
                         for s in skills), key=lambda x: -x[0])
        return [{**s.to_dict(), "score": round(sc, 3)} for sc, s in scored[:k] if sc > 0.05]


class RemoteGlobalBrain:
    """Sync client for a distributed global brain. Only GLOBAL-domain items are ever sent."""

    def __init__(self, local: LocalGlobalBrain):
        if not settings.global_brain_url:
            raise MissingCredential("REGENT_GLOBAL_BRAIN_URL", "no global brain network configured")
        self.local = local
        self.url = settings.global_brain_url.rstrip("/")

    def publish_fact(self, subject, statement, value=None, *, confidence=0.5, provenance=None, domain="global"):
        f = self.local.publish_fact(subject, statement, value, confidence=confidence, provenance=provenance)
        httpx.post(f"{self.url}/facts", json=f.to_dict(), timeout=10).raise_for_status()
        return f

    def publish_skill(self, skill, *, domain="global"):
        s = self.local.publish_skill(skill, domain="global")
        httpx.post(f"{self.url}/skills", json=s.to_dict(), timeout=10).raise_for_status()
        return s

    def query_world(self, query, k=5):
        r = httpx.get(f"{self.url}/facts", params={"q": query, "k": k}, timeout=10)
        r.raise_for_status()
        return r.json() + self.local.query_world(query, k)

    def query_skill(self, task, k=5):
        r = httpx.get(f"{self.url}/skills", params={"q": task, "k": k}, timeout=10)
        r.raise_for_status()
        return r.json() + self.local.query_skill(task, k)


def global_brain(db: Session) -> GlobalBrain:
    local = LocalGlobalBrain(db)
    if settings.global_brain_url:
        try:
            return RemoteGlobalBrain(local)
        except MissingCredential:
            pass
    return local
