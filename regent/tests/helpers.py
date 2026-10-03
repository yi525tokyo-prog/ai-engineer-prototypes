"""Shared builders for tests."""

from __future__ import annotations

from regent.core.goals.missions import MissionGraph
from regent.core.observe.events import EventStore
from regent.db import Route
from regent.ids import new_id
from regent.schemas import RouteProposal


def world_with_cash(db, balance=180000, burn=210000):
    EventStore(db).append("resource_changed", {"id": "cash", "kind": "money", "name": "Cash", "unit": "JPY",
                                               "balance": balance, "attrs": {"monthly_burn": burn}})


def mission(db, **kw):
    kw.setdefault("title", "Test mission")
    kw.setdefault("objective", "Test objective")
    kw.setdefault("value_scale", 100.0)
    return MissionGraph(db).create(**kw)


def route_from(db, m, prop: RouteProposal, status="alive") -> Route:
    r = Route(id=new_id("rt"), mission_id=m.id, key=prop.key, title=prop.title, thesis=prop.thesis,
              archetype=prop.archetype, generated_by=["test"], status=status,
              estimates=prop.estimates.model_dump(), effective={}, estimate_sources={},
              sensitivities=[s.model_dump() for s in prop.sensitivities],
              blockers=[b.model_dump() for b in prop.blockers], required_capabilities=prop.required_capabilities,
              operation_specs=[o.model_dump() for o in prop.operations], tags=prop.tags, uncertainty=[],
              critiques=[], evidence_ids=[])
    db.add(r)
    db.flush()
    return r


def prop(key, **est) -> RouteProposal:
    tags = est.pop("tags", [])
    sens = est.pop("sensitivities", [])
    blockers = est.pop("blockers", [])
    caps = est.pop("required_capabilities", [])
    ops = est.pop("operations", [])
    return RouteProposal(key=key, title=key.title(), thesis=f"thesis {key}", estimates=est, tags=tags,
                         sensitivities=sens, blockers=blockers, required_capabilities=caps, operations=ops)
