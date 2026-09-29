"""Event store: the append-only source of truth.

Every meaningful change -- an email arriving, a tool failing, a route being
invalidated, a permission change -- is an event. World-state projections are
applied synchronously on append so reads are always consistent with the log.
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from regent.db import Event
from regent.ids import new_id

# Canonical event vocabulary. Unknown types are still stored (and shown in the
# audit trail); they simply have no projection.
EVENT_TYPES = {
    # world
    "entity_upserted", "entity_removed", "relation_added", "relation_removed",
    "fact_observed", "new_fact_discovered", "email_received", "message_sent",
    "calendar_event_changed", "user_moved", "price_changed",
    # resources / governance
    "resource_changed", "budget_changed", "resource_spent",
    "constitution_item_upserted", "permission_changed", "capability_changed",
    "skill_published", "global_fact_published",
    # execution
    "mission_created", "mission_status_changed", "routes_generated", "routes_ranked",
    "route_selected", "plan_changed", "route_invalidated", "operation_planned",
    "operation_started", "tool_succeeded", "tool_failed", "operation_verified",
    "operation_cancelled", "operation_rerouted", "human_interrupt_raised", "human_completed_action",
    "evidence_recorded", "capability_missing", "loop_tick",
}


class EventStore:
    def __init__(self, db: Session):
        self.db = db

    def append(
        self,
        type: str,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "user",
        mission_id: str | None = None,
        domain: str = "private",
        causation_id: str | None = None,
        project: bool = True,
    ) -> Event:
        ev = Event(
            id=new_id("ev"),
            type=type,
            payload=payload or {},
            source=source,
            mission_id=mission_id,
            domain=domain,
            causation_id=causation_id,
        )
        self.db.add(ev)
        self.db.flush()  # assigns seq
        if project:
            from regent.core.world.projector import apply_event

            apply_event(self.db, ev)
        return ev

    def extend(self, events: Iterable[dict[str, Any]], *, source: str = "user") -> list[Event]:
        out = []
        for e in events:
            e = dict(e)
            t = e.pop("type")
            mission_id = e.pop("mission_id", None)
            domain = e.pop("domain", "private")
            src = e.pop("source", source)
            payload = e.pop("payload", e)
            out.append(self.append(t, payload, source=src, mission_id=mission_id, domain=domain))
        return out

    def head(self) -> int:
        return int(self.db.scalar(select(func.coalesce(func.max(Event.seq), 0))) or 0)

    def since(
        self,
        seq: int,
        *,
        mission_id: str | None = None,
        types: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        q = select(Event).where(Event.seq > seq).order_by(Event.seq)
        if mission_id is not None:
            q = q.where((Event.mission_id == mission_id) | (Event.mission_id.is_(None)))
        if types:
            q = q.where(Event.type.in_(list(types)))
        if limit:
            q = q.limit(limit)
        return list(self.db.scalars(q))

    def recent(self, limit: int = 100, mission_id: str | None = None) -> list[Event]:
        q = select(Event).order_by(Event.seq.desc()).limit(limit)
        if mission_id is not None:
            q = q.where((Event.mission_id == mission_id) | (Event.mission_id.is_(None)))
        return list(self.db.scalars(q))

    def get(self, event_id: str) -> Event | None:
        return self.db.scalar(select(Event).where(Event.id == event_id))
