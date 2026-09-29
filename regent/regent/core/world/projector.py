"""Projection of events into current world state.

Everything written here is reconstructable: ``rebuild()`` wipes the projection
tables and replays the event log (optionally starting from a snapshot).
"""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from regent.db import (
    AuthorityGrant,
    Capability,
    ConstitutionItem,
    Entity,
    Event,
    Fact,
    GlobalFact,
    LedgerEntry,
    Relation,
    Resource,
    Skill,
    Snapshot,
)
from regent.ids import new_id, utcnow

ENTITY_KINDS = {
    "person", "organization", "place", "account", "service", "project", "goal",
    "event", "document", "message", "asset", "contract", "commitment",
    "capability", "tool", "operation", "route", "resource", "evidence",
    "decision", "human_interrupt",
}

RELATION_TYPES = {
    "member_of", "owns", "uses", "works_for", "located_at", "depends_on",
    "blocked_by", "has_access_to", "requires", "created_by", "related_to",
    "scheduled_for", "pays", "connected_to", "sent_by", "sent_to", "offers",
    "attends",
}

PROJECTION_TABLES = [
    Entity, Relation, Fact, Resource, LedgerEntry, ConstitutionItem,
    AuthorityGrant, Capability, Skill, GlobalFact,
]


def _upsert_entity(db: Session, ev: Event, eid: str, kind: str, name: str | None,
                   attrs: dict | None, domain: str | None = None, merge: bool = True) -> Entity:
    kind = kind.lower()
    ent = db.get(Entity, eid)
    if ent is None:
        ent = Entity(id=eid, kind=kind, name=name or eid, attrs=dict(attrs or {}),
                     domain=domain or ev.domain, version=1, last_event_seq=ev.seq,
                     created_at=ev.created_at or utcnow(), updated_at=ev.created_at or utcnow())
        db.add(ent)
    else:
        new_attrs = dict(ent.attrs or {}) if merge else {}
        new_attrs.update(attrs or {})
        ent.attrs = new_attrs
        if name:
            ent.name = name
        ent.kind = kind or ent.kind
        ent.version = (ent.version or 0) + 1
        ent.last_event_seq = ev.seq
        ent.updated_at = ev.created_at or utcnow()
    return ent


def _add_relation(db: Session, ev: Event, src: str, rel: str, dst: str, attrs: dict | None = None,
                  rid: str | None = None) -> Relation:
    rid = rid or f"{src}:{rel}:{dst}"
    r = db.get(Relation, rid)
    if r is None:
        r = Relation(id=rid, src_id=src, rel=rel, dst_id=dst, attrs=attrs or {},
                     domain=ev.domain, last_event_seq=ev.seq, created_at=ev.created_at or utcnow())
        db.add(r)
    else:
        r.attrs = {**(r.attrs or {}), **(attrs or {})}
        r.last_event_seq = ev.seq
    return r


def _set_fact(db: Session, ev: Event, key: str, value: Any, confidence: float = 1.0,
              evidence_id: str | None = None, source: str | None = None) -> Fact:
    f = db.get(Fact, key)
    if f is None:
        f = Fact(key=key)
        db.add(f)
    f.value = value
    f.confidence = float(confidence)
    f.evidence_id = evidence_id
    f.source = source or ev.source
    f.domain = ev.domain
    f.last_event_seq = ev.seq
    f.updated_at = ev.created_at or utcnow()
    return f


# ------------------------------------------------------------------ handlers

def h_entity_upserted(db, ev):
    p = ev.payload
    _upsert_entity(db, ev, p["id"], p.get("kind", "document"), p.get("name"), p.get("attrs"),
                   p.get("domain"), p.get("merge", True))
    for r in p.get("relations", []) or []:
        _add_relation(db, ev, r.get("src", p["id"]), r["rel"], r["dst"], r.get("attrs"))


def h_entity_removed(db, ev):
    eid = ev.payload["id"]
    ent = db.get(Entity, eid)
    if ent is not None:
        db.delete(ent)
    db.execute(delete(Relation).where((Relation.src_id == eid) | (Relation.dst_id == eid)))


def h_relation_added(db, ev):
    p = ev.payload
    _add_relation(db, ev, p["src"], p["rel"], p["dst"], p.get("attrs"), p.get("id"))


def h_relation_removed(db, ev):
    p = ev.payload
    rid = p.get("id") or f"{p['src']}:{p['rel']}:{p['dst']}"
    r = db.get(Relation, rid)
    if r is not None:
        db.delete(r)


def h_fact_observed(db, ev):
    p = ev.payload
    facts = p.get("facts")
    if facts is None:
        facts = [p]
    for f in facts:
        _set_fact(db, ev, f["key"], f.get("value"), f.get("confidence", 1.0),
                  f.get("evidence_id", p.get("evidence_id")), f.get("source"))


