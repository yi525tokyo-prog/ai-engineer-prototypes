"""Executor.

Runs ready operations against real tools. For each operation it:

* checks dependencies (by key within the route) and tool availability;
* asks the Authority Manager -- no permission request when authority exists,
  a bounded interrupt when it does not;
* checks affordability with the Treasury;
* applies learned skill shortcuts (e.g. a known reroute around a missing
  credential);
* resolves ``{{ops.<key>.outputs.<path>}}`` / ``{{facts.<key>}}`` templates;
* runs tools concurrently with timeouts;
* handles retry and fallback reroutes, and turns blockers (CAPTCHA, login...)
  into structured human interrupts.

Results are recorded as ``unverified``; the Verifier decides success.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.core.audit.log import DecisionLog
from regent.core.authority.manager import AuthorityManager
from regent.core.human.interrupts import HumanInterruptManager
from regent.core.memory.skills import SkillExtractor
from regent.core.observe.events import EventStore
from regent.core.treasury.treasury import Treasury
from regent.core.world.state import WorldView
from regent.db import Mission, Operation
from regent.ids import utcnow
from regent.schemas import ToolResult
from regent.tools.base import ToolContext, get_path

TEMPLATE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
DONE = {"succeeded"}
DEAD = {"failed", "cancelled", "skipped"}


@dataclass
class ExecutionReport:
    started: list[str] = field(default_factory=list)
    finished: list[str] = field(default_factory=list)
    interrupts: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    authority: list[dict[str, Any]] = field(default_factory=list)


def resolve_templates(value: Any, ops_by_key: dict[str, Operation], facts: dict[str, Any],
                      mission: Mission | None = None, self_op: Operation | None = None) -> Any:
    if isinstance(value, dict):
        return {k: resolve_templates(v, ops_by_key, facts, mission, self_op) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_templates(v, ops_by_key, facts, mission, self_op) for v in value]
    if not isinstance(value, str) or "{{" not in value:
        return value

    def lookup(expr: str) -> Any:
        if expr.startswith("facts."):
            return facts.get(expr[len("facts."):])
        if expr.startswith("self.") and self_op is not None:
            return get_path({"outputs": self_op.outputs or {}, "inputs": self_op.inputs or {}}, expr[5:])
        if expr.startswith("mission.") and mission is not None:
            return getattr(mission, expr.split(".", 1)[1], None)
        if expr.startswith("ops."):
            rest = expr[4:]
            # op keys may contain dots: find the longest key prefix that exists
            for k in sorted(ops_by_key, key=len, reverse=True):
                if rest.startswith(k + "."):
                    op = ops_by_key[k]
                    return get_path({"outputs": op.outputs or {}, "inputs": op.inputs or {}}, rest[len(k) + 1:])
        return None

    whole = TEMPLATE.fullmatch(value.strip())
    if whole:
        return lookup(whole.group(1))
    return TEMPLATE.sub(lambda m: "" if lookup(m.group(1)) is None else str(lookup(m.group(1))), value)


def world_context(world: WorldView) -> dict[str, Any]:
    return {
        "entities": {e.id: {"id": e.id, "kind": e.kind, "name": e.name, "attrs": e.attrs} for e in world.entities.values()},
        "resources": [{"id": r.id, "kind": r.kind, "balance": r.balance, "unit": r.unit, "attrs": r.attrs}
                      for r in world.resources.values()],
    }


class Executor:
    def __init__(self, db: Session, services: Any, max_parallel: int = 6):
        self.db = db
        self.services = services
        self.events = EventStore(db)
        self.authority = AuthorityManager(db, services.tools)
        self.interrupts = HumanInterruptManager(db, services)
        self.treasury = Treasury(db)
        self.skills = SkillExtractor(db)
        self.log = DecisionLog(db)
        self.max_parallel = max_parallel

    def mission_ops(self, mission_id: str) -> list[Operation]:
        return list(self.db.scalars(select(Operation).where(Operation.mission_id == mission_id)
                                    .order_by(Operation.sequence, Operation.created_at)))

    def _route_ops(self, mission_id: str, route_id: str | None) -> dict[str, Operation]:
        return {o.key: o for o in self.mission_ops(mission_id) if o.route_id == route_id}

    def ready(self, mission: Mission) -> list[Operation]:
        ops = self.mission_ops(mission.id)
        by_route: dict[str | None, dict[str, Operation]] = {}
        for o in ops:
            by_route.setdefault(o.route_id, {})[o.key] = o
        out = []
        for op in ops:
            if op.status == "blocked" and (op.error or "").startswith("tool unavailable") \
                    and self.services.tools.get(op.tool) is not None:
                op.status, op.error = "pending", None
            if op.status != "pending":
                continue
            siblings = by_route.get(op.route_id, {})
            deps = [siblings.get(k) for k in op.depends_on or []]
            if any(d is not None and d.status in DEAD for d in deps):
                bad = [d.key for d in deps if d is not None and d.status in DEAD]
                if op.kind != "probe" and op.status != "blocked":
                    op.status, op.error = "blocked", f"dependency did not succeed: {bad}"
                continue
            if all(d is None or d.status in DONE for d in deps):
                out.append(op)
        out.sort(key=lambda o: -o.priority)
        return out

    # ----------------------------------------------------------------- run

    def run(self, mission: Mission, world: WorldView) -> ExecutionReport:
        rep = ExecutionReport()
        facts, _ = world.fact_map()
        budget = self.treasury.api_budget_left()
        if budget is not None:
            facts["treasury.api_budget_left"] = budget
        wctx = world_context(world)
        batch: list[tuple[Operation, str, str, dict[str, Any]]] = []
        for op in self.ready(mission):
            if op.tool == "human":
                op.authority_decision = {"level": "IDENTITY", "allowed": False, "needs": "identity",
                                         "reason": "operation is routed to the principal"}
                hi = self.interrupts.for_human_operation(op)
                rep.interrupts.append(hi.id)
                continue
            tool = self.services.tools.get(op.tool)
            if tool is None:
                op.status, op.error = "blocked", f"tool unavailable: '{op.tool}' (capability not yet acquired)"
                rep.blocked.append(op.id)
                continue
            if op.action not in tool.actions:
                op.status, op.error = "failed", f"unknown action {op.tool}.{op.action}"
                continue
            decision = self.authority.decide(op, world)
            op.authority_decision = {**(op.authority_decision or {}), **decision.as_dict()}
            if decision.level != "AUTO":
                rep.authority.append({"operation_id": op.id, **decision.as_dict()})
                self.log.record(mission_id=mission.id, kind="authority", tick=mission.tick_count,
                                summary=f"{'Allowed' if decision.allowed else 'Needs principal'}: {op.tool}.{op.action} "
                                        f"- {decision.reason}", authority=decision.as_dict(),
                                rationale={"operation_id": op.id, "goal": op.goal})
            if not decision.allowed:
                if decision.needs == "approval":
                    hi = self.interrupts.for_authorization(op, decision.reason)
                else:
                    hi = self.interrupts.for_human_operation(op)
                rep.interrupts.append(hi.id)
                continue
            ok, why = self.treasury.can_afford(op.cost_estimate or {})
            if not ok:
                op.status, op.error = "blocked", f"insufficient resources: {why}"
                rep.blocked.append(op.id)
                continue
            tool_name, action = op.tool, op.action
            shortcut = self.skills.shortcut_for(tool_name, action, set(tool.missing_credentials))
            if shortcut is not None and self.services.tools.has_action(shortcut["tool"], shortcut["action"]):
                op.attempt_log = list(op.attempt_log or []) + [{
                    "tool": tool_name, "action": action, "status": "skipped", "via": f"skill {shortcut['skill_id']}",
                    "note": shortcut["reason"], "at": utcnow().isoformat()}]
                op.tool, op.action = shortcut["tool"], shortcut["action"]
                tool_name, action = op.tool, op.action
            inputs = resolve_templates({k: v for k, v in (op.inputs or {}).items() if not k.startswith("_")},
                                       self._route_ops(mission.id, op.route_id), facts, mission)
            op.status = "running"
            op.started_at = op.started_at or utcnow()
            self.events.append("operation_started", {"operation_id": op.id, "tool": tool_name, "action": action,
                                                     "attempt": (op.attempts or 0) + 1},
                               source="executor", mission_id=mission.id)
            batch.append((op, tool_name, action, inputs))
            rep.started.append(op.id)
        if not batch:
            return rep
        # Durability + no lock held while tools run (tools use their own sessions).
        self.db.commit()
        results = self._invoke_all(mission, batch, facts, wctx)
        for (op, tool_name, action, inputs), res in zip(batch, results):
            self._record(mission, op, tool_name, action, inputs, res, rep)
        self.db.flush()
        return rep

    def _invoke_all(self, mission: Mission, batch, facts, wctx) -> list[ToolResult]:
        results: list[ToolResult] = []
        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            futures = []
            for op, tool_name, action, inputs in batch:
                ctx = ToolContext(mission_id=mission.id, operation_id=op.id, workspace=self.services.workspace,
                                  facts=facts, world=wctx, services=self.services)
                tool = self.services.tools.get(tool_name)
                futures.append((op, pool.submit(tool.invoke, action, inputs, ctx)))
            for op, fut in futures:
                try:
                    results.append(fut.result(timeout=max(op.timeout_s or 60, 1)))
                except FutureTimeout:
                    results.append(ToolResult(status="failed", error=f"timeout after {op.timeout_s}s"))
                except Exception as e:  # pragma: no cover - Tool.invoke already guards
                    results.append(ToolResult(status="failed", error=f"{type(e).__name__}: {e}"))
        return results

    def _record(self, mission: Mission, op: Operation, tool_name: str, action: str, inputs: dict[str, Any],
                res: ToolResult, rep: ExecutionReport) -> None:
        op.attempts = (op.attempts or 0) + 1
        attempt = {"tool": tool_name, "action": action, "status": res.status, "error": res.error,
                   "degraded": res.degraded, "at": utcnow().isoformat(),
                   "blocker": res.blocker.model_dump() if res.blocker else None}
        op.attempt_log = list(op.attempt_log or []) + [attempt]
        cost = res.cost.model_dump()
        spent = self.treasury.record_operation_cost(op, cost)
        op.actual_cost = {k: round((op.actual_cost or {}).get(k, 0) + v, 6) for k, v in {**cost, **spent}.items()}
        payload = {"operation_id": op.id, "tool": tool_name, "action": action, "status": res.status,
                   "error": res.error, "degraded": res.degraded}
        if res.status == "ok":
            op.outputs = {**res.outputs, "_claims": res.claims, "_facts": res.facts, "_degraded": res.degraded}
            op.status = "unverified"
            op.error = None
            self.events.append("tool_succeeded", payload, source=f"tool:{tool_name}", mission_id=mission.id)
            rep.finished.append(op.id)
            return
        self.events.append("tool_failed", payload, source=f"tool:{tool_name}", mission_id=mission.id)
        if res.status == "blocked" and res.blocker is not None:
            op.outputs = {**(op.outputs or {}), **res.outputs}
            hi = self.interrupts.from_blocker(op, res.blocker)
            rep.interrupts.append(hi.id)
            return
        policy = dict(op.retry_policy or {})
        failures_on_current = sum(1 for a in op.attempt_log if a["tool"] == tool_name and a["action"] == action
                                  and a["status"] in ("failed",))
        fallbacks = policy.get("fallback") or []
        idx = int(policy.get("fallback_index", -1))
        if failures_on_current < int(policy.get("max_attempts", 1)) and "missing credential" not in (res.error or ""):
            op.status, op.error = "pending", res.error  # retry next round
        elif idx + 1 < len(fallbacks):
            fb = fallbacks[idx + 1]
            policy["fallback_index"] = idx + 1
            op.retry_policy = policy
            op.tool, op.action = fb["tool"], fb["action"]
            if fb.get("inputs"):
                op.inputs = {**(op.inputs or {}), **fb["inputs"]}
            op.required_authority = self.authority.classify(op.tool, op.action, op.inputs)
            t = self.services.tools.get(op.tool)
            op.executor = t.executor if t else op.executor
            op.status, op.error = "pending", f"rerouted after: {res.error}"
            self.events.append("operation_rerouted", {"operation_id": op.id, "from": f"{tool_name}.{action}",
                                                      "to": f"{op.tool}.{op.action}", "reason": res.error},
                               source="executor", mission_id=mission.id)
        else:
            op.status, op.error = "failed", res.error
            op.finished_at = utcnow()
        rep.finished.append(op.id)
