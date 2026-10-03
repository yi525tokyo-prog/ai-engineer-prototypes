"""What the person sees: their requests, in plain words.

Everything Regent does internally (missions, routes, tools, operations, interrupts) is translated
here into: what you asked, what Regent is doing about it right now, what it needs from you (only
when it truly needs you), what came out of it, and how sure it is. Machinery is not shown.
"""

from __future__ import annotations

import os
import shutil
import time
from datetime import timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.db import Event, HumanInterrupt, Mission, Operation, Route

router = APIRouter()

# what an operation means to the person, in their words
DOING = {
    "acquire.analyze": "Understanding what you're asking",
    "acquire.discover": "Looking things up on the web",
    "acquire.inventory": "Checking what can actually answer this, and on what terms",
    "software.compose": "Putting the answer together",
    "software.verify": "Double-checking the answer against its sources",
    "software.activate": "Setting it up so it stays current",
    "software.reuse": "Using what it already built for this",
    "software.upgrade": "Adding the newly connected source",
    "software.design_app": "Working out exactly what the app must do and how to test it",
    "software.build_app": "Having the app built, then testing it itself in a real browser",
    "software.extend_app": "Adding this to your app and testing it on a copy of your data",
    "software.use_app": "Doing it in your app",
    "software.remind": "Scheduling the message",
}
DONE = {
    "acquire.analyze": "Understood the request",
    "acquire.discover": "Looked it up on the web",
    "acquire.inventory": "Checked what can answer it",
    "software.compose": "Put the answer together",
    "software.verify": "Checked the answer against its sources",
    "software.activate": "Set it up to stay current",
    "software.reuse": "Reused what it had already built",
    "software.design_app": "Designed the app and how to test it",
    "software.build_app": "Had the app built and tested it",
    "software.extend_app": "Updated your app (tested on a copy of your data first)",
    "software.use_app": "Did it in your app",
    "software.remind": "Scheduled the message for you",
}


def get_db():
    from regent import db as dbm

    s = dbm.session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _kick(bg: BackgroundTasks) -> None:
    from regent.api.app import run_loop_now

    bg.add_task(run_loop_now)


def _ago(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    s = max(0, time.time() - dt.timestamp())
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} d ago"


# ------------------------------------------------------------------ questions for the person


def _question(db: Session, hi: HumanInterrupt) -> dict[str, Any]:
    """An interrupt as a question a person can answer in seconds."""
    op = db.get(Operation, hi.operation_id) if hi.operation_id else None
    what = f"{op.tool}.{op.action}" if op else ""
    q: dict[str, Any] = {"id": hi.id, "item": hi.mission_id, "seconds": hi.estimated_time_seconds or 0}
    if hi.kind == "authorization":
        if what == "software.build_app":
            q.update(title="Build a small private app for this?",
                     body=("Nothing that already exists does what you asked without handing your data to someone "
                           "else. Regent can have a coding assistant build a small app just for you, then test it "
                           "itself (in a real browser, including what must stay private) before you use it. It "
                           "usually takes 5–10 minutes and about $1 of AI usage. It runs on this computer."))
        elif what == "software.extend_app":
            q.update(title="Add this to your app?",
                     body=("Your app can't do this yet. Regent can have a new version built, test it on a copy of "
                           "your data, and switch over only if nothing is lost (your current version is kept as a "
                           "backup). Usually a few minutes and well under $1."))
        else:
            q.update(title=f"Allow Regent to {(op.goal if op else hi.required_action).rstrip('.').lower()}?",
                     body=hi.reason)
        q["actions"] = [{"id": "approve", "label": "Yes, go ahead", "primary": True},
                        {"id": "deny", "label": "No"}]
        return q
    failed_build = _failed_build(db, hi.mission_id)
    route = db.get(Route, op.route_id) if op is not None and op.route_id else None
    fields = [{"name": k, "label": v.get("label") or k, "secret": v.get("type") == "secret" or "secret" in k.lower()
               or "token" in k.lower() or "key" in k.lower()}
              for k, v in (hi.response_schema or {}).items() if isinstance(v, dict) and v.get("type") != "boolean"]
    body = hi.reason if hi.reason and hi.reason != hi.required_action else ""
    if route is not None and route.archetype == "use_existing":
        th = route.thesis or ""
        privacy = th.split("Privacy: ", 1)[1].split(" From then on", 1)[0].strip() if "Privacy: " in th else ""
        name = route.title.replace("Use ", "", 1)
        body = (f"Use {name}: create an account (a few minutes), then do it there yourself — Regent can't act "
                f"inside {name} for you." + (f"\n\nWhere your data would live: {privacy}" if privacy else ""))
    elif route is not None and route.thesis:
        body = route.thesis
    if failed_build:
        body = (f"Building an app for this didn't work out ({failed_build}). Another way is below — or Regent can try "
                "building it again.\n\n" + body)
    q.update(title=hi.required_action, body=body.strip(), fields=fields)
    q["actions"] = ([{"id": "submit", "label": "Save", "primary": True}] if fields else
                    [{"id": "done", "label": "Done", "primary": True}])
    if failed_build:
        q["actions"].append({"id": "retry_item", "label": "Try building it again"})
    q["actions"].append({"id": "decline", "label": "I won't do this"})
    return q


