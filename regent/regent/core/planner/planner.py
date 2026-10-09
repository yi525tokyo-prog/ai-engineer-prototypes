"""Planner: plan selection and operation decomposition.

Selection uses hysteresis: an incumbent route is replaced only when it becomes
unselectable or a competitor beats it by ``SWITCH_MARGIN`` -- enough to avoid
thrashing on noise, small enough that evidence moves the plan. Sunk cost is
never an argument: completed work on the incumbent does not enter the score.

Decomposition materializes the selected route's operation specs as
``Operation`` rows. Cheap AUTO probes from *alternative* routes are also
materialized when they resolve decision-relevant uncertainties, so
information is gathered in parallel across the live route set.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.core.authority.manager import AuthorityManager
from regent.core.evaluate.evaluator import RouteEval
from regent.core.observe.events import EventStore
from regent.db import HumanInterrupt, Mission, Operation, Route
from regent.ids import new_id, utcnow
from regent.schemas import OperationSpec

SWITCH_MARGIN = 0.03
PROBE_MAX_API_USD = 0.05
PROBE_MAX_MONEY = 0.0


class Planner:
    def __init__(self, db: Session, services: Any):
        self.db = db
        self.services = services
        self.events = EventStore(db)
        self.authority = AuthorityManager(db, services.tools)

    # ------------------------------------------------------------ select

    def select(self, mission: Mission, evals: list[RouteEval]) -> dict[str, Any]:
        selectable = [e for e in evals if e.selectable]
        current = next((e for e in evals if e.route.id == mission.selected_route_id), None)
        if not selectable:
            return {"changed": current is not None, "selected": None, "previous": current,
                    "reason": "no selectable route", "kind": "no_route"}
        best = selectable[0]
        if current is None:
            return {"changed": True, "selected": best, "previous": None, "kind": "route_selected",
                    "reason": f"initial selection: '{best.route.title}' ranks first ({best.score:.3f})"}
        if not current.selectable:
            why = "; ".join(current.invalid + current.blocked) or "not selectable"
            return {"changed": best.route.id != current.route.id, "selected": best, "previous": current,
                    "kind": "plan_changed", "reason": f"incumbent '{current.route.title}' is no longer viable ({why})"}
        if best.route.id != current.route.id and best.score > current.score + SWITCH_MARGIN:
            return {"changed": True, "selected": best, "previous": current, "kind": "plan_changed",
                    "reason": (f"'{best.route.title}' ({best.score:.3f}) now beats incumbent "
                               f"'{current.route.title}' ({current.score:.3f}) by more than {SWITCH_MARGIN}")}
        reason = "incumbent still best" if best.route.id == current.route.id else (
            f"'{best.route.title}' leads by only {best.score - current.score:.3f} (< {SWITCH_MARGIN}); keeping incumbent")
        return {"changed": False, "selected": current, "previous": current, "kind": "plan_kept", "reason": reason}

    def apply_selection(self, mission: Mission, sel: dict[str, Any]) -> None:
        new = sel["selected"]
        prev = sel["previous"]
        if prev is not None and (new is None or prev.route.id != new.route.id):
            if prev.route.status == "selected":
                prev.route.status = "alive" if prev.selectable else "invalidated"
                if not prev.selectable:
                    prev.route.invalidated_reason = "; ".join(prev.invalid + prev.blocked)
            self.cancel_route_steps(prev.route, reason=f"route deselected: {sel['reason']}")
        if new is not None:
            new.route.status = "selected"
            mission.selected_route_id = new.route.id
        else:
            mission.selected_route_id = None
        mission.updated_at = utcnow()

    def cancel_route_steps(self, route: Route, reason: str) -> list[Operation]:
        """Cancel not-yet-done *steps* of a deselected route. Probes keep running:
        the information they gather still matters to the remaining routes."""
        out = []
        for op in self.db.scalars(select(Operation).where(Operation.route_id == route.id,
                                                          Operation.status.in_(("pending", "waiting_human", "blocked")))):
            if op.kind == "probe":
                continue
            op.status = "cancelled"
            op.error = reason
            out.append(op)
            for hi in self.db.scalars(select(HumanInterrupt).where(HumanInterrupt.operation_id == op.id,
                                                                   HumanInterrupt.status == "open")):
                hi.status = "cancelled"
                hi.resolution = "route_deselected"
                hi.resolved_at = utcnow()
            self.events.append("operation_cancelled", {"operation_id": op.id, "route_id": route.id, "reason": reason},
                               source="regent", mission_id=op.mission_id)
        return out

    # ------------------------------------------------------- decomposition

    def _existing(self, route: Route) -> dict[str, Operation]:
        return {o.key: o for o in self.db.scalars(select(Operation).where(Operation.route_id == route.id))}

    def _make_op(self, mission: Mission, route: Route, spec: OperationSpec, seq: int, priority: float) -> Operation:
        tool = self.services.tools.get(spec.tool)
        executor = tool.executor if tool else ("code" if spec.tool == "invoice" else "api")
        level = self.authority.classify(spec.tool, spec.action, spec.inputs, spec.authority)
        op = Operation(
            id=new_id("op"), mission_id=mission.id, route_id=route.id, key=spec.key, goal=spec.goal, kind=spec.kind,
            executor=executor, tool=spec.tool, action=spec.action, required_authority=level, status="pending",
            inputs=spec.inputs, outputs={}, timeout_s=spec.timeout_s,
            retry_policy={**spec.retry.model_dump(), "fallback_index": -1},
            verification=spec.verification.model_dump(), cost_estimate=spec.cost_estimate.model_dump(),
            depends_on=spec.depends_on, resolves=spec.resolves, emits=spec.emits, sequence=seq, priority=priority,
            attempt_log=[], evidence_ids=[],
        )
        self.db.add(op)
        return op

    def materialize(self, mission: Mission, route: Route) -> list[Operation]:
        existing = self._existing(route)
        created = []
        for i, raw in enumerate(route.operation_specs or []):
            spec = OperationSpec.model_validate(raw)
            old = existing.get(spec.key)
            if old is not None:
                if old.status == "cancelled" and route.status == "selected":
                    old.status, old.error = "pending", None  # route re-selected: revive its steps
                continue
            created.append(self._make_op(mission, route, spec, i, priority=1.0 if spec.kind != "probe" else 1.5))
        self.db.flush()
        for op in created:
            self.events.append("operation_planned", {"operation_id": op.id, "route_id": route.id, "key": op.key,
                                                     "tool": op.tool, "action": op.action,
                                                     "authority": op.required_authority},
                               source="regent", mission_id=mission.id)
        return created

    def materialize_probes(self, mission: Mission, evals: list[RouteEval],
                           uncertainties: list[dict[str, Any]], top_k: int = 3) -> list[Operation]:
        """Parallel low-cost information gathering across live routes."""
        wanted: dict[str, float] = {}
        for u in uncertainties:
            if u["voi"] > 0 or u["flips_selection"]:
                wanted[u["fact"]] = max(wanted.get(u["fact"], 0), u["voi"] + (0.5 if u["flips_selection"] else 0))
        created = []
        live = [e for e in evals if e.route.status in ("alive", "selected")]
        for rank, e in enumerate(live):
            existing = self._existing(e.route)
            for i, raw in enumerate(e.route.operation_specs or []):
                spec = OperationSpec.model_validate(raw)
                if spec.kind != "probe" or spec.key in existing or spec.depends_on:
                    continue
                relevance = max([wanted.get(f, 0) for f in spec.resolves] or [0])
                if relevance <= 0 and rank >= top_k:
                    continue
                level = self.authority.classify(spec.tool, spec.action, spec.inputs, spec.authority)
                if level != "AUTO" or spec.cost_estimate.api_usd > PROBE_MAX_API_USD \
                        or spec.cost_estimate.money > PROBE_MAX_MONEY:
                    continue
                created.append(self._make_op(mission, e.route, spec, i, priority=2.0 + 10 * relevance))
        self.db.flush()
        for op in created:
            self.events.append("operation_planned", {"operation_id": op.id, "route_id": op.route_id, "key": op.key,
                                                     "kind": "probe", "tool": op.tool, "action": op.action},
                               source="regent", mission_id=mission.id)
        return created
