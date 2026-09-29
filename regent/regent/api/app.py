"""HTTP surface of Regent (FastAPI).

The cockpit reads ``/api/missions/{id}/cockpit``. Writes go through the same
core services the loop uses; every write is an event. A background worker
drives the loop; ``POST .../tick`` runs it immediately.
"""

from __future__ import annotations

import html
import threading
from contextlib import asynccontextmanager
from typing import Any, Iterator

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from regent import db as dbm
from regent.api import views
from regent.config import settings
from regent.core.authority.manager import AuthorityManager
from regent.core.capabilities.manager import CapabilityManager
from regent.core.constitution.model import ConstitutionModel
from regent.core.goals.missions import MissionGraph
from regent.core.human.interrupts import HumanInterruptManager
from regent.core.loop import BackgroundLoop, RegentLoop
from regent.core.memory.memory import MemoryStore
from regent.core.observe.events import EventStore
from regent.core.treasury.treasury import Treasury
from regent.core.world.projector import rebuild, take_snapshot
from regent.core.world.state import WorldView, what_changed
from regent.db import Decision, Evidence, Mission, Operation, Route, RouteScore, Skill, Snapshot
from regent.global_brain.brain import LocalGlobalBrain, PrivacyViolation
from regent.runtime import get_services
from regent.sim import scenario

_loop: BackgroundLoop | None = None
_run_lock = threading.Lock()


def get_db() -> Iterator[Session]:
    s = dbm.session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def run_loop_now() -> dict[str, Any]:
    """Run all active missions to quiescence (serialized with the background worker)."""
    lock = _loop.lock if _loop is not None else _run_lock
    with lock:
        s = dbm.session()
        try:
            out = RegentLoop(s).run_all()
            s.commit()
            return {m: [{"tick": r.tick, "status": r.status, "progress": r.progress} for r in reps]
                    for m, reps in out.items()}
        finally:
            s.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    dbm.configure()
    dbm.init_db()
    s = dbm.session()
    try:
        get_services().tools.load_built(s)
    finally:
        s.close()
    if settings.background_loop:
        _loop = BackgroundLoop(settings.loop_interval_s)
        _loop.start()
    yield
    if _loop is not None:
        _loop.stop()