def _failed_build(db: Session, mission_id: str) -> str | None:
    """Why the app Regent had built for this was not handed over, in a few words (None if it was not)."""
    for o in db.scalars(select(Operation).where(Operation.mission_id == mission_id, Operation.tool == "software",
                                                Operation.action.in_(("build_app", "extend_app")),
                                                Operation.status == "failed")):
        out = o.outputs or {}
        rounds = out.get("rounds") or 0
        last = (out.get("failures_by_round") or [[]])[-1]
        what = "one of its own checks kept failing" if len(last) == 1 else f"{len(last)} of its own checks kept failing"
        return f"after {rounds} attempts {what}, so Regent did not hand it to you" if rounds else "it could not be built"
    return None


# ------------------------------------------------------------------------ one request


def _result(db: Session, m: Mission) -> dict[str, Any] | None:
    said = (m.attrs or {}).get("reply")
    if said and said.get("text"):
        return {"kind": "reply", "text": said["text"][:8000], "unsure": said.get("unsure") or []}
    kept = (m.attrs or {}).get("remembered")
    if kept:
        return {"kind": "remembered", "note": kept.get("note"), "date": kept.get("date_local"),
                "time": kept.get("time_local"), "remind": kept.get("remind"), "problem": kept.get("remind_problem")}
    from regent.software import capability as K
    from regent.software.tables import SwCapability

    from regent import reminders as RM

    attrs = m.attrs or {}
    need = attrs.get("need") or {}
    rems = RM.for_mission(db, m.id)
    if rems:
        return {"kind": "reminder", "items": [{"when": RM.describe(r), "text": r.text, "active": r.active}
                                              for r in rems]}
    # an answer it keeps (or an app it runs) for this request -- its own, or one it reused
    caps = list(db.scalars(select(SwCapability).where(SwCapability.mission_id == m.id,
                                                      SwCapability.status.in_(("usable", "degraded")))))
    used = [x for x in (need.get("reuse") or [])]
    if not caps and used:
        c = db.get(SwCapability, used[0]["id"])
        caps = [c] if c is not None else []
    # what the person asked to be done, done through an app
    for o in db.scalars(select(Operation).where(Operation.mission_id == m.id, Operation.tool == "software",
                                                Operation.action == "use_app").order_by(Operation.created_at.desc())):
        out = o.outputs or {}
        if out.get("passed") and out.get("report"):
            return {"kind": "done_in_app", "text": out["report"], "app": _app(caps[0]) if caps else None}
        if out.get("passed") is False:
            return {"kind": "problem", "text": "Regent tried to do this in your app, but checking afterwards showed "
                                               "it did not take effect. It will try another way."}
    if caps:
        c = caps[0]
        if c.implementation == "application":
            return {"kind": "app", "app": _app(c)}
        r = K.read(db, c, count_use=False)
        metrics = r.get("metrics", [])
        head = min((x for x in metrics if x.get("headline")), key=lambda x: x["headline"], default=None)
        others = [x for x in metrics if x is not head and x.get("value") is not None][:5]
        return {"kind": "answer", "view": f"/software/{c.slug}", "keeps_current": (c.spec or {}).get("refresh_s"),
                "headline": _metric(head) if head else None, "also": [_metric(x) for x in others],
                "unknown": [u.get("question") for u in r.get("unanswered", [])][:3],
                "reused": bool(used) and c.mission_id != m.id}
    # housing and other acquisition work: the shortlist it wrote
    for o in db.scalars(select(Operation).where(Operation.mission_id == m.id).order_by(Operation.created_at.desc())):
        brief = (o.outputs or {}).get("brief_markdown")
        if brief and "shortlist" in brief.lower() and len(brief) > 120:
            return {"kind": "brief", "text": brief[:6000]}
    return None