def h_email_received(db, ev):
    p = ev.payload
    sender = p.get("from") or {}
    sid = sender.get("id") or f"person_{(sender.get('email') or 'unknown').split('@')[0]}"
    _upsert_entity(db, ev, sid, "person", sender.get("name"),
                   {k: v for k, v in sender.items() if k not in ("id", "name")})
    mid = p.get("id") or new_id("msg")
    attrs = {
        "subject": p.get("subject", ""),
        "body": p.get("body", ""),
        "received_at": p.get("received_at") or (ev.created_at.isoformat() if ev.created_at else None),
        "channel": p.get("channel", "email"),
        "requires_reply": p.get("requires_reply", True),
        "answered": False,
        "from": sid,
        "thread": p.get("thread"),
        "deadline": p.get("deadline"),
    }
    _upsert_entity(db, ev, mid, "message", p.get("subject") or "message", attrs)
    _add_relation(db, ev, mid, "sent_by", sid)
    for rel in p.get("related_to", []) or []:
        _add_relation(db, ev, mid, "related_to", rel)
    _set_fact(db, ev, f"message.{mid}.answered", False)


def h_message_sent(db, ev):
    p = ev.payload
    reply_to = p.get("in_reply_to")
    if reply_to:
        ent = db.get(Entity, reply_to)
        if ent is not None:
            ent.attrs = {**(ent.attrs or {}), "answered": True, "answered_by": p.get("message_id")}
            ent.version += 1
            ent.last_event_seq = ev.seq
        _set_fact(db, ev, f"message.{reply_to}.answered", True)


def h_calendar_event_changed(db, ev):
    p = ev.payload
    eid = p["id"]
    attrs = {k: v for k, v in p.items() if k not in ("id", "title")}
    _upsert_entity(db, ev, eid, "event", p.get("title"), attrs)
    if p.get("location_id"):
        _add_relation(db, ev, eid, "located_at", p["location_id"])
    for a in p.get("attendees", []) or []:
        if isinstance(a, str):
            _add_relation(db, ev, a, "attends", eid)
    if "format" in p:
        _set_fact(db, ev, f"event.{eid}.format", p["format"])
    if "status" in p:
        _set_fact(db, ev, f"event.{eid}.status", p["status"])


def h_user_moved(db, ev):
    p = ev.payload
    _set_fact(db, ev, "user.location", p.get("place_id") or p.get("place_name"))
    if p.get("place_id"):
        for r in db.scalars(select(Relation).where(Relation.src_id == "user", Relation.rel == "located_at")):
            db.delete(r)
        _add_relation(db, ev, "user", "located_at", p["place_id"])


def h_price_changed(db, ev):
    p = ev.payload
    _set_fact(db, ev, f"price.{p['subject']}", {"amount": p["price"], "currency": p.get("currency", "")})


def h_resource_changed(db, ev):
    p = ev.payload
    r = db.get(Resource, p["id"])
    if r is None:
        r = Resource(id=p["id"], kind=p.get("kind", "money"), name=p.get("name", p["id"]),
                     unit=p.get("unit", ""), balance=0.0, attrs={})
        db.add(r)
    for k in ("kind", "name", "unit", "limit"):
        if k in p:
            setattr(r, k, p[k])
    if "balance" in p:
        r.balance = float(p["balance"])
    if "delta" in p:
        r.balance = float(r.balance or 0) + float(p["delta"])
    if "attrs" in p:
        r.attrs = {**(r.attrs or {}), **p["attrs"]}
    r.last_event_seq = ev.seq
    r.updated_at = ev.created_at or utcnow()


def h_resource_spent(db, ev):
    p = ev.payload
    r = db.get(Resource, p["resource_id"])
    if r is None:
        return
    amount = float(p["amount"])
    r.balance = float(r.balance or 0) - amount
    r.last_event_seq = ev.seq
    db.add(LedgerEntry(id=p.get("id") or new_id("led"), resource_id=r.id, amount=-amount,
                       operation_id=p.get("operation_id"), mission_id=ev.mission_id,
                       reason=p.get("reason", ""), event_seq=ev.seq))


def h_constitution_item_upserted(db, ev):
    p = ev.payload
    item = db.get(ConstitutionItem, p["id"])
    if item is None:
        item = ConstitutionItem(id=p["id"], type=p.get("type", "weak_preference"),
                                statement=p.get("statement", ""), sources=[])
        db.add(item)
    for k in ("type", "statement", "dimension", "direction", "rule", "confidence",
              "alpha", "beta", "status"):
        if k in p:
            setattr(item, k, p[k])
    if "sources" in p:
        item.sources = list(p["sources"])
    if "add_source" in p:
        item.sources = list(item.sources or []) + [p["add_source"]]
    item.last_event_seq = ev.seq
    item.updated_at = ev.created_at or utcnow()


def h_permission_changed(db, ev):
    p = ev.payload
    g = db.get(AuthorityGrant, p["id"])
    if g is None:
        g = AuthorityGrant(id=p["id"], scope=p["scope"], level=p.get("level", "COMMIT"))
        db.add(g)
    for k in ("scope", "level", "granted", "constraints", "note"):
        if k in p:
            setattr(g, k, p[k])
    g.last_event_seq = ev.seq
    g.updated_at = ev.created_at or utcnow()


