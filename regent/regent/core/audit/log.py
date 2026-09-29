"""Audit / Decision Log: autonomy without opacity.

Every meaningful decision records the state snapshot it was made against,
the routes considered, the selection, the reason, the model/tool outputs used,
the evidence, the authority decision and (later) the outcome.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.core.observe.events import EventStore
from regent.db import Decision, ModelCall
from regent.ids import new_id


class DecisionLog:
    def __init__(self, db: Session):
        self.db = db

    def record(self, *, mission_id: str | None, kind: str, summary: str, tick: int = 0,
               snapshot_id: str | None = None, routes_considered: list[dict] | None = None,
               selected_route_id: str | None = None, previous_route_id: str | None = None,
               rationale: dict[str, Any] | None = None, evidence_ids: list[str] | None = None,
               model_outputs: list[dict] | None = None, authority: dict[str, Any] | None = None,
               outcome: dict[str, Any] | None = None) -> Decision:
        d = Decision(id=new_id("dec"), mission_id=mission_id, kind=kind, tick=tick, snapshot_id=snapshot_id,
                     event_seq=EventStore(self.db).head(), summary=summary,
                     routes_considered=routes_considered or [], selected_route_id=selected_route_id,
                     previous_route_id=previous_route_id, rationale=rationale or {},
                     evidence_ids=evidence_ids or [], model_outputs=model_outputs or [],
                     authority=authority or {}, outcome=outcome or {})
        self.db.add(d)
        self.db.flush()
        return d

    def set_outcome(self, decision_id: str, outcome: dict[str, Any]) -> None:
        d = self.db.get(Decision, decision_id)
        if d is not None:
            d.outcome = {**(d.outcome or {}), **outcome}

    def for_mission(self, mission_id: str, limit: int = 100) -> list[Decision]:
        return list(self.db.scalars(select(Decision).where(Decision.mission_id == mission_id)
                                    .order_by(Decision.created_at.desc()).limit(limit)))

    def record_model_calls(self, calls: list[dict[str, Any]]) -> list[ModelCall]:
        out = []
        for c in calls:
            mc = ModelCall(id=new_id("mc"), provider=c["provider"], model=c["model"], task=c["task"],
                           mission_id=c.get("mission_id"), ok=c.get("ok", True), error=c.get("error"),
                           latency_ms=c.get("latency_ms", 0), cost_usd=c.get("cost_usd", 0) if c.get("ok") else 0,
                           output_summary=c.get("output_summary", ""))
            self.db.add(mc)
            out.append(mc)
        self.db.flush()
        return out