def _metric(x: dict[str, Any]) -> dict[str, Any]:
    return {"label": x.get("label"), "value": x.get("display") if x.get("value") is not None else None,
            "status": x.get("status"), "why": x.get("why") or x.get("definition"), "form": x.get("form")}


def _app(c) -> dict[str, Any]:
    from regent.software import appservice as S
    from regent.software.appcap import app_slug

    app = (c.spec or {}).get("app") or {}
    svc = S.get(app_slug(c), "live")
    from regent.api.appgate import open_url

    return {"name": c.title, "url": open_url(app_slug(c), (svc.url if svc else None) or app.get("url")),
            "version": app.get("version"),
            "running": bool(svc and svc.healthy()), "can": [r.get("capability") for r in (c.spec or {}).get(
                "requirements", [])][:8], "passphrase": bool(app.get("credential")), "capability": c.id}


def _item(db: Session, m: Mission, open_q: list[dict[str, Any]]) -> dict[str, Any]:
    attrs = m.attrs or {}
    ops = list(db.scalars(select(Operation).where(Operation.mission_id == m.id).order_by(Operation.created_at)))
    running = [o for o in ops if o.status in ("running", "pending", "ready", "waiting_human")]
    mine = [q for q in open_q if q["item"] == m.id]
    result = _result(db, m)
    did = [DONE.get(f"{o.tool}.{o.action}") for o in ops if o.status in ("succeeded", "verified", "unverified")]
    did = [x for i, x in enumerate(did) if x and x not in did[:i]]
    failed = [o for o in ops if o.status == "failed"]
    if attrs.get("unsupported"):
        state, now = "cannot", attrs["unsupported"]["why"]
    elif attrs.get("route") == "pending" and not attrs.get("paused"):
        state, now = "working", "Working out what to do with this"
    elif (attrs.get("reply") or {}).get("partial"):
        state, now = "working", "Answering…"
    elif attrs.get("paused"):
        state, now = "paused", "Waiting to try again: " + attrs["paused"]["why"]
    elif mine:
        state, now = "needs_you", "Waiting for you (below)"
    elif m.status == "completed" or (m.status == "monitoring" and result):
        state = "done"
        now = ("Keeping watch — you'll hear from Regent only when something changes"
               if attrs.get("mode") == "watch" else
               "Done — and it keeps this current" if result and result.get("kind") == "answer"
               and result.get("keeps_current") else "Done")
    elif m.status == "abandoned":
        state, now = "stopped", "Stopped"
    elif running:
        o = running[-1]
        state, now = "working", DOING.get(f"{o.tool}.{o.action}") or o.goal
    elif failed and not result:
        state, now = "stuck", "Hit a problem and is looking for another way"
    else:
        state, now = "working", "Thinking about how best to do this"
    why = None
    if m.selected_route_id:
        r = db.get(Route, m.selected_route_id)
        alts = db.scalar(select(Route.id).where(Route.mission_id == m.id, Route.id != m.selected_route_id).limit(1))
        if r is not None:
            why = {"chose": r.title, "because": r.thesis, "had_alternatives": alts is not None}
    problem = None
    if failed and state in ("stuck", "working", "needs_you"):
        problem = _plain_error(failed[-1].error)
    fb = _failed_build(db, m.id)
    if fb and not (result and result.get("kind") in ("app", "done_in_app")):
        problem = f"The app Regent had built didn't pass its checks: {fb}."
    return {"id": m.id, "asked": m.objective or m.title, "state": state, "now": now, "result": result,
            "did": did[-8:], "why": why, "problem": problem, "updated": _ago(m.updated_at),
            "started": _ago(m.created_at)}