app = FastAPI(title="Regent", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------ system


@app.get("/api/system")
def system(db: Session = Depends(get_db)) -> dict[str, Any]:
    sv = get_services()
    missing = sorted({c for t in sv.tools.tools.values() for c in t.missing_credentials}
                     | {c for p in sv.providers.providers.values() for c in p.missing_credentials()})
    return {
        "database": dbm.engine().dialect.name, "event_seq": EventStore(db).head(),
        "providers": sv.providers.describe(), "tools": sv.tools.describe(), "missing_credentials": missing,
        "background_loop": {"enabled": _loop is not None, "passes": _loop.passes if _loop else 0,
                            "last_error": _loop.last_error if _loop else None},
        "workspace": str(settings.workspace),
    }


# ---------------------------------------------------------------- missions


class MissionIn(BaseModel):
    title: str
    objective: str
    success_criteria: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    value_scale: float = 1.0
    horizon_days: float = 30
    parent_id: str | None = None


@app.get("/api/missions")
def missions(db: Session = Depends(get_db)):
    return views.mission_list(db)


@app.post("/api/missions")
def create_mission(body: MissionIn, bg: BackgroundTasks, db: Session = Depends(get_db)):
    m = MissionGraph(db).create(**body.model_dump())
    db.commit()
    bg.add_task(run_loop_now)
    return m.to_dict()


@app.get("/api/missions/{mission_id}/cockpit")
def cockpit(mission_id: str, db: Session = Depends(get_db)):
    try:
        return views.cockpit(db, mission_id)
    except KeyError:
        raise HTTPException(404, "mission not found")


@app.post("/api/missions/{mission_id}/tick")
def tick(mission_id: str, db: Session = Depends(get_db)):
    if db.get(Mission, mission_id) is None:
        raise HTTPException(404)
    return run_loop_now()


@app.post("/api/missions/{mission_id}/regenerate")
def regenerate(mission_id: str, bg: BackgroundTasks, db: Session = Depends(get_db)):
    m = db.get(Mission, mission_id)
    if m is None:
        raise HTTPException(404)
    m.attrs = {**(m.attrs or {}), "regenerate": True}
    EventStore(db).append("fact_observed", {"key": f"mission.{m.id}.regenerate_requested", "value": True},
                          source="principal", mission_id=m.id)
    db.commit()
    bg.add_task(run_loop_now)
    return {"ok": True}


class OverrideIn(BaseModel):
    route_id: str
    note: str = ""


@app.post("/api/missions/{mission_id}/override")
def override(mission_id: str, body: OverrideIn, bg: BackgroundTasks, db: Session = Depends(get_db)):
    """Final constitutional authority: the principal picks a route. Regent complies and
    learns from the choice (constitution inference), without being asked to re-plan by hand."""
    m = db.get(Mission, mission_id)
    r = db.get(Route, body.route_id)
    if m is None or r is None or r.mission_id != m.id:
        raise HTTPException(404)
    prev = db.get(Route, m.selected_route_id) if m.selected_route_id else None
    from regent.core.audit.log import DecisionLog
    from regent.core.planner.planner import Planner

    planner = Planner(db, get_services())
    if prev is not None and prev.id != r.id:
        prev.status = "alive"
        planner.cancel_route_steps(prev, reason="principal override")
    r.status = "selected"
    m.selected_route_id = r.id
    d = DecisionLog(db).record(mission_id=m.id, kind="plan_changed", tick=m.tick_count,
                               summary=f"Principal override: '{r.title}'" + (f" ({body.note})" if body.note else ""),
                               selected_route_id=r.id, previous_route_id=prev.id if prev else None,
                               rationale={"trigger": "principal_override", "note": body.note})
    rejected = [prev] if prev is not None and prev.id != r.id else []
    updates = ConstitutionModel(db).observe_choice(r, rejected, kind="override", decision_id=d.id) if rejected else []
    # the override pins the choice: raise the switch bar for this route via an explicit priority item
    ConstitutionModel(db).upsert(id=f"pin:{m.id}", type="priority", statement=f"Principal chose '{r.title}'",
                                 dimension=f"tag:pinned_{r.key}", direction=1.0, confidence=0.95,
                                 source={"kind": "override", "decision_id": d.id})
    r.tags = list(set((r.tags or []) + [f"pinned_{r.key}"]))
    EventStore(db).append("plan_changed", {"decision_id": d.id, "selected": r.key, "previous": prev.key if prev else None,
                                           "reason": "principal override"}, source="principal", mission_id=m.id)
    db.commit()
    bg.add_task(run_loop_now)
    return {"decision": d.to_dict(), "constitution_updates": updates}


@app.post("/api/missions/{mission_id}/reject")
def reject(mission_id: str, body: OverrideIn, bg: BackgroundTasks, db: Session = Depends(get_db)):
    m = db.get(Mission, mission_id)
    r = db.get(Route, body.route_id)
    if m is None or r is None:
        raise HTTPException(404)
    r.status = "abandoned"
    r.invalidated_reason = f"rejected by principal{': ' + body.note if body.note else ''}"
    from regent.core.planner.planner import Planner

    Planner(db, get_services()).cancel_route_steps(r, reason="rejected by principal")
    if m.selected_route_id == r.id:
        m.selected_route_id = None
    alive = list(db.scalars(select(Route).where(Route.mission_id == m.id, Route.status.in_(("alive", "selected")))))
    best = max(alive, key=lambda x: x.score) if alive else None
    from regent.core.audit.log import DecisionLog

    d = DecisionLog(db).record(mission_id=m.id, kind="route_rejected", tick=m.tick_count,
                               summary=f"Principal rejected '{r.title}'", previous_route_id=r.id,
                               rationale={"note": body.note})
    updates = ConstitutionModel(db).observe_choice(best, [r], kind="rejection", decision_id=d.id) if best else []
    EventStore(db).append("route_invalidated", {"route_id": r.id, "key": r.key, "reason": r.invalidated_reason},
                          source="principal", mission_id=m.id)
    db.commit()
    bg.add_task(run_loop_now)
    return {"decision": d.to_dict(), "constitution_updates": updates}


@app.get("/api/routes/{route_id}")
def route_detail(route_id: str, db: Session = Depends(get_db)):
    r = db.get(Route, route_id)
    if r is None:
        raise HTTPException(404)
    ops = list(db.scalars(select(Operation).where(Operation.route_id == r.id).order_by(Operation.sequence)))
    hist = [x.to_dict() for x in db.scalars(select(RouteScore).where(RouteScore.route_id == r.id)
                                            .order_by(RouteScore.created_at))]
    return {**views.route_dict(r, ops), "score_history": hist}


@app.get("/api/operations/{op_id}")
def operation(op_id: str, db: Session = Depends(get_db)):
    o = db.get(Operation, op_id)
    if o is None:
        raise HTTPException(404)
    return o.to_dict()


# ------------------------------------------------------------------ events


class EventIn(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = "user"
    mission_id: str | None = None
    domain: str = "private"


@app.post("/api/events")
def post_events(body: list[EventIn] | EventIn, bg: BackgroundTasks, db: Session = Depends(get_db)):
    items = body if isinstance(body, list) else [body]
    es = EventStore(db)
    out = [views.event_dict(es.append(e.type, e.payload, source=e.source, mission_id=e.mission_id,
                                      domain=e.domain)) for e in items]
    db.commit()
    bg.add_task(run_loop_now)
    return out


@app.get("/api/events")
def get_events(since: int = 0, mission_id: str | None = None, limit: int = 200, db: Session = Depends(get_db)):
    return [views.event_dict(e) for e in EventStore(db).since(since, mission_id=mission_id, limit=limit)]


class TextIn(BaseModel):
    text: str


@app.post("/api/ingest/text")
def ingest_text(body: TextIn, bg: BackgroundTasks, db: Session = Depends(get_db)):
    reg = get_services().providers
    p = reg.select("extract_world_state", stakes=0.5)
    events = reg.call(p, "extract_world_state", lambda: p.extract_world_state(body.text, WorldView.load(db).summary()))
    es = EventStore(db)
    stored = []
    for e in events:
        e = dict(e)
        t = e.pop("type")
        stored.append(views.event_dict(es.append(t, e, source=f"extract:{p.name}")))
    db.commit()
    bg.add_task(run_loop_now)
    return {"provider": p.name, "events": stored}


# ------------------------------------------------------------------- world


@app.get("/api/world")
def world(db: Session = Depends(get_db)):
    w = WorldView.load(db)
    return {**w.summary(max_entities=500), "panel": views.world_panel(db, w)}


@app.get("/api/world/snapshots")
def snapshots(db: Session = Depends(get_db)):
    return [{"id": s.id, "event_seq": s.event_seq, "reason": s.reason, "created_at": s.created_at.isoformat()}
            for s in db.scalars(select(Snapshot).order_by(Snapshot.event_seq.desc()).limit(100))]


@app.get("/api/world/changes")
def changes(from_snapshot: str, to_snapshot: str, db: Session = Depends(get_db)):
    try:
        return what_changed(db, from_snapshot, to_snapshot)
    except KeyError:
        raise HTTPException(404)


@app.post("/api/world/rebuild")
def world_rebuild(from_snapshot: str | None = None, db: Session = Depends(get_db)):
    before = take_snapshot(db, "pre-rebuild")
    n = rebuild(db, from_snapshot=from_snapshot)
    after = take_snapshot(db, "post-rebuild")
    diff = what_changed(db, before.id, after.id)
    db.commit()
    return {"replayed_events": n, "diff_vs_before": diff}


# --------------------------------------------------------------- interrupts


class ResolveIn(BaseModel):
    response: dict[str, Any] = Field(default_factory=dict)
    resolution: str = "completed"


@app.get("/api/interrupts")
def interrupts(db: Session = Depends(get_db)):
    return [h.to_dict() for h in HumanInterruptManager(db).open()]


@app.post("/api/interrupts/{interrupt_id}/resolve")
def resolve_interrupt(interrupt_id: str, body: ResolveIn, bg: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        hi = HumanInterruptManager(db, get_services()).resolve(interrupt_id, body.response, resolution=body.resolution)
    except KeyError:
        raise HTTPException(404)
    db.commit()
    bg.add_task(run_loop_now)  # Regent resumes automatically
    return hi.to_dict()


# ------------------------------------------------------ governance & state


class PreferenceIn(BaseModel):
    type: str = "weak_preference"
    statement: str
    dimension: str | None = None
    direction: float = 1.0
    confidence: float | None = None
    rule: dict[str, Any] | None = None


@app.get("/api/constitution")
def constitution(db: Session = Depends(get_db)):
    return ConstitutionModel(db).grouped()


@app.post("/api/constitution")
def add_preference(body: PreferenceIn, bg: BackgroundTasks, db: Session = Depends(get_db)):
    it = ConstitutionModel(db).upsert(**body.model_dump())
    db.commit()
    bg.add_task(run_loop_now)
    return ConstitutionModel.as_dict(it)


class GrantIn(BaseModel):
    scope: str
    level: str = "COMMIT"
    constraints: dict[str, Any] = Field(default_factory=dict)
    note: str = ""
    granted: bool = True


@app.get("/api/authority")
def authority(db: Session = Depends(get_db)):
    return [g.to_dict() for g in AuthorityManager(db, get_services().tools).grants()]


@app.post("/api/authority/grants")
def add_grant(body: GrantIn, bg: BackgroundTasks, db: Session = Depends(get_db)):
    g = AuthorityManager(db, get_services().tools).grant(**body.model_dump())
    db.commit()
    bg.add_task(run_loop_now)
    return g.to_dict()


@app.delete("/api/authority/grants/{grant_id}")
def revoke_grant(grant_id: str, db: Session = Depends(get_db)):
    AuthorityManager(db, get_services().tools).revoke(grant_id)
    return {"ok": True}


@app.get("/api/treasury")
def treasury(db: Session = Depends(get_db)):
    t = Treasury(db)
    return {"resources": [r.to_dict() for r in t.resources()], "scarcity": t.scarcity(),
            "ledger": [e.to_dict() for e in t.ledger(100)]}


@app.get("/api/decisions")
def decisions(mission_id: str | None = None, kind: str | None = None, db: Session = Depends(get_db)):
    q = select(Decision).order_by(Decision.created_at.desc()).limit(200)
    if mission_id:
        q = q.where(Decision.mission_id == mission_id)
    if kind:
        q = q.where(Decision.kind == kind)
    return [d.to_dict() for d in db.scalars(q)]


@app.get("/api/decisions/{decision_id}")
def decision(decision_id: str, db: Session = Depends(get_db)):
    d = db.get(Decision, decision_id)
    if d is None:
        raise HTTPException(404)
    ev = [e.to_dict() for e in db.scalars(select(Evidence).where(Evidence.id.in_(d.evidence_ids or [])))]
    return {**d.to_dict(), "evidence": ev}


@app.get("/api/evidence")
def evidence(mission_id: str | None = None, db: Session = Depends(get_db)):
    q = select(Evidence).order_by(Evidence.created_at.desc()).limit(200)
    if mission_id:
        q = q.where(Evidence.mission_id == mission_id)
    return [e.to_dict() for e in db.scalars(q)]


@app.get("/api/capabilities")
def capabilities(db: Session = Depends(get_db)):
    return CapabilityManager(db, get_services()).acquisition_state()


@app.get("/api/capabilities/{cap_id}/options")
def capability_options(cap_id: str, db: Session = Depends(get_db)):
    opts = CapabilityManager(db, get_services()).acquisition_options(cap_id, WorldView.load(db))
    return [o.model_dump() for o in opts]


@app.get("/api/skills")
def skills(db: Session = Depends(get_db)):
    return [s.to_dict() for s in db.scalars(select(Skill).order_by(Skill.created_at.desc()))]


@app.get("/api/global/skills")
def query_skills(q: str, db: Session = Depends(get_db)):
    return LocalGlobalBrain(db).query_skill(q)


@app.get("/api/global/facts")
def query_world(q: str, db: Session = Depends(get_db)):
    return LocalGlobalBrain(db).query_world(q)


class GlobalFactIn(BaseModel):
    subject: str
    statement: str
    value: Any = None
    confidence: float = 0.5


@app.post("/api/global/facts")
def publish_fact(body: GlobalFactIn, db: Session = Depends(get_db)):
    try:
        f = LocalGlobalBrain(db).publish_fact(body.subject, body.statement, body.value, confidence=body.confidence)
    except PrivacyViolation as e:
        raise HTTPException(422, str(e))
    return f.to_dict()


@app.get("/api/memory")
def memory(q: str, k: int = 8, db: Session = Depends(get_db)):
    return [{"id": m.id, "kind": m.kind, "text": m.text, "score": s, "domain": m.domain}
            for m, s in MemoryStore(db).recall(q, k)]


# --------------------------------------------------------------------- sim


class SeedIn(BaseModel):
    reset: bool = True
    api_url: str | None = None
    run: bool = True


@app.post("/api/sim/seed")
def sim_seed(body: SeedIn, request: Request, bg: BackgroundTasks):
    lock = _loop.lock if _loop is not None else _run_lock
    with lock:
        if body.reset:
            dbm.init_db(drop=True)
            from regent.runtime import set_services

            set_services(None)
        s = dbm.session()
        try:
            api_url = body.api_url or settings.public_api_url or str(request.base_url)
            out = scenario.seed(s, api_url)
        finally:
            s.close()
    if body.run:
        bg.add_task(run_loop_now)
    return out


@app.get("/api/sim/script")
def sim_script():
    return [{"key": k, "label": v["label"]} for k, v in scenario.SCRIPT.items()]


@app.post("/api/sim/script/{key}")
def sim_apply(key: str, bg: BackgroundTasks, db: Session = Depends(get_db)):
    if key not in scenario.SCRIPT:
        raise HTTPException(404)
    seqs = scenario.apply_script(db, key)
    bg.add_task(run_loop_now)
    return {"events": seqs}


# ---------------------------------------------------------- simulated portal

PORTAL_CSS = """body{font:15px/1.5 system-ui,sans-serif;max-width:560px;margin:48px auto;padding:0 16px;color:#1d1d1f}
h1{font-size:18px}.box{border:1px solid #ccc;border-radius:6px;padding:16px;margin:16px 0}
code{font-size:20px;letter-spacing:4px;background:#f2f2f2;padding:4px 8px}dt{color:#666;font-size:12px}
dd{margin:0 0 8px 0}input{font-size:16px;padding:6px}button{font-size:15px;padding:6px 12px}"""


@app.get("/sim/portal/{slug}", response_class=HTMLResponse)
def portal(slug: str, error: int = 0):
    st = scenario.portal_state(slug)
    if st is None:
        raise HTTPException(404)
    if not st.get("captcha_solved"):
        err = "<p style='color:#b00'>Incorrect code, try again.</p>" if error else ""
        return f"""<!doctype html><html><head><title>Client portal - verification</title><style>{PORTAL_CSS}</style></head>
<body><h1>Kinoshita Design - client portal</h1>
<div class="box captcha-challenge" id="captcha" data-captcha="text">
<p><strong>Verify you are human</strong> to confirm your meeting slot.</p>
<p>Type the characters: <code>{scenario.PORTAL_CODE}</code></p>{err}
<form method="post" action="/sim/portal/{slug}/verify"><input name="code" autocomplete="off" autofocus>
<button type="submit">Verify &amp; confirm slot</button></form></div></body></html>"""
    return f"""<!doctype html><html><head><title>Client portal - meeting</title><style>{PORTAL_CSS}</style></head>
<body><h1>Kinoshita Design - client portal</h1><div class="box"><dl>
<dt>Meeting slot</dt><dd>{html.escape(st['slot'])} &middot; <span id="slot-status">{st['slot_status']}</span></dd>
<dt>Format</dt><dd id="meeting-format">{st['meeting_format']}</dd>
<dt>Budget status</dt><dd id="budget-status">{st['budget_status']}</dd>
<dt>Revised engagement value (JPY)</dt><dd id="revised-value">{st['revised_value']}</dd>
<dt>Note from Haruka</dt><dd id="client-note">{html.escape(st['client_note'])}</dd></dl></div></body></html>"""


@app.post("/sim/portal/{slug}/verify")
def portal_verify(slug: str, code: str = Form(...)):
    ok = scenario.portal_verify(slug, code)
    return RedirectResponse(f"/sim/portal/{slug}" + ("" if ok else "?error=1"), status_code=303)