def h_capability_changed(db, ev):
    p = ev.payload
    c = db.get(Capability, p["id"])
    if c is None:
        c = Capability(id=p["id"], name=p.get("name", p["id"]), provided_by=[], attrs={})
        db.add(c)
    for k in ("name", "description", "status", "provided_by", "acquisition_mission_id"):
        if k in p:
            setattr(c, k, p[k])
    if "attrs" in p:
        c.attrs = {**(c.attrs or {}), **p["attrs"]}
    c.last_event_seq = ev.seq
    c.updated_at = ev.created_at or utcnow()


def h_skill_published(db, ev):
    p = ev.payload
    s = db.get(Skill, p["id"])
    if s is None:
        s = Skill(id=p["id"], name=p.get("name", p["id"]), task_pattern=p.get("task_pattern", ""))
        db.add(s)
    for k in ("domain", "name", "task_pattern", "preconditions", "procedure", "failure_modes",
              "verification", "confidence", "provenance", "uses"):
        if k in p:
            setattr(s, k, p[k])
    s.last_verified = ev.created_at or utcnow()


def h_global_fact_published(db, ev):
    p = ev.payload
    g = db.get(GlobalFact, p["id"])
    if g is None:
        g = GlobalFact(id=p["id"], subject=p["subject"], statement=p.get("statement", ""))
        db.add(g)
    for k in ("domain", "subject", "statement", "value", "confidence", "provenance"):
        if k in p:
            setattr(g, k, p[k])


def h_tool_outcome(db, ev):
    """Learn tool reliability from outcomes (Beta-style running estimate)."""
    tool = ev.payload.get("tool")
    if not tool:
        return
    key = f"tool.{tool}.reliability"
    f = db.get(Fact, key)
    stats = dict(f.value) if f is not None and isinstance(f.value, dict) else {"ok": 0, "fail": 0}
    if ev.type == "tool_succeeded":
        stats["ok"] = stats.get("ok", 0) + 1
    else:
        stats["fail"] = stats.get("fail", 0) + 1
    stats["estimate"] = round((stats["ok"] + 1) / (stats["ok"] + stats["fail"] + 2), 3)
    _set_fact(db, ev, key, stats, source="projector")


HANDLERS: dict[str, Callable[[Session, Event], None]] = {
    "entity_upserted": h_entity_upserted,
    "entity_removed": h_entity_removed,
    "relation_added": h_relation_added,
    "relation_removed": h_relation_removed,
    "fact_observed": h_fact_observed,
    "new_fact_discovered": h_fact_observed,
    "email_received": h_email_received,
    "message_sent": h_message_sent,
    "calendar_event_changed": h_calendar_event_changed,
    "user_moved": h_user_moved,
    "price_changed": h_price_changed,
    "resource_changed": h_resource_changed,
    "budget_changed": h_resource_changed,
    "resource_spent": h_resource_spent,
    "constitution_item_upserted": h_constitution_item_upserted,
    "permission_changed": h_permission_changed,
    "capability_changed": h_capability_changed,
    "skill_published": h_skill_published,
    "global_fact_published": h_global_fact_published,
    "tool_succeeded": h_tool_outcome,
    "tool_failed": h_tool_outcome,
}


def apply_event(db: Session, ev: Event) -> None:
    handler = HANDLERS.get(ev.type)
    if handler is not None:
        handler(db, ev)
        db.flush()


# ------------------------------------------------------------ snapshot / rebuild

def serialize_projection(db: Session) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for model in PROJECTION_TABLES:
        out[model.__tablename__] = [row.to_dict() for row in db.scalars(select(model))]
    return out


def take_snapshot(db: Session, reason: str = "") -> Snapshot:
    from regent.core.observe.events import EventStore

    snap = Snapshot(id=new_id("snap"), event_seq=EventStore(db).head(), reason=reason,
                    state=serialize_projection(db))
    db.add(snap)
    db.flush()
    return snap


def _restore(db: Session, state: dict[str, list[dict]]) -> None:
    from datetime import datetime

    for model in PROJECTION_TABLES:
        cols = {c.key: c for c in model.__table__.columns}
        for row in state.get(model.__tablename__, []):
            kwargs = {}
            for k, v in row.items():
                if k not in cols:
                    continue
                if v is not None and cols[k].type.__class__.__name__ == "DateTime":
                    v = datetime.fromisoformat(v)
                kwargs[k] = v
            db.add(model(**kwargs))
    db.flush()


def rebuild(db: Session, *, from_snapshot: str | None = None) -> int:
    """Wipe projections and rebuild them from the event log.

    If ``from_snapshot`` is given, state is restored from that snapshot and only
    the events after it are replayed. Returns the number of events replayed.
    """
    for model in PROJECTION_TABLES:
        db.execute(delete(model))
    db.flush()
    start = 0
    if from_snapshot:
        snap = db.get(Snapshot, from_snapshot)
        if snap is None:
            raise KeyError(from_snapshot)
        _restore(db, snap.state)
        start = snap.event_seq
    n = 0
    for ev in db.scalars(select(Event).where(Event.seq > start).order_by(Event.seq)):
        apply_event(db, ev)
        n += 1
    db.flush()
    return n
