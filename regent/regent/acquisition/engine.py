"""AcquisitionEngine: generic machinery; the domain adapter decides *what*.

Pipeline for one AcquisitionRequest::

    plan (adapter) -> navigate/fetch (policy-enforced) -> extract (adapter)
    -> resolve (adapter resolver + generic bookkeeping) -> claims
    -> beliefs / conflicts / freshness -> funnel (adapter)
    -> enrichment jobs (adapter) -> reliability learning -> world projection

The engine commits after each stage so the cockpit shows progress live, and
it enforces a page budget and a wall-clock deadline.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.acquisition import navigate as nav
from regent.acquisition.claims import ClaimStore
from regent.acquisition.domain import DomainAdapter
from regent.acquisition.fetch import Fetcher
from regent.acquisition.resolution import EntityResolution
from regent.acquisition.tables import AcqEntity, AcqJob, AcqMention, AcqRequest, ENTITY_ORDER
from regent.acquisition.types import EnrichmentJobSpec, FetchedDocument, Mention
from regent.core.observe.events import EventStore
from regent.ids import new_id, utcnow


class BudgetExhausted(RuntimeError):
    pass


class AcquisitionEngine:
    def __init__(self, db: Session, adapter: DomainAdapter, request: AcqRequest, *,
                 transport: httpx.BaseTransport | None = None, now: datetime | None = None,
                 max_pages: int = 80, deadline_s: float = 900.0, min_interval_s: float | None = None):
        self.db = db
        self.adapter = adapter
        self.request = request
        kw = {} if min_interval_s is None else {"min_interval_s": min_interval_s}
        self.fetcher = Fetcher(db, request_id=request.id, transport=transport, **kw)
        self.claims = ClaimStore(db, adapter.policy(), now=now)
        self.er = EntityResolution(db, adapter.name, adapter.resolver())
        self.events = EventStore(db)
        self.dirty: set[str] = set()
        self._last_static: FetchedDocument | None = None
        self.max_pages = max_pages
        self.deadline = time.time() + deadline_s
        self.started = now or utcnow()
        # an injected clock (tests, simulated TTL expiry) must stamp *everything* this run
        # observes -- documents, claims and jobs -- or re-observed claims would look old
        self._clock = now
        # region of the entity a job is working on: records it produces belong to that region
        self.region_ctx: dict | None = None
        self.stats: dict[str, Any] = {"pages": 0, "pages_ok": 0, "blocked": 0, "robots_disallowed": 0, "errors": 0,
                                      "mentions": 0, "claims": 0, "by_host": {}, "render_browser": 0}

    # ------------------------------------------------------------------ util

    def log(self, stage: str, message: str, **data: Any) -> None:
        self.request.stage = stage
        self.request.log = list(self.request.log or []) + [{"at": utcnow().isoformat(), "stage": stage,
                                                             "message": message, **data}]
        self.request.updated_at = utcnow()

    def checkpoint(self) -> None:
        self.request.stats = {**(self.request.stats or {}), **self.stats, "resolution": dict(self.er.stats)}
        self.db.commit()

    def budget_left(self) -> bool:
        return self.stats["pages"] < self.max_pages and time.time() < self.deadline

    # ----------------------------------------------------------------- fetch

    def fetch(self, url: str, *, purpose: str, kind: str = "portal", render: str = "auto") -> FetchedDocument | None:
        if not self.budget_left():
            self.log("budget", "page budget or deadline reached; skipping further fetches", url=url)
            return None
        check = None
        if purpose in ("listing", "detail", "verify", "recheck", "market", "discovery"):
            # "has content" means the domain extractor finds records, not a text heuristic
            def check(_text: str, _url: str = url, _purpose: str = purpose) -> bool:
                return bool(self._last_static and self.adapter.extract(self._last_static, _purpose))
        self._last_static = None
        doc = self.fetcher.fetch(url, purpose=purpose, kind=kind, render=render, content_check=check,
                                 on_static=lambda d: setattr(self, "_last_static", d))
        if self._clock is not None and not doc.from_cache:
            doc.fetched_at = self._clock
        if not doc.from_cache:
            self.stats["pages"] += 1
        host = urlparse(url).netloc
        h = self.stats["by_host"].setdefault(host, {"pages": 0, "ok": 0, "blocked": 0, "mentions": 0, "last": ""})
        h["pages"] += 1
        if doc.ok:
            self.stats["pages_ok"] += 1
            h["ok"] += 1
            if doc.render == "browser":
                self.stats["render_browser"] += 1
        elif doc.blocked:
            key = "robots_disallowed" if doc.blocked.get("type") == "robots" else "blocked"
            self.stats[key] += 1
            h["blocked"] += 1
        else:
            self.stats["errors"] += 1
        h["last"] = "ok" if doc.ok else (f"blocked:{doc.blocked.get('type')}" if doc.blocked else f"error:{doc.status}")
        return doc

    def navigate(self, entry_url: str, terms: list[str], *, kind: str, render: str = "auto",
                 hints: list[list[str]] | None = None, avoid: list[str] | None = None,
                 beam: int = 3) -> tuple[FetchedDocument | None, list[str]]:
        """Reach the page for ``terms`` by following anchors from ``entry_url``.

        Small beam search with look-ahead: at each step the best few matching links are
        tried, and a link is accepted only if the page it leads to offers a link for the
        next term (the last step accepts any reachable page). This backs out of wrong
        turns such as a "Tokyo" link that leads to a by-rail-line index."""
        trail = [entry_url]
        doc = self.fetch(entry_url, purpose="navigate", kind=kind, render=render)
        if doc is None or not doc.ok:
            return doc, trail

        def step_hints(i: int) -> list[str] | None:
            return hints[i] if hints and i < len(hints) else None

        def walk(d: FetchedDocument, i: int) -> FetchedDocument | None:
            if i == len(terms):
                return d
            links = nav.find_links(d.html, d.final_url, terms[i], path_hints=step_hints(i), avoid=avoid)[:beam]
            if not links:
                self.log("navigate", f"no link for '{terms[i]}' on {d.final_url}", host=d.host)
                return None
            for link in links:
                nd = self.fetch(link, purpose="listing" if i == len(terms) - 1 else "navigate", kind=kind, render=render)
                if nd is None:
                    return None
                if not nd.ok:
                    continue
                if i + 1 < len(terms) and not nav.find_links(nd.html, nd.final_url, terms[i + 1],
                                                             path_hints=step_hints(i + 1), avoid=avoid):
                    self.log("navigate", f"'{terms[i]}' -> {nd.final_url} has no '{terms[i + 1]}'; backtracking",
                             host=d.host)
                    continue
                trail.append(nd.final_url)
                found = walk(nd, i + 1)
                if found is not None:
                    return found
            return None

        return walk(doc, 0), trail

    # ---------------------------------------------------------------- ingest

    def ingest(self, doc: FetchedDocument, mentions: list[Mention], *, source_kind: str,
               pin: str | None = None, pin_min_p: float = 0.0, region: dict | None = None) -> list[AcqEntity]:
        """``pin``: the page was reached *from* this entity (e.g. its detail link), so its
        single matching record is that entity by provenance -- provided the resolver's own
        probability is at least ``pin_min_p`` (0 = trust provenance fully)."""
        out = []
        pinned = self.db.get(AcqEntity, pin) if pin else None
        for m in mentions:
            parent = None
            if pinned is not None and m.entity_type == pinned.entity_type:
                p, detail = self.adapter.resolver().score(m.entity_type, self.adapter.mention_features(m),
                                                          pinned.features or {})
                if p >= pin_min_p:
                    if m.parent is not None and pinned.parent_id:
                        self._ingest_one(doc, m.parent, source_kind, parent_id=None, force=pinned.parent_id)
                    out.append(self._ingest_one(doc, m, source_kind, parent_id=pinned.parent_id, force=pinned.id,
                                                force_p=p))
                    pinned = None   # one record per pin
                    continue
                self.log("pin", f"record on {doc.final_url} does not match pinned entity (p={p:.2f})",
                         entity=pin, detail=detail)
            if m.parent is not None and (m.parent.claims or m.parent.key_fields):
                child_f = self.adapter.mention_features(m)
                child_f["_doc"] = doc.id
                child_f["_host"] = doc.host
                parent = self._ingest_one(doc, m.parent, source_kind, parent_id=None, child=(m.entity_type, child_f))
            ent = self._ingest_one(doc, m, source_kind, parent_id=parent.id if parent else None)
            rid = (region or self.region_ctx or {}).get("id")
            if rid:
                for x in (ent, parent):
                    if x is not None and not x.region_id:
                        x.region_id = rid
            out.append(ent)
        self.stats["mentions"] += len(mentions)
        self.stats["by_host"].setdefault(doc.host, {"pages": 0, "ok": 0, "blocked": 0, "mentions": 0,
                                                     "last": ""})["mentions"] += len(mentions)
        from regent.acquisition.tables import AcqDocument, AcqSource

        d = self.db.get(AcqDocument, doc.id)
        if d is not None:
            d.mentions = len(mentions)
        src = self.db.get(AcqSource, doc.host)
        if src is not None:
            src.records += len(mentions)
        self.db.flush()
        return out

    def _ingest_one(self, doc: FetchedDocument, m: Mention, source_kind: str, parent_id: str | None,
                    force: str | None = None, force_p: float = 1.0, child: tuple | None = None) -> AcqEntity:
        feats = self.adapter.mention_features(m)
        feats["_doc"] = doc.id
        feats["_host"] = doc.host
        row = AcqMention(id=new_id("mn"), document_id=doc.id, request_id=self.request.id, entity_type=m.entity_type,
                         url=doc.final_url, host=doc.host, features={k: v for k, v in feats.items() if k != "_doc"},
                         links=m.links, raw_text=m.raw_text[:1000], observed_at=doc.fetched_at)
        self.db.add(row)
        self.db.flush()
        if force is not None:
            from regent.acquisition.tables import AcqLink

            ent = self.db.get(AcqEntity, force)
            ent.features = self.adapter.resolver().merge(m.entity_type, ent.features or {}, feats)
            ent.mention_count = (ent.mention_count or 0) + 1
            row.entity_id = ent.id
            row.resolution = {"decision": "pinned", "probability": round(force_p, 4), "reason": "reached via entity link"}
            self.db.add(AcqLink(id=new_id("lnk"), mention_id=row.id, entity_id=ent.id, probability=round(force_p, 4),
                                decision="pinned", features={"provenance": "link from entity"}))
        else:
            ent, decision, p = self.er.resolve(row, m.entity_type, feats, parent_id=parent_id,
                                               request_id=self.request.id, child=child)
        for c in m.claims:
            c.observed_at = c.observed_at or doc.fetched_at
            self.claims.add(ent.id, c, source_host=doc.host, source_kind=source_kind, url=doc.final_url,
                            document_id=doc.id, mention_id=row.id)
            self.stats["claims"] += 1
        self.dirty.add(ent.id)
        return ent

    def fetch_and_ingest(self, url: str, *, purpose: str, kind: str, render: str = "auto", pin: str | None = None,
                         pin_min_p: float = 0.0) -> tuple[FetchedDocument | None, list[AcqEntity]]:
        doc = self.fetch(url, purpose=purpose, kind=kind, render=render)
        if doc is None or not doc.ok:
            return doc, []
        mentions = self.adapter.extract(doc, purpose)
        return doc, self.ingest(doc, mentions, source_kind=kind, pin=pin, pin_min_p=pin_min_p)

    # --------------------------------------------------------------- beliefs

    def refresh_dirty(self) -> int:
        n = 0
        for eid in list(self.dirty):
            e = self.db.get(AcqEntity, eid)
            if e is not None:
                self.claims.refresh(e)
                n += 1
        self.dirty.clear()
        self.db.flush()
        return n

    # ------------------------------------------------------------------ jobs

    def schedule(self, specs: list[EnrichmentJobSpec], *, due_at: datetime | None = None) -> list[AcqJob]:
        out = []
        for s in specs:
            dup = self.db.scalar(select(AcqJob).where(AcqJob.entity_id == s.entity_id, AcqJob.kind == s.kind,
                                                      AcqJob.status.in_(("pending", "done"))).limit(1))
            if dup is not None and not s.params.get("force"):
                continue
            # strictly increasing in plan order (an injected clock is constant): equal-priority jobs then
            # run in the order they were planned, not in whatever order the database returns ties
            self._job_seq = getattr(self, "_job_seq", 0) + 1
            j = AcqJob(id=new_id("job"), request_id=self.request.id, entity_id=s.entity_id, kind=s.kind,
                       params=s.params, reason=s.reason, priority=s.priority, due_at=due_at, status="pending",
                       created_at=self.claims.now() + timedelta(microseconds=self._job_seq))
            self.db.add(j)
            out.append(j)
        self.db.flush()
        return out

    def run_jobs(self, limit: int = 200) -> dict[str, int]:
        tally: dict[str, int] = {}
        for _ in range(limit):
            j = self.db.scalar(select(AcqJob).where(AcqJob.request_id == self.request.id, AcqJob.status == "pending")
                               .order_by(AcqJob.priority.desc(), AcqJob.created_at).limit(1))
            if j is None:
                break
            if not self.budget_left() and j.kind in getattr(self.adapter, "network_jobs", ()):
                j.status, j.error = "skipped", "budget exhausted"
                continue
            try:
                j.result = self.adapter.run_job(self, j) or {}
                j.status = j.result.pop("_status", "done")
            except Exception as e:  # a failing job never fails the request
                j.status, j.error = "failed", f"{type(e).__name__}: {e}"[:400]
            j.finished_at = self.claims.now()
            tally[f"{j.kind}:{j.status}"] = tally.get(f"{j.kind}:{j.status}", 0) + 1
            self.db.flush()
        self.refresh_dirty()
        return tally

    # ----------------------------------------------------------------- finish

    def project(self) -> int:
        n = 0
        for ev in self.adapter.project(self, self.request):
            ev = dict(ev)
            t = ev.pop("type")
            self.events.append(t, ev, source=f"acquisition:{self.adapter.name}",
                               mission_id=self.request.mission_id)
            n += 1
        return n

    def finish(self, status: str = "done") -> dict[str, Any]:
        self.refresh_dirty()
        ents = list(self.db.scalars(select(AcqEntity).where(AcqEntity.request_id == self.request.id).order_by(*ENTITY_ORDER)))
        self.stats["reliability_learning"] = self.claims.learn_reliability(ents, since=self.started - timedelta(minutes=1))
        self.stats["world_events"] = self.project()
        self.request.status = status
        self.log("done", f"acquisition {status}", pages=self.stats["pages"], mentions=self.stats["mentions"])
        self.checkpoint()
        self.fetcher.close()
        return dict(self.request.stats or {})