def _plain_error(err: str | None) -> str | None:
    if not err:
        return None
    e = err.lower()
    if "session limit" in e or "rate limit" in e or "usage limit" in e:
        return "Regent's reasoning service hit its usage limit; it will continue when the limit resets."
    if "timeout" in e:
        return "Something took too long and was stopped."
    if "verification fail" in e:
        return "A step finished but did not pass Regent's own check, so it was not accepted."
    return None


# --------------------------------------------------------------------------- the page data


@router.get("/api/home")
def home(db: Session = Depends(get_db)):
    from regent.software.reasoner import LAST_FAILURE

    open_hi = list(db.scalars(select(HumanInterrupt).where(HumanInterrupt.status == "open")
                              .order_by(HumanInterrupt.created_at)))
    questions = [_question(db, h) for h in open_hi]
    missions = list(db.scalars(select(Mission).where(Mission.parent_id.is_(None))
                               .order_by(Mission.created_at.desc()).limit(40)))
    items = [_item(db, m, questions) for m in missions if not (m.attrs or {}).get("hidden")]
    order = {"needs_you": 0, "working": 1, "paused": 1, "stuck": 1, "done": 2, "cannot": 3, "stopped": 4}
    items.sort(key=lambda x: order.get(x["state"], 5))
    shown = {m.id: m for m in missions if not (m.attrs or {}).get("hidden")}
    messages = []
    for e in db.scalars(select(Event).where(Event.type == "principal_notified").order_by(Event.created_at.desc())
                        .limit(20)):
        text = (e.payload or {}).get("text") or ""
        if not text or e.mission_id not in shown:
            continue
        # the message is its first clause (the verdict or the news); the details are on the request
        messages.append({"id": e.seq if hasattr(e, "seq") else str(e.id), "text": text.split(" — ")[0].strip()[:240],
                         "when": _ago(e.created_at),
                         "about": (shown[e.mission_id].objective or "")[:90], "item": e.mission_id})
        if len(messages) >= 5:
            break
    notice = None
    if shutil.which("claude") is None:
        notice = ("Regent needs Claude Code (the `claude` command) to understand requests. Install it and sign in, "
                  "then restart Regent.")
    elif os.environ.get("REGENT_BACKUP_URL") and not any(
            os.environ.get(k) for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")):
        notice = ("Regent is running, but it can't understand requests yet: it has no Claude key. Add one "
                  "(ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN) and deploy again.")
    elif LAST_FAILURE and time.time() - LAST_FAILURE.get("at", 0) < 1800:
        notice = _plain_error(LAST_FAILURE.get("error")) or "Regent's reasoning service is not answering right now."
    from regent.api import app as appmod

    if appmod._loop is not None and appmod._loop.last_error:
        notice = notice or "Something inside Regent failed on its last pass; it keeps retrying."
    return {"questions": questions, "items": items, "messages": messages, "notice": notice,
            "busy": any(i["state"] == "working" for i in items)}


class IntentIn(BaseModel):
    text: str = Field(min_length=2, max_length=2000)
    timezone: str | None = None


@router.post("/api/intent")
def intent(body: IntentIn, bg: BackgroundTasks, db: Session = Depends(get_db)):
    from regent.core.goals.missions import MissionGraph

    text = body.text.strip()
    m = MissionGraph(db).create(title=text, objective=text,
                                attrs={"route": "pending", **({"timezone": body.timezone} if body.timezone else {})})
    db.commit()
    bg.add_task(_route_now, m.id)
    _kick(bg)
    return {"id": m.id}


def _route_now(mission_id: str) -> None:
    """Decide at once what the sentence should make happen; answers and things to remember finish here."""
    from regent import db as dbm
    from regent.software.router import route_mission

    with dbm.session() as s:
        m = s.get(Mission, mission_id)
        if m is not None:
            route_mission(s, m, stream=True)
            s.commit()


class AnswerIn(BaseModel):
    action: str
    values: dict[str, Any] = {}


@router.post("/api/questions/{qid}")
def answer(qid: str, body: AnswerIn, bg: BackgroundTasks, db: Session = Depends(get_db)):
    from regent.core.human.interrupts import HumanInterruptManager
    from regent.runtime import get_services

    hi = db.get(HumanInterrupt, qid)
    if hi is None or hi.status != "open":
        raise HTTPException(404, "already answered")
    mgr = HumanInterruptManager(db, get_services())
    started = hi.created_at
    seconds = max(1, min(int(time.time() - started.timestamp()), hi.estimated_time_seconds or 60)) if started else None
    if body.action == "retry_item":
        db.commit()
        return retry(hi.mission_id, bg, db)
    if body.action in ("approve", "deny"):
        mgr.resolve(qid, {"approve": body.action == "approve", "active_seconds": 10})
    elif body.action == "decline":
        mgr.resolve(qid, {"declined": True, "active_seconds": 5}, resolution="declined")
        op = db.get(Operation, hi.operation_id) if hi.operation_id else None
        if op is not None:
            op.status, op.error = "failed", "the person declined this step"
    else:
        mgr.resolve(qid, {**body.values, "done": True, "active_seconds": seconds})
    db.commit()
    _kick(bg)
    return {"ok": True}


@router.post("/api/items/{mid}/stop")
def stop(mid: str, db: Session = Depends(get_db)):
    m = db.get(Mission, mid)
    if m is None:
        raise HTTPException(404)
    m.status = "abandoned"
    for hi in db.scalars(select(HumanInterrupt).where(HumanInterrupt.mission_id == mid,
                                                      HumanInterrupt.status == "open")):
        hi.status, hi.resolution = "cancelled", "stopped by the person"
    _silence(db, mid)
    return {"ok": True}


def _silence(db: Session, mid: str) -> None:
    """A request the person stopped or removed never speaks up again: its reminders are cancelled."""
    from regent import reminders as RM

    for r in RM.for_mission(db, mid):
        r.active = False


@router.post("/api/items/{mid}/retry")
def retry(mid: str, bg: BackgroundTasks, db: Session = Depends(get_db)):
    """Start the request over: what it produced last time is set aside (kept, not deleted) so the
    new attempt looks at the world again instead of reusing it."""
    from regent.core.goals.missions import MissionGraph
    from regent.software.tables import SwCapability

    m = db.get(Mission, mid)
    if m is None:
        raise HTTPException(404)
    for c in db.scalars(select(SwCapability).where(SwCapability.mission_id == mid,
                                                   SwCapability.implementation != "application")):
        c.status = "retired"          # applications keep running: they hold the person's data
    for hi in db.scalars(select(HumanInterrupt).where(HumanInterrupt.mission_id == mid,
                                                      HumanInterrupt.status == "open")):
        hi.status, hi.resolution = "cancelled", "started over"
    m.attrs = {**(m.attrs or {}), "hidden": True, "replaced": True}
    if m.status not in ("completed",):
        m.status = "abandoned"
    text = m.objective or m.title
    from regent.software.reasoner import FRESH

    tz = (m.attrs or {}).get("timezone")
    new = MissionGraph(db).create(title=text, objective=text,
                                  attrs={"route": "pending", **({"timezone": tz} if tz else {})})
    new.attrs = {**(new.attrs or {}), "fresh": True}
    FRESH.add(new.id)
    db.commit()
    bg.add_task(_route_now, new.id)
    _kick(bg)
    return {"id": new.id}


@router.post("/api/items/{mid}/remove")
def remove(mid: str, db: Session = Depends(get_db)):
    m = db.get(Mission, mid)
    if m is None:
        raise HTTPException(404)
    m.attrs = {**(m.attrs or {}), "hidden": True}
    if m.status not in ("completed", "monitoring"):
        m.status = "abandoned"
    _silence(db, mid)
    return {"ok": True}


@router.get("/api/apps/{cap_id}/passphrase")
def passphrase(cap_id: str, db: Session = Depends(get_db)):
    """The sign-in passphrase of an app Regent built for you (only for someone already let into Regent)."""
    from regent.software import secrets
    from regent.software.tables import SwCapability

    c = db.get(SwCapability, cap_id)
    cred = ((c.spec or {}).get("app") or {}).get("credential") if c else None
    if not cred:
        raise HTTPException(404)
    return {"passphrase": secrets.get(cred)}


@router.post("/api/apps/{cap_id}/start")
def start_app(cap_id: str, db: Session = Depends(get_db)):
    from regent.software import appcap
    from regent.software.tables import SwCapability

    c = db.get(SwCapability, cap_id)
    if c is None:
        raise HTTPException(404)
    from regent.api.appgate import open_url

    svc = appcap.live(c)
    return {"url": open_url(appcap.app_slug(c), svc.url)}
