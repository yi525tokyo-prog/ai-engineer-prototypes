"""Verifier.

An operation is not successful because a tool returned; it is successful when
its declared verification passes:

* ``schema``          -- required output keys are present and non-null
* ``predicate``       -- ``{"path", "op", "value"}`` holds on the outputs
* ``state_check``     -- an independent read-only tool call confirms the new
                         world state (e.g. the sent message is in the outbox)
* ``model``           -- a provider judges the result (weakest; used last)
* ``human_confirmed`` -- the principal reported completion

Verified results become Evidence; declared ``emits`` become world facts that
cite that evidence.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from regent.core.observe.events import EventStore
from regent.db import Evidence, Operation
from regent.ids import new_id, utcnow
from regent.schemas import Condition, VerificationResult, VerificationSpec
from regent.tools.base import ToolContext, get_path


class Verifier:
    def __init__(self, db: Session, services: Any):
        self.db = db
        self.services = services
        self.events = EventStore(db)

    def verify(self, op: Operation) -> VerificationResult:
        spec = VerificationSpec.model_validate(op.verification or {})
        out = {k: v for k, v in (op.outputs or {}).items() if not k.startswith("_")}
        if spec.method == "none":
            return VerificationResult(verdict="pass", method="none", detail="no verification declared")
        if spec.method == "schema":
            missing = [k for k in spec.required_keys if out.get(k) is None]
            return VerificationResult(verdict="fail" if missing else "pass", method="schema",
                                      detail=f"missing {missing}" if missing else f"keys present: {spec.required_keys}",
                                      checks=[{"key": k, "present": k not in missing} for k in spec.required_keys])
        if spec.method == "predicate":
            p = spec.predicate or {}
            val = get_path(out, p.get("path", ""))
            cond = Condition(fact="x", op=p.get("op", "eq"), value=p.get("value"))
            ok = cond.holds({"x": val}, {"x"} if val is not None else set())
            return VerificationResult(verdict="pass" if ok else "fail", method="predicate",
                                      detail=f"{p.get('path')}={val!r} {p.get('op')} {p.get('value')!r}",
                                      checks=[{"path": p.get("path"), "value": val, "ok": bool(ok)}])
        if spec.method == "state_check":
            return self._state_check(op, spec, out)
        if spec.method == "human_confirmed":
            ok = bool(out.get("done") or out.get("resolved_by"))
            return VerificationResult(verdict="pass" if ok else "fail", method="human_confirmed",
                                      detail="principal reported completion" if ok else "no confirmation")
        if spec.method == "model":
            reg = self.services.providers
            p = reg.select("verify_result")
            return reg.call(p, "verify_result", lambda: p.verify_result(
                {"goal": op.goal, "tool": op.tool, "action": op.action}, out), mission_id=op.mission_id)
        return VerificationResult(verdict="inconclusive", method=spec.method, detail="unknown method")

    def _state_check(self, op: Operation, spec: VerificationSpec, out: dict[str, Any]) -> VerificationResult:
        from regent.core.executor.executor import resolve_templates

        chk = spec.check or {}
        tool = self.services.tools.get(chk.get("tool", ""))
        if tool is None or chk.get("action") not in tool.actions:
            return VerificationResult(verdict="inconclusive", method="state_check", detail="check tool unavailable")
        if tool.actions[chk["action"]].authority != "AUTO":
            return VerificationResult(verdict="inconclusive", method="state_check",
                                      detail="state checks must be read-only (AUTO)")
        inputs = resolve_templates(chk.get("inputs", {}), {}, {}, None, op)
        expect = resolve_templates(chk.get("expect", {}), {}, {}, None, op)
        ctx = ToolContext(mission_id=op.mission_id, operation_id=op.id, workspace=self.services.workspace,
                          services=self.services)
        res = tool.invoke(chk["action"], inputs, ctx)
        if res.status != "ok":
            return VerificationResult(verdict="fail", method="state_check", detail=f"check failed: {res.error}")
        checks = []
        ok = True
        if "contains_id" in expect:
            hit = expect["contains_id"] in (res.outputs.get("ids") or [])
            checks.append({"contains_id": expect["contains_id"], "ok": hit})
            ok &= hit
        if "nonempty" in expect:
            hit = bool(res.outputs.get(expect["nonempty"]))
            checks.append({"nonempty": expect["nonempty"], "ok": hit})
            ok &= hit
        for path, val in (expect.get("equals") or {}).items():
            hit = get_path(res.outputs, path) == val
            checks.append({"path": path, "expected": val, "ok": hit})
            ok &= hit
        return VerificationResult(verdict="pass" if ok else "fail", method="state_check",
                                  detail=f"independent check {chk['tool']}.{chk['action']}", checks=checks)

    # -------------------------------------------------------------- apply

    def verify_and_record(self, op: Operation) -> tuple[VerificationResult, Evidence]:
        vr = self.verify(op)
        op.verification_result = vr.model_dump()
        out = {k: v for k, v in (op.outputs or {}).items() if not k.startswith("_")}
        claims = (op.outputs or {}).get("_claims") or []
        degraded = bool((op.outputs or {}).get("_degraded"))
        if vr.verdict == "pass":
            op.status = "succeeded"
            claim = f"{op.goal}: " + ("; ".join(claims) if claims else "verified")
            conf = 0.9 if not degraded else 0.75
        else:
            policy = op.retry_policy or {}
            if vr.verdict == "fail" and (op.attempts or 0) < int(policy.get("max_attempts", 1)) and op.tool != "human":
                op.status = "pending"
            else:
                op.status = "failed"
            op.error = f"verification {vr.verdict}: {vr.detail}"
            claim = f"{op.goal}: verification {vr.verdict} ({vr.detail})"
            conf = 0.6
        op.finished_at = utcnow() if op.status in ("succeeded", "failed") else None
        facts = []
        if vr.verdict == "pass":
            for path, key in (op.emits or {}).items():
                val = get_path(out, path)
                if val is not None:
                    facts.append({"key": key, "value": _coerce(val), "confidence": conf})
            for f in (op.outputs or {}).get("_facts") or []:
                facts.append({"confidence": conf, **f})
        ev = Evidence(id=new_id("evd"), mission_id=op.mission_id, route_id=op.route_id, operation_id=op.id,
                      kind="human_report" if op.tool == "human" else "tool_result", claim=claim[:1000],
                      data={"verification": vr.model_dump(), "outputs": _trim(out), "degraded": degraded},
                      source=f"{op.tool}.{op.action}", confidence=conf, fact_keys=[f["key"] for f in facts])
        self.db.add(ev)
        self.db.flush()
        op.evidence_ids = list(op.evidence_ids or []) + [ev.id]
        if facts:
            self.events.append("fact_observed", {"facts": facts, "evidence_id": ev.id}, source=f"verifier:{op.tool}",
                               mission_id=op.mission_id)
        self.events.append("operation_verified", {"operation_id": op.id, "verdict": vr.verdict, "method": vr.method,
                                                  "evidence_id": ev.id, "facts": [f["key"] for f in facts]},
                           source="verifier", mission_id=op.mission_id)
        return vr, ev


def _coerce(v: Any) -> Any:
    if isinstance(v, str):
        s = v.strip()
        low = s.lower()
        if low in ("true", "false"):
            return low == "true"
        try:
            return int(s.replace(",", "")) if s.replace(",", "").lstrip("-").isdigit() else float(s)
        except ValueError:
            return s
    return v


def _trim(d: dict[str, Any], n: int = 1500) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        if isinstance(v, str) and len(v) > n:
            out[k] = v[:n] + "..."
        elif isinstance(v, list) and len(v) > 20:
            out[k] = v[:20]
        else:
            out[k] = v
    return out
