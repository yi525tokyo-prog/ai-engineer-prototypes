"""Goal / Mission graph.

Missions form a tree: a root objective, sub-goals the principal adds, and
sub-missions Regent spawns itself (e.g. capability acquisition). Success
criteria are fact conditions, so completion is verified against the world
model rather than asserted.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.core.observe.events import EventStore
from regent.core.world.state import WorldView
from regent.db import Mission
from regent.ids import new_id, utcnow
from regent.schemas import Condition


class MissionGraph:
    def __init__(self, db: Session):
        self.db = db
        self.events = EventStore(db)

    def create(self, *, title: str, objective: str, success_criteria: list[dict[str, Any]] | None = None,
               tags: list[str] | None = None, value_scale: float = 1.0, horizon_days: float = 30.0,
               parent_id: str | None = None, attrs: dict[str, Any] | None = None, mission_id: str | None = None,
               source: str = "user") -> Mission:
        if len(title) > 200:       # the title is a label; the full sentence stays in the objective
            title = title[:197].rsplit(" ", 1)[0] + "..."
        m = Mission(id=mission_id or new_id("mis"), parent_id=parent_id, title=title, objective=objective,
                    success_criteria=success_criteria or [], tags=tags or [], value_scale=value_scale,
                    horizon_days=horizon_days, attrs=attrs or {})
        self.db.add(m)
        self.db.flush()
        self.events.append("mission_created", {"mission_id": m.id, "title": title, "parent_id": parent_id,
                                               "tags": tags or []}, source=source, mission_id=m.id)
        if parent_id:
            self.events.append("relation_added", {"src": m.id, "rel": "depends_on", "dst": parent_id},
                               source=source, mission_id=m.id)
        return m

    def get(self, mission_id: str) -> Mission | None:
        return self.db.get(Mission, mission_id)

    def roots(self) -> list[Mission]:
        return list(self.db.scalars(select(Mission).where(Mission.parent_id.is_(None)).order_by(Mission.created_at)))

    def children(self, mission_id: str) -> list[Mission]:
        return list(self.db.scalars(select(Mission).where(Mission.parent_id == mission_id).order_by(Mission.created_at)))

    def active(self) -> list[Mission]:
        return list(self.db.scalars(select(Mission).where(Mission.status.in_(("active", "waiting_human", "monitoring")))
                                    .order_by(Mission.created_at)))

    def tree(self, mission_id: str) -> dict[str, Any]:
        m = self.get(mission_id)
        if m is None:
            return {}
        return {"id": m.id, "title": m.title, "status": m.status, "tags": m.tags,
                "children": [self.tree(c.id) for c in self.children(m.id)]}

    def set_status(self, m: Mission, status: str, reason: str = "") -> None:
        if m.status == status:
            return
        prev = m.status
        m.status = status
        m.updated_at = utcnow()
        self.events.append("mission_status_changed", {"mission_id": m.id, "from": prev, "to": status,
                                                      "reason": reason}, source="regent", mission_id=m.id)

    def criteria_status(self, m: Mission, world: WorldView) -> list[dict[str, Any]]:
        facts, present = world.fact_map()
        out = []
        for c in m.success_criteria or []:
            cond = c.get("condition")
            holds = None
            if cond:
                holds = Condition(**cond).holds(facts, present)
            elif c.get("any"):
                results = [Condition(**x).holds(facts, present) for x in c["any"]]
                holds = True if any(r is True for r in results) else (None if all(r is None for r in results) else False)
            out.append({**c, "met": bool(holds), "known": holds is not None})
        return out

    def is_complete(self, m: Mission, world: WorldView) -> bool:
        crit = self.criteria_status(m, world)
        return bool(crit) and all(c["met"] for c in crit)
