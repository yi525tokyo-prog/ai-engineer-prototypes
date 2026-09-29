"""World Acquisition read API: requests, sources, documents, entities with their claims.

Everything the cockpit shows about an acquired entity is derived from stored
claims at read time, so a value on screen can always be traced to the source
page (and its cached copy) that asserted it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from regent.acquisition import domain as D
from regent.acquisition import service
from regent.acquisition.claims import ClaimStore
from regent.acquisition.tables import (AcqClaim, AcqConflict, AcqDocument, AcqEntity, AcqJob, AcqLink, AcqMention,
                                       AcqRequest, AcqSource)


def _get_db():
    from regent.api.app import get_db

    yield from get_db()


router = APIRouter(prefix="/api/acquisition", tags=["acquisition"])

UNIT_COLUMNS = ("rent", "management_fee", "deposit", "key_money", "availability", "move_in", "housing_type",
                "vacancies")
BUILDING_COLUMNS = ("name", "address", "stations", "built_year", "structure", "nearest_stations_public",
                    "rail_distance_m", "hub_minutes_est", "libraries_nearby", "universities_nearby", "coords")


def _source_row(s: AcqSource) -> dict[str, Any]:
    from regent.acquisition.types import SOURCE_KIND_PRIOR

    return {"host": s.host, "kind": s.kind, "fetches": s.fetches, "ok": s.ok, "blocked": s.blocked,
            "robots_disallowed": s.disallowed, "records": s.records, "last_status": s.last_status,
            "last_fetch_at": s.last_fetch_at.isoformat() if s.last_fetch_at else None,
            "reliability": round(s.agree / (s.agree + s.disagree), 3),
            "reliability_n": round(s.agree + s.disagree - 2, 1),
            "kind_prior": SOURCE_KIND_PRIOR.get(s.kind, 0.6)}


def _belief_view(b: dict[str, Any] | None) -> dict[str, Any] | None:
    if not b:
        return None
    out = {k: b.get(k) for k in ("value", "confidence", "fresh", "conflict", "n_sources", "n_claims",
                                 "latest_observed", "ttl_s")}
    out["hypotheses"] = [{k: h.get(k) for k in ("value", "share", "support", "hosts", "fresh")}
                         for h in b.get("hypotheses") or []]
    return out


def _entity_row(db: Session, e: AcqEntity, parents: dict[str, AcqEntity] | None = None) -> dict[str, Any]:
    p = (parents or {}).get(e.parent_id) if e.parent_id else None
    if e.parent_id and p is None:
        p = db.get(AcqEntity, e.parent_id)
    b, pb = e.beliefs or {}, (p.beliefs if p else {}) or {}
    return {"id": e.id, "type": e.entity_type, "label": e.label, "stage": e.stage, "score": round(e.score or 0, 4),
            "score_detail": e.score_detail, "sources": e.source_hosts, "mentions": e.mention_count,
            "updated_at": e.updated_at.isoformat() if e.updated_at else None,
            "building": ({"id": p.id, "label": p.label, "sources": p.source_hosts} if p else None),
            "attrs": {**{k: _belief_view(b.get(k)) for k in UNIT_COLUMNS if b.get(k)},
                      **{k: _belief_view(pb.get(k)) for k in BUILDING_COLUMNS if pb.get(k)}},
            "conflicts": sorted(k for k, v in {**pb, **b}.items() if isinstance(v, dict) and v.get("conflict")),
            "stale": sorted(k for k, v in b.items() if isinstance(v, dict) and v.get("n_claims") and not v.get("fresh"))}


@router.get("/overview")
def overview(mission_id: str | None = None, domain: str = "housing", db: Session = Depends(_get_db)):
    reqs = select(AcqRequest).where(AcqRequest.domain == domain).order_by(AcqRequest.created_at.desc()).limit(20)
    if mission_id:
        reqs = reqs.where(AcqRequest.mission_id == mission_id)
    requests = list(db.scalars(reqs))
    disc = next((r for r in requests if r.goal == "discover" and r.status in ("done", "running")), None)
    stages = dict(db.execute(select(AcqEntity.stage, func.count()).where(
        AcqEntity.domain == domain, AcqEntity.entity_type == "unit", AcqEntity.status == "active")
        .group_by(AcqEntity.stage)).all())
    types = dict(db.execute(select(AcqEntity.entity_type, func.count()).where(
        AcqEntity.domain == domain, AcqEntity.status == "active").group_by(AcqEntity.entity_type)).all())
    links = dict(db.execute(select(AcqLink.decision, func.count()).group_by(AcqLink.decision)).all())
    units = list(db.scalars(select(AcqEntity).where(AcqEntity.domain == domain, AcqEntity.entity_type == "unit",
                                                    AcqEntity.stage.in_(("shortlisted", "filtered")))
                            .order_by(AcqEntity.score.desc()).limit(40)))
    pids = {u.parent_id for u in units if u.parent_id}
    parents = {p.id: p for p in db.scalars(select(AcqEntity).where(AcqEntity.id.in_(pids)))} if pids else {}
    multi = sum(1 for (hosts,) in db.execute(select(AcqEntity.source_hosts).where(
        AcqEntity.domain == domain, AcqEntity.entity_type == "unit", AcqEntity.status == "active"))
        if len(hosts or []) >= 2)
    jobs = dict(db.execute(select(AcqJob.kind + ":" + AcqJob.status, func.count()).group_by(
        AcqJob.kind, AcqJob.status)).all())
    return {
        "domain": domain,
        "requests": [{k: v for k, v in r.to_dict().items() if k not in ("log", "plan")} for r in requests],
        "funnel": ((disc.stats or {}).get("funnel") if disc else None) or {},
        "discovery_log": (disc.log or [])[-60:] if disc else [],
        "entities": types, "unit_stages": stages, "multi_source_units": multi,
        "resolution": links,
        "open_conflicts": db.scalar(select(func.count()).select_from(AcqConflict).where(AcqConflict.status == "open")),
        "claims": db.scalar(select(func.count()).select_from(AcqClaim)),
        "documents": db.scalar(select(func.count()).select_from(AcqDocument)),
        "jobs": jobs,
        "sources": [_source_row(s) for s in db.scalars(select(AcqSource).order_by(AcqSource.fetches.desc()))],
        "candidates": [_entity_row(db, u, parents) for u in units],
    }


@router.get("/requests")
def requests(mission_id: str | None = None, db: Session = Depends(_get_db)):
    q = select(AcqRequest).order_by(AcqRequest.created_at.desc()).limit(100)
    if mission_id:
        q = q.where(AcqRequest.mission_id == mission_id)
    return [r.to_dict() for r in db.scalars(q)]


@router.get("/requests/{request_id}")
def request_detail(request_id: str, db: Session = Depends(_get_db)):
    r = db.get(AcqRequest, request_id)
    if r is None:
        raise HTTPException(404, "unknown request")
    docs = [d.to_dict() for d in db.scalars(select(AcqDocument).where(AcqDocument.request_id == request_id)
                                           .order_by(AcqDocument.fetched_at))]
    return {**r.to_dict(), "documents": docs}


@router.get("/sources")
def sources(db: Session = Depends(_get_db)):
    return [{**_source_row(s), "robots_txt_head": (s.robots_txt or "")[:400]}
            for s in db.scalars(select(AcqSource).order_by(AcqSource.host))]


@router.get("/documents")
def documents(host: str | None = None, request_id: str | None = None, limit: int = 200,
              db: Session = Depends(_get_db)):
    q = select(AcqDocument).order_by(AcqDocument.fetched_at.desc()).limit(min(limit, 1000))
    if host:
        q = q.where(AcqDocument.host == host)
    if request_id:
        q = q.where(AcqDocument.request_id == request_id)
    return [d.to_dict() for d in db.scalars(q)]


@router.get("/entities")
def entities(stage: str | None = None, entity_type: str = "unit", domain: str = "housing", limit: int = 100,
             db: Session = Depends(_get_db)):
    q = select(AcqEntity).where(AcqEntity.domain == domain, AcqEntity.entity_type == entity_type,
                                AcqEntity.status == "active").order_by(AcqEntity.score.desc()).limit(min(limit, 1000))
    if stage:
        q = q.where(AcqEntity.stage == stage)
    return [_entity_row(db, e) for e in db.scalars(q)]


@router.get("/entities/{entity_id}")
def entity_detail(entity_id: str, db: Session = Depends(_get_db)):
    """The entity's competing hypotheses per attribute, each with its claims and source pages."""
    e = db.get(AcqEntity, entity_id)
    if e is None:
        raise HTTPException(404, "unknown entity")
    adapter = D.adapters().get(e.domain)
    if adapter is None:
        raise HTTPException(400, f"no adapter for domain {e.domain}")
    store = ClaimStore(db, adapter.policy(), now=service.now())
    claims = store.claims(e.id)
    by_attr: dict[str, list[AcqClaim]] = defaultdict(list)
    for c in claims:
        by_attr[c.attribute].append(c)
    attrs = {}
    for a, cs in sorted(by_attr.items()):
        attrs[a] = {"belief": store.belief(a, cs),
                    "claims": [{"id": c.id, "value": c.value, "source_host": c.source_host,
                                "source_kind": c.source_kind, "url": c.url, "document_id": c.document_id,
                                "observed_at": c.observed_at.isoformat(), "ttl_s": c.ttl_s,
                                "confidence": c.confidence, "extractor": c.extractor, "evidence": c.evidence[:300]}
                               for c in sorted(cs, key=lambda c: c.observed_at, reverse=True)]}
    mentions = list(db.scalars(select(AcqMention).where(AcqMention.entity_id == e.id)
                               .order_by(AcqMention.observed_at.desc()).limit(50)))
    links = list(db.scalars(select(AcqLink).where(AcqLink.mention_id.in_([m.id for m in mentions]))
                            .order_by(AcqLink.probability.desc()))) if mentions else []
    near_misses = list(db.scalars(select(AcqLink).where(AcqLink.entity_id == e.id, AcqLink.decision == "ambiguous")
                                  .limit(20)))
    parent = db.get(AcqEntity, e.parent_id) if e.parent_id else None
    children = list(db.scalars(select(AcqEntity).where(AcqEntity.parent_id == e.id, AcqEntity.status == "active")
                               .order_by(AcqEntity.score.desc()).limit(50)))
    jobs = list(db.scalars(select(AcqJob).where(AcqJob.entity_id.in_([e.id] + ([e.parent_id] if e.parent_id else [])))
                           .order_by(AcqJob.created_at.desc())))
    conflicts = list(db.scalars(select(AcqConflict).where(AcqConflict.entity_id == e.id)))
    return {
        "entity": {k: v for k, v in e.to_dict().items() if k != "beliefs"},
        "summary": _entity_row(db, e),
        "attributes": attrs,
        "conflicts": [c.to_dict() for c in conflicts],
        "mentions": [{"id": m.id, "url": m.url, "host": m.host, "observed_at": m.observed_at.isoformat(),
                      "resolution": m.resolution, "raw_text": (m.raw_text or "")[:400], "links": m.links}
                     for m in mentions],
        "resolution_links": [lk.to_dict() for lk in links],
        "ambiguous_links": [lk.to_dict() for lk in near_misses],
        "parent": _entity_row(db, parent) if parent else None,
        "children": [_entity_row(db, c) for c in children],
        "jobs": [j.to_dict() for j in jobs],
        "source_counts": dict(Counter(c.source_host for c in claims)),
    }


class RunIn(BaseModel):
    action: str = Field(pattern="^(discover|enrich|recheck)$")
    mission_id: str | None = None
    domain: str = "housing"
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("/run")
def run(body: RunIn, background: BackgroundTasks):
    """Operator-triggered acquisition (the loop does this on its own via the ``acquire`` tool)."""
    if body.domain not in D.adapters():
        raise HTTPException(400, f"no adapter for domain {body.domain}")
    background.add_task(service.run_action, body.action, mission_id=body.mission_id, params=body.params,
                        domain=body.domain)
    return {"accepted": True, "action": body.action}
