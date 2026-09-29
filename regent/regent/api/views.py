"""Read models for the cockpit: dense, structured, explanation-first."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.core.constitution.model import ConstitutionModel
from regent.core.goals.missions import MissionGraph
from regent.core.human.interrupts import HumanInterruptManager
from regent.core.observe.events import EventStore
from regent.core.treasury.treasury import Treasury
from regent.core.world.state import WorldView, diff_states
from regent.db import (
    Capability,
    Decision,
    Event,
    Evidence,
    HumanInterrupt,
    Mission,
    ModelCall,
    Operation,
    Route,
    RouteScore,
    Skill,
    Snapshot,
)


def describe_event(e: Event) -> str:
    p = e.payload or {}
    t = e.type
    if t == "email_received":
        return f"Message from {((p.get('from') or {}).get('name') or '?')}: “{p.get('subject', '')}”"
    if t == "message_sent":
        return f"Sent reply {p.get('message_id')} to {', '.join(p.get('to') or [])}"
    if t in ("fact_observed", "new_fact_discovered"):
        facts = p.get("facts") or [p]
        return "Learned " + "; ".join(f"{f.get('key')} = {f.get('value')!r}" for f in facts[:4])
    if t == "calendar_event_changed":
        return f"Calendar: {p.get('title', p.get('id'))} ({p.get('status', 'updated')})"
    if t == "user_moved":
        return f"Location: {p.get('place_id') or p.get('place_name')}"
    if t in ("budget_changed", "resource_changed"):
        return f"Resource {p.get('id')}: " + (f"{p['delta']:+,.0f}" if "delta" in p else f"balance {p.get('balance')}")
    if t == "resource_spent":
        return f"Spent {p.get('amount')} from {p.get('resource_id')} ({p.get('reason', '')[:60]})"
    if t == "entity_upserted":
        return f"{p.get('kind', 'entity').title()}: {p.get('name', p.get('id'))}"
    if t == "plan_changed":
        return f"Plan changed: {p.get('previous')} → {p.get('selected')}"
    if t == "route_selected":
        return f"Route selected: {p.get('selected')}"
    if t == "human_interrupt_raised":
        return f"Needs you: {p.get('required_action')}"
    if t == "human_completed_action":
        return f"Human action {p.get('interrupt_id')} {p.get('resolution')}"
    if t == "tool_succeeded":
        return f"{p.get('tool')}.{p.get('action')} succeeded" + (" (degraded backend)" if p.get("degraded") else "")
    if t == "tool_failed":
        return f"{p.get('tool')}.{p.get('action')} {p.get('status')}: {(p.get('error') or '')[:80]}"
    if t == "operation_verified":
        return f"Verified {p.get('operation_id')}: {p.get('verdict')} ({p.get('method')})"
    if t == "capability_changed":
        return f"Capability {p.get('id')}: {p.get('status', 'updated')}"
    if t == "permission_changed":
        return f"Authority {p.get('scope', p.get('id'))}: {'granted' if p.get('granted', True) else 'revoked'}"
    if t == "constitution_item_upserted":
        return f"Constitution: {p.get('statement') or p.get('id')}"
    if t == "skill_published":
        return f"Skill learned ({p.get('domain')}): {p.get('name')}"
    if t == "route_invalidated":
        return f"Route invalidated: {p.get('key')} ({p.get('reason')})"
    if t == "operation_rerouted":
        return f"Rerouted {p.get('from')} → {p.get('to')}: {(p.get('reason') or '')[:60]}"
    if t == "mission_status_changed":
        return f"Mission {p.get('mission_id')}: {p.get('from')} → {p.get('to')}"
    return t.replace("_", " ")


WORLD_EVENT_TYPES = {"email_received", "message_sent", "fact_observed", "new_fact_discovered",
                     "calendar_event_changed", "user_moved", "budget_changed", "resource_changed", "price_changed",
                     "entity_upserted", "human_completed_action", "capability_changed", "permission_changed",
                     "skill_published", "resource_spent"}


def event_dict(e: Event) -> dict[str, Any]:
    return {"seq": e.seq, "id": e.id, "type": e.type, "source": e.source, "mission_id": e.mission_id,
            "at": e.created_at.isoformat() if e.created_at else None, "text": describe_event(e),
            "payload": e.payload}


def op_dict(o: Operation) -> dict[str, Any]:
    d = o.to_dict()
    d["outputs"] = {k: v for k, v in (o.outputs or {}).items() if k not in ("output", "trace", "_facts")}
    return d


def route_dict(r: Route, ops: list[Operation] | None = None) -> dict[str, Any]:
    d = r.to_dict()
    if ops is not None:
        d["operations"] = [op_dict(o) for o in ops]
    return d


def interrupt_dict(h: HumanInterrupt, api_url: str = "") -> dict[str, Any]:
    d = h.to_dict()
    return d


def cockpit(db: Session, mission_id: str) -> dict[str, Any]:
    graph = MissionGraph(db)
    m = graph.get(mission_id)
    if m is None:
        raise KeyError(mission_id)
    world = WorldView.load(db)
    routes = list(db.scalars(select(Route).where(Route.mission_id == m.id)))
    ops = list(db.scalars(select(Operation).where(Operation.mission_id == m.id)
                          .order_by(Operation.created_at)))
    ops_by_route: dict[str, list[Operation]] = defaultdict(list)
    for o in ops:
        ops_by_route[o.route_id or ""].append(o)
    selected = next((r for r in routes if r.id == m.selected_route_id), None)
    ranked = sorted([r for r in routes if r.status not in ("abandoned",)],
                    key=lambda r: (r.status == "invalidated", r.rank or 99))
    decisions = list(db.scalars(select(Decision).where(Decision.mission_id == m.id)
                                .order_by(Decision.created_at.desc()).limit(60)))
    strategy_decisions = [d for d in decisions if d.kind in ("route_selected", "plan_changed", "plan_kept", "no_route")]
    last_change = next((d for d in decisions if d.kind in ("plan_changed", "route_selected")), None)

    # NOW: world events since the previous strategy decision, and a structural diff
    since_seq = 0
    prev_change = [d for d in decisions if d.kind in ("plan_changed", "route_selected")]
    if len(prev_change) >= 1:
        since_seq = prev_change[0].event_seq
    recent = [e for e in EventStore(db).recent(250) if e.type in WORLD_EVENT_TYPES]
    world_diff: dict[str, Any] = {}
    snaps = list(db.scalars(select(Snapshot).order_by(Snapshot.event_seq.desc()).limit(1)))
    if last_change is not None and last_change.snapshot_id and snaps:
        a = db.get(Snapshot, last_change.snapshot_id)
        if a is not None and a.id != snaps[0].id:
            world_diff = _compact_diff(diff_states(a.state, snaps[0].state))

    tree_ids = _tree_ids(graph, m.id)
    interrupts = [h.to_dict() for h in db.scalars(select(HumanInterrupt).where(HumanInterrupt.mission_id.in_(tree_ids))
                                                  .order_by(HumanInterrupt.created_at.desc()).limit(20))]
    history: dict[str, list] = defaultdict(list)
    for rs in db.scalars(select(RouteScore).where(RouteScore.mission_id == m.id).order_by(RouteScore.created_at)):
        history[rs.route_id].append({"tick": rs.tick, "score": round(rs.score, 4), "rank": rs.rank})

    children = []
    for c in graph.children(m.id):
        cs = db.get(Route, c.selected_route_id) if c.selected_route_id else None
        children.append({"id": c.id, "title": c.title, "status": c.status, "tags": c.tags,
                         "selected_route": cs.title if cs else None,
                         "criteria": graph.criteria_status(c, world)})
    parent = graph.get(m.parent_id) if m.parent_id else None
    evidence = list(db.scalars(select(Evidence).where(Evidence.mission_id == m.id)
                               .order_by(Evidence.created_at.desc()).limit(30)))
    tr = Treasury(db)
    return {
        "mission": {**m.to_dict(), "criteria": graph.criteria_status(m, world), "children": children,
                    "parent": {"id": parent.id, "title": parent.title} if parent else None},
        "now": {"events": [event_dict(e) for e in recent[:40]], "since_seq": since_seq,
                "new_since_last_strategy": sum(1 for e in recent if e.seq > since_seq),
                "world_diff": world_diff},
        "best_route": route_dict(selected, ops_by_route.get(selected.id, [])) if selected else None,
        "why": {
            "last_decision": last_change.to_dict() if last_change else None,
            "evaluation": (m.attrs or {}).get("evaluation_context"),
            "uncertainties": (m.attrs or {}).get("uncertainties", []),
        },
        "executing": {
            "phase": m.phase, "status": m.status, "last_tick": (m.attrs or {}).get("last_tick"),
            "operations": [op_dict(o) for o in sorted(ops, key=lambda o: (o.started_at is None, o.created_at),
                                                      reverse=True)[:40]],
        },
        "blocked_by_you": [i for i in interrupts if i["status"] == "open"],
        "interrupt_history": [i for i in interrupts if i["status"] != "open"][:8],
        "alternatives": [route_dict(r, ops_by_route.get(r.id, [])) for r in ranked if r.id != m.selected_route_id],
        "score_history": history,
        "changes": [d.to_dict() for d in strategy_decisions[:20]],
        "decisions": [d.to_dict() for d in decisions[:40]],
        "evidence": [e.to_dict() for e in evidence],
        "world": world_panel(db, world),
        "treasury": {"resources": [r.to_dict() for r in tr.resources()], "scarcity": tr.scarcity(),
                     "ledger": [e.to_dict() for e in tr.ledger(15)]},
        "constitution": ConstitutionModel(db).grouped(),
        "model_calls": [c.to_dict() for c in db.scalars(select(ModelCall).where(ModelCall.mission_id.in_(tree_ids))
                                                        .order_by(ModelCall.created_at.desc()).limit(15))],
    }


def world_panel(db: Session, world: WorldView) -> dict[str, Any]:
    kinds = ("contract", "commitment", "event", "project", "service", "place", "person", "organization", "message")
    ents = [{"id": e.id, "kind": e.kind, "name": e.name, "attrs": e.attrs} for e in world.entities.values()
            if e.kind in kinds]
    facts = [{"key": f.key, "value": f.value, "confidence": f.confidence, "evidence_id": f.evidence_id,
              "source": f.source, "updated_at": f.updated_at.isoformat() if f.updated_at else None}
             for f in sorted(world.facts.values(), key=lambda f: -(f.last_event_seq or 0))
             if not f.key.startswith("tool.")]
    return {
        "entities": sorted(ents, key=lambda e: (kinds.index(e["kind"]), e["id"])),
        "unanswered": [{"id": m.id, "subject": m.attrs.get("subject"),
                        "from": (world.entities.get(m.attrs.get("from") or "") or m).name if m.attrs.get("from") in world.entities else m.attrs.get("from"),
                        "deadline": m.attrs.get("deadline")} for m in world.unanswered_messages()],
        "facts": facts[:60],
        "capabilities": [c.to_dict() for c in db.scalars(select(Capability).order_by(Capability.status, Capability.id))],
        "skills": [s.to_dict() for s in db.scalars(select(Skill).order_by(Skill.created_at.desc()).limit(10))],
        "relations": len(world.relations),
        "event_seq": world.event_seq,
        "runway_months": world.runway_months(),
    }


def _tree_ids(graph: MissionGraph, mid: str) -> list[str]:
    out = [mid]
    for c in graph.children(mid):
        out += _tree_ids(graph, c.id)
    return out


def _compact_diff(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for table, ch in d.items():
        if table == "facts":
            ch = {k: [x for x in v if not str(x.get("key", "")).startswith("tool.")] for k, v in ch.items()}
        out[table] = {
            "added": [x.get("key") or x.get("id") for x in ch["added"]][:20],
            "removed": [x.get("key") or x.get("id") for x in ch["removed"]][:20],
            "changed": [{"id": c.get("key") or c.get("id"),
                         "fields": {k: v for k, v in c["fields"].items() if k in ("value", "balance", "status", "attrs", "confidence")}}
                        for c in ch["changed"]][:20],
        }
    return out


def mission_list(db: Session) -> list[dict[str, Any]]:
    out = []
    for m in db.scalars(select(Mission).order_by(Mission.created_at)):
        out.append({"id": m.id, "title": m.title, "status": m.status, "parent_id": m.parent_id, "tags": m.tags,
                    "selected_route_id": m.selected_route_id, "tick_count": m.tick_count,
                    "open_interrupts": len(HumanInterruptManager(db).open(m.id))})
    return out
