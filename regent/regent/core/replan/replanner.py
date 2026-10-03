"""Replanner.

Two responsibilities:

1. **Consequences of verified work** -- world effects (a sent reply marks the
   message answered, a built tool registers a capability), linking evidence to
   routes, extracting skills from reroutes.
2. **Full re-evaluation** -- re-score *every* live route against the updated
   world, re-select with hysteresis, and when the plan changes, record exactly
   which evidence moved which estimates (the answer to "why did the plan change?").
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.core.audit.log import DecisionLog
from regent.core.capabilities.manager import CapabilityManager
from regent.core.constitution.model import ConstitutionModel
from regent.core.evaluate.evaluator import Evaluator, RouteEval
from regent.core.memory.memory import MemoryStore
from regent.core.memory.skills import SkillExtractor
from regent.core.observe.events import EventStore
from regent.core.planner.planner import Planner
from regent.core.world.state import WorldView
from regent.db import Evidence, Mission, Operation, Route, RouteScore
from regent.ids import new_id


class Replanner:
    def __init__(self, db: Session, services: Any):
        self.db = db
        self.services = services
        self.events = EventStore(db)
        self.evaluator = Evaluator(db)
        self.planner = Planner(db, services)
        self.log = DecisionLog(db)
        self.capabilities = CapabilityManager(db, services)
        self.skills = SkillExtractor(db)
        self.memory = MemoryStore(db)

    # ------------------------------------------------------- consequences

    def apply_consequences(self, op: Operation, evidence: Evidence) -> list[str]:
        notes: list[str] = []
        if op.route_id:
            r = self.db.get(Route, op.route_id)
            if r is not None:
                r.evidence_ids = list(r.evidence_ids or []) + [evidence.id]
        if op.status == "failed" and op.route_id and op.kind != "probe":
            # an operation the route needs failed for good (retries and fallbacks are exhausted, or
            # its result failed verification): the route as planned cannot deliver. Take it out of
            # contention; re-evaluation selects the next best route instead of waiting silently.
            r = self.db.get(Route, op.route_id)
            if r is not None and r.status in ("alive", "selected"):
                r.status = "failed"
                r.invalidated_reason = f"{op.key} failed: {(op.error or 'verification failed')[:300]}"
                m = self.db.get(Mission, op.mission_id)
                if m is not None and m.selected_route_id == r.id:
                    m.selected_route_id = None
                for o in self.db.scalars(select(Operation).where(Operation.route_id == r.id,
                                                                 Operation.status.in_(("pending", "blocked")))):
                    o.status, o.error = "cancelled", f"route failed: {op.key}"
                self.events.append("route_invalidated", {"route_id": r.id, "key": r.key,
                                                         "reason": r.invalidated_reason},
                                   source="regent", mission_id=op.mission_id)
                notes.append(f"route {r.key} failed: {r.invalidated_reason}")
        if op.status != "succeeded":
            return notes
        out = op.outputs or {}
        if op.tool == "email" and op.action == "send":
            self.events.append("message_sent", {"message_id": out.get("message_id"), "to": out.get("to"),
                                                "in_reply_to": out.get("in_reply_to")},
                               source="tool:email", mission_id=op.mission_id)
            notes.append(f"reply sent ({out.get('message_id')})")
        if op.tool == "calendar" and op.action in ("hold", "invite"):
            self.events.append("calendar_event_changed", {
                "id": out.get("event_id"), "title": (op.inputs or {}).get("title", "hold"), "start": out.get("start"),
                "end": out.get("end"), "status": "held", "created_by": "regent"},
                source="tool:calendar", mission_id=op.mission_id)
        if op.tool == "code" and op.action == "build_tool" and out.get("tests_passed"):
            caps = self.capabilities.register_built_tool(out, op.mission_id)
            notes.append(f"capability acquired: {caps}")
        if op.tool == "commerce" and op.action == "purchase":
            self.events.append("entity_upserted", {"id": f"order_{out.get('order_id')}", "kind": "commitment",
                                                   "name": op.goal, "attrs": {"order_id": out.get("order_id"),
                                                                              "amount": out.get("amount")}},
                               source="tool:commerce", mission_id=op.mission_id)
        if len(op.attempt_log or []) > 1:
            skills = self.skills.extract(op)
            if skills:
                notes.append(f"skill extracted: {skills[0].name}")
        return notes

    # ------------------------------------------------------ re-evaluation

    def reevaluate(self, mission: Mission, world: WorldView, *, trigger: str, snapshot_id: str | None,
                   evidence_ids: list[str] | None = None, model_outputs: list[dict] | None = None) -> dict[str, Any]:
        routes = list(self.db.scalars(select(Route).where(Route.mission_id == mission.id)))
        previous = {r.id: {"score": r.score, "effective": dict(r.effective or {}), "rank": r.rank,
                           "applied": list(r.applied_sensitivities or [])} for r in routes}
        evals, ctx = self.evaluator.evaluate(mission, routes, world)
        for e in evals:
            r = e.route
            r.effective = {k: round(v, 5) for k, v in e.effective.items()}
            r.applied_sensitivities = e.applied
            r.score = e.score
            r.score_breakdown = {"components": e.components, "blocked": e.blocked, "invalid": e.invalid,
                                 "missing_capabilities": e.missing_capabilities, "selectable": e.selectable}
            r.rank = e.rank
            if e.invalid and r.status == "alive":
                r.status = "invalidated"
                r.invalidated_reason = "; ".join(e.invalid)
                self.events.append("route_invalidated", {"route_id": r.id, "key": r.key, "reason": r.invalidated_reason},
                                   source="evaluator", mission_id=mission.id)
            elif not e.invalid and r.status == "invalidated":
                r.status, r.invalidated_reason = "alive", None
            self.db.add(RouteScore(id=new_id("rs"), mission_id=mission.id, route_id=r.id, tick=mission.tick_count,
                                   score=e.score, rank=e.rank, effective=r.effective))
        sel = self.planner.select(mission, evals)
        uncertainties = self.evaluator.decision_relevant_uncertainties(
            mission, routes, world, sel["selected"].route.id if sel["selected"] else None)
        mission.attrs = {**(mission.attrs or {}), "evaluation_context": ctx, "uncertainties": uncertainties[:8]}
        considered = [e.summary() for e in evals]
        decision = None
        if sel["changed"]:
            self.planner.apply_selection(mission, sel)
            rationale = self._explain(sel, evals, previous, ctx)
            ev_ids = sorted({a["evidence_id"] for e in evals for a in e.applied if a.get("evidence_id")}
                            | set(evidence_ids or []))
            decision = self.log.record(
                mission_id=mission.id, kind=sel["kind"], tick=mission.tick_count, snapshot_id=snapshot_id,
                summary=sel["reason"], routes_considered=considered,
                selected_route_id=sel["selected"].route.id if sel["selected"] else None,
                previous_route_id=sel["previous"].route.id if sel["previous"] else None,
                rationale={**rationale, "trigger": trigger}, evidence_ids=ev_ids, model_outputs=model_outputs or [])
            self.events.append(sel["kind"] if sel["kind"] in ("route_selected", "plan_changed") else "routes_ranked", {
                "decision_id": decision.id, "selected": sel["selected"].route.key if sel["selected"] else None,
                "previous": sel["previous"].route.key if sel["previous"] else None, "reason": sel["reason"]},
                source="regent", mission_id=mission.id)
            self.memory.remember(f"{mission.title}: {sel['reason']}", kind="decision", mission_id=mission.id,
                                 meta={"decision_id": decision.id})
            if sel["kind"] == "plan_changed" and sel["previous"] is not None and sel["selected"] is not None:
                self._note_constitution_tension(mission, sel["selected"].route, sel["previous"].route, decision.id)
        else:
            rank_changed = any(previous.get(e.route.id, {}).get("rank") != e.rank for e in evals)
            if rank_changed and previous:
                decision = self.log.record(
                    mission_id=mission.id, kind="plan_kept", tick=mission.tick_count, snapshot_id=snapshot_id,
                    summary=f"Ranking changed; {sel['reason']}", routes_considered=considered,
                    selected_route_id=mission.selected_route_id, previous_route_id=mission.selected_route_id,
                    rationale={**self._explain(sel, evals, previous, ctx), "trigger": trigger})
        return {"evals": evals, "selection": sel, "decision": decision, "uncertainties": uncertainties,
                "context": ctx}

    def _explain(self, sel: dict[str, Any], evals: list[RouteEval], previous: dict[str, dict],
                 ctx: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"weights": ctx["weights"], "weight_reasons": ctx["weight_reasons"], "routes": []}
        for e in evals:
            prev = previous.get(e.route.id) or {}
            prev_applied = {(a["fact"], str(a.get("value"))) for a in prev.get("applied", [])}
            new_applied = [a for a in e.applied if (a["fact"], str(a.get("value"))) not in prev_applied]
            delta = {f: {"from": round(prev.get("effective", {}).get(f, v), 4), "to": round(v, 4)}
                     for f, v in e.effective.items()
                     if prev.get("effective") and abs(prev["effective"].get(f, v) - v) > 1e-6}
            out["routes"].append({
                "route_id": e.route.id, "key": e.route.key, "title": e.route.title, "score": e.score,
                "previous_score": prev.get("score"), "rank": e.rank, "previous_rank": prev.get("rank"),
                "components": e.components, "estimate_changes": delta, "new_evidence_effects": new_applied,
                "selectable": e.selectable, "blocked": e.blocked, "invalid": e.invalid})
        if sel.get("selected") is not None:
            s = sel["selected"]
            runner = next((e for e in evals if e.selectable and e.route.id != s.route.id), None)
            if runner is not None:
                diffs = {k: round(s.components.get(k, 0) - runner.components.get(k, 0), 4)
                         for k in set(s.components) | set(runner.components)}
                out["versus_runner_up"] = {"runner_up": runner.route.key, "margin": round(s.score - runner.score, 4),
                                           "component_advantage": dict(sorted(diffs.items(), key=lambda x: -abs(x[1])))}
        return out

    def _note_constitution_tension(self, mission: Mission, new: Route, old: Route, decision_id: str) -> None:
        """If the switch trades against a stated preference, surface it as an unresolved conflict."""
        cm = ConstitutionModel(self.db)
        bonuses = cm.tag_bonuses()
        lost = [t for t in (old.tags or []) if bonuses.get(t, (0, ""))[0] > 0 and t not in (new.tags or [])]
        gained = [t for t in (new.tags or []) if bonuses.get(t, (0, ""))[0] < 0]
        if lost or gained:
            self.events.append("constitution_item_upserted", {
                "id": f"conflict:plan:{mission.id}", "type": "conflict", "status": "active",
                "statement": (f"Switching to '{new.title}' trades against stated preferences "
                              f"({', '.join(lost + gained)}); evidence made the preferred route unviable."),
                "confidence": 0.6, "sources": [{"kind": "plan_change", "decision_id": decision_id}]},
                source="replanner", mission_id=mission.id)
