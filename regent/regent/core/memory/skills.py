"""Skill extraction: persistent procedural memory.

When an operation goes attempt -> failure -> reroute -> success, the path is
generalized into a skill: task pattern, preconditions, the successful
procedure, known failure modes, verification method, confidence, provenance,
last verified time. Skills are stored privately and, when the privacy filter
allows, published (generalized) to the global skill network. The executor
consults skills before running an operation so learned reroutes are reused.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.core.observe.events import EventStore
from regent.db import Operation, Skill
from regent.global_brain.brain import LocalGlobalBrain, PrivacyViolation
from regent.ids import utcnow


def _skill_id(tool: str, action: str, failure: str) -> str:
    h = hashlib.sha1(f"{tool}.{action}|{failure}".encode()).hexdigest()[:10]
    return f"skill:{tool}.{action}:{h}"


def _failure_category(attempt: dict[str, Any]) -> str:
    if attempt.get("blocker"):
        return f"blocked:{attempt['blocker'].get('type')}"
    err = str(attempt.get("error") or "")
    if "missing credential" in err:
        return "missing_credential:" + err.split("missing credential", 1)[1].split(":")[0].strip()
    if "timeout" in err.lower():
        return "timeout"
    return "error"


class SkillExtractor:
    def __init__(self, db: Session):
        self.db = db
        self.events = EventStore(db)

    def extract(self, op: Operation) -> list[Skill]:
        log = op.attempt_log or []
        if op.status != "succeeded" or len(log) < 2:
            return []
        failures = [a for a in log[:-1] if a.get("status") in ("failed", "blocked")]
        if not failures:
            return []
        success = log[-1]
        first = failures[0]
        category = _failure_category(first)
        sid = _skill_id(first["tool"], first["action"], category)
        existing = self.db.get(Skill, sid)
        procedure = []
        for a in log:
            step = {"step": f"{a['tool']}.{a['action']}", "tool": a["tool"], "action": a["action"],
                    "outcome": a.get("status")}
            if a.get("via"):
                step["via"] = a["via"]
            procedure.append(step)
            if a.get("blocker"):
                procedure.append({"step": "human.interrupt", "tool": "human", "action": "perform",
                                  "outcome": "resolved",
                                  "note": f"escalate {a['blocker'].get('type')} to the principal as a bounded "
                                          "identity action; resume automatically when the page state changes"})
        failure_modes = sorted({_failure_category(a) for a in failures})
        uses = (existing.uses if existing else 0) + 1
        confidence = round(1 - 0.5 ** (uses + 1), 3)  # 0.75, 0.875, ...
        payload = {
            "id": sid, "domain": "private",
            "name": f"{first['tool']}.{first['action']} -> {success['tool']}.{success['action']} when {category}",
            "task_pattern": op.goal,
            "preconditions": [f"tool '{success['tool']}' available"],
            "procedure": procedure,
            "failure_modes": failure_modes,
            "verification": op.verification or {},
            "confidence": confidence,
            "provenance": (existing.provenance if existing else []) + [
                {"kind": "operation", "operation_id": op.id, "mission_id": op.mission_id,
                 "at": utcnow().isoformat(), "outcome": "succeeded"}],
            "uses": uses,
        }
        self.events.append("skill_published", payload, source="skill_extractor", mission_id=op.mission_id)
        out = [self.db.get(Skill, sid)]
        try:
            out.append(LocalGlobalBrain(self.db).publish_skill(payload, domain="global"))
        except PrivacyViolation as e:
            self.events.append("fact_observed", {"key": f"skill.{sid}.global_publish", "value": f"withheld: {e}"},
                               source="skill_extractor", mission_id=op.mission_id)
        return [s for s in out if s is not None]

    def shortcut_for(self, tool: str, action: str, credential_missing: set[str]) -> dict[str, Any] | None:
        """A learned reroute applicable right now (e.g. credential still missing)."""
        for s in self.db.scalars(select(Skill).where(Skill.domain == "private")):
            proc = s.procedure or []
            if not proc or proc[0].get("tool") != tool or proc[0].get("action") != action:
                continue
            for fm in s.failure_modes or []:
                if fm.startswith("missing_credential:") and fm.split(":", 1)[1] in credential_missing:
                    last = proc[-1]
                    if last.get("outcome") == "ok" and (last["tool"], last["action"]) != (tool, action):
                        return {"skill_id": s.id, "tool": last["tool"], "action": last["action"],
                                "reason": f"learned: {fm}"}
        return None
