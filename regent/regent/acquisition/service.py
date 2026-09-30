"""Acquisition service: the seam between the Regent loop and World Acquisition.

* ``mission_state`` -- what the acquisition layer knows about a mission
  (discovery done? shortlist lacking deep research? stale claims?)
* ``needs`` -- the ACQUIRE phase of the loop asks each responsible domain
  adapter "what don't I know that blocks a decision?"
* ``run_action`` -- executes discover / enrich / recheck in its own session
  (called by the ``acquire`` tool from the executor's worker thread)
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from regent import db as dbm
from regent.acquisition import domain as D
from regent.acquisition.claims import ClaimStore
from regent.acquisition.engine import AcquisitionEngine
from regent.acquisition.tables import AcqEntity, AcqJob, AcqRequest
from regent.ids import new_id, utcnow

DISCOVERY_MAX_AGE = timedelta(hours=24)
DEEP_JOB_KINDS = ("detail", "geocode")

# test hooks: an httpx transport replaying recorded pages, and a clock
TRANSPORT: httpx.BaseTransport | None = None
CLOCK: Any = None
ENGINE_KW: dict[str, Any] = {}


def now() -> datetime:
    return CLOCK() if CLOCK else utcnow()


def _aware(dt: datetime) -> datetime:
    from datetime import timezone

    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def mission_state(db: Session, mission_id: str, adapter: D.DomainAdapter) -> dict[str, Any]:
    reqs = list(db.scalars(select(AcqRequest).where(AcqRequest.mission_id == mission_id,
                                                    AcqRequest.domain == adapter.name)
                           .order_by(AcqRequest.created_at.desc())))
    disc = [r for r in reqs if r.goal == "discover"]
    state: dict[str, Any] = {
        "requests": len(reqs),
        "discovery_done": any(r.status == "done" and now() - _aware(r.created_at) < DISCOVERY_MAX_AGE for r in disc),
        "discovery_running": any(r.status == "running" for r in reqs),
    }
    shortlisted = list(db.scalars(select(AcqEntity).where(AcqEntity.domain == adapter.name,
                                                          AcqEntity.entity_type == "unit",
                                                          AcqEntity.stage == "shortlisted")))
    pending, stale = [], []
    store = ClaimStore(db, adapter.policy(), now=now())
    for e in shortlisted:
        ids = [e.id] + ([e.parent_id] if e.parent_id else [])
        done = db.scalar(select(AcqJob.id).where(AcqJob.entity_id.in_(ids), AcqJob.kind.in_(DEEP_JOB_KINDS),
                                                 AcqJob.status.in_(("done", "failed", "blocked", "skipped"))).limit(1))
        if done is None:
            pending.append(e.id)
            continue
        beliefs = {a: store.belief(a, store.claims(e.id, a)) for a in adapter.time_sensitive}
        if any(b["n_claims"] and not b["fresh"] for b in beliefs.values()):
            recent = db.scalar(select(AcqJob.id).where(AcqJob.entity_id == e.id, AcqJob.kind == "recheck",
                                                       AcqJob.created_at > now() - timedelta(hours=1)).limit(1))
            if recent is None:
                stale.append(e.id)
    state["shortlist_pending_enrichment"] = pending
    state["stale_shortlisted"] = stale
    state["shortlisted"] = len(shortlisted)
    return state


def needs(db: Session, mission, world) -> list[dict[str, Any]]:
    out = []
    for adapter in D.for_mission(mission):
        st = mission_state(db, mission.id, adapter)
        for n in adapter.information_needs(mission, world, st):
            out.append({**n, "domain": adapter.name, "state": {k: v for k, v in st.items() if not isinstance(v, list)}})
    return out


# One acquisition run at a time per process: runs of the same domain write the same entities
# (an enrich and a recheck in one tick would otherwise deadlock on them).
_RUN_LOCK = threading.Lock()


def run_action(action: str, *, mission_id: str | None, params: dict[str, Any], domain: str = "housing",
               db: Session | None = None) -> dict[str, Any]:
    with _RUN_LOCK:
        return _run_action(action, mission_id=mission_id, params=params, domain=domain, db=db)


def _run_action(action: str, *, mission_id: str | None, params: dict[str, Any], domain: str,
                db: Session | None) -> dict[str, Any]:
    own = db is None
    s = db or dbm.session()
    try:
        adapter = D.adapters()[domain]
        req = AcqRequest(id=new_id("acq"), mission_id=mission_id, domain=domain, goal=action,
                         params={k: v for k, v in params.items() if k not in ("entity_ids",)}, status="running",
                         stats={}, log=[], plan={})
        s.add(req)
        s.commit()
        engine = AcquisitionEngine(s, adapter, req, transport=TRANSPORT, now=CLOCK() if CLOCK else None, **ENGINE_KW)
        status = "done"
        try:
            if action == "discover":
                adapter.run_discovery(engine, req)
                engine.refresh_dirty()
                adapter.funnel(engine, req)
            elif action == "enrich":
                for eid in params.get("entity_ids") or []:
                    e = s.get(AcqEntity, eid)
                    if e is not None:
                        engine.schedule(adapter.enrichment_jobs(engine, e))
                engine.log("deep_research", f"enriching {len(params.get('entity_ids') or [])} shortlisted candidates")
                req.stats = {**(req.stats or {}), "jobs": engine.run_jobs()}
                adapter.funnel(engine, req)
            elif action == "recheck":
                ids = params.get("entity_ids") or [e.id for e in s.scalars(select(AcqEntity).where(
                    AcqEntity.domain == domain, AcqEntity.stage == "shortlisted"))]
                for eid in ids:
                    e = s.get(AcqEntity, eid)
                    if e is not None:
                        engine.schedule(adapter.recheck_jobs(engine, e))
                engine.log("recheck", f"re-observing time-sensitive claims for {len(ids)} candidates")
                req.stats = {**(req.stats or {}), "jobs": engine.run_jobs()}
                adapter.funnel(engine, req)
            else:
                raise ValueError(f"unknown acquisition action {action}")
        except Exception as e:
            import traceback

            status = "failed"
            s.rollback()   # keep what was checkpointed; record the failure after the rollback
            req = s.get(AcqRequest, req.id)
            engine.request = req
            engine.log("error", f"{type(e).__name__}: {e}"[:400], where=traceback.format_exc(limit=4)[-800:])
        stats = engine.finish(status)
        shortlisted = list(s.scalars(select(AcqEntity).where(AcqEntity.domain == domain,
                                                             AcqEntity.stage == "shortlisted")))
        out = {"request_id": req.id, "action": action, "status": status,
               "pages": stats.get("pages", 0), "mentions": stats.get("mentions", 0), "claims": stats.get("claims", 0),
               "funnel": stats.get("funnel", {}), "jobs": stats.get("jobs", {}),
               "shortlisted": [e.id for e in shortlisted], "brief_markdown": brief(s, adapter, shortlisted)}
        s.commit()
        return out
    finally:
        if own:
            s.close()


def brief(db: Session, adapter, units: list[AcqEntity]) -> str:
    """Viewing brief where every material value carries its sources and freshness."""
    lines = ["# Housing shortlist", "", f"Generated {now().isoformat(timespec='minutes')} from live sources.", ""]
    for i, e in enumerate(sorted(units, key=lambda x: -x.score), 1):
        b = e.beliefs or {}
        bl = db.get(AcqEntity, e.parent_id) if e.parent_id else None
        bb = (bl.beliefs if bl else {}) or {}
        name = (bb.get("name") or {}).get("value") or (bb.get("address") or {}).get("value") or \
            (b.get("title") or {}).get("value") or e.label
        region = db.get(AcqEntity, e.region_id) if e.region_id else None
        cur = (b.get("currency") or {}).get("value")
        lines.append(f"## {i}. {name} — {e.label}" + (f" — {region.label}" if region else "")
                     + (f" [{cur}]" if cur else "") + f" (score {e.score:.3f})")
        for attr, src in (("rent", b), ("rent_period", b), ("management_fee", b), ("deposit", b), ("key_money", b),
                          ("bedrooms", b), ("unit_kind", b), ("availability", b), ("move_in", b), ("address", bb),
                          ("postcode", bb), ("stations", bb), ("nearest_stations_public", bb), ("centre_km", bb),
                          ("rail_distance_m", bb), ("hub_minutes_est", bb), ("libraries_nearby", bb)):
            x = src.get(attr)
            if not x:
                continue
            hyps = "; ".join(f"{h['value']} ← {', '.join(h['hosts'])}" for h in x["hypotheses"][:3])
            flag = (" ⚠ conflict" if x.get("conflict") else "") + ("" if x.get("fresh", True) else " ⏱ stale")
            lines.append(f"- **{attr}**: {x['value']} (conf {x['confidence']:.2f}){flag} — {hyps}")
        lines.append("")
    return "\n".join(lines)
