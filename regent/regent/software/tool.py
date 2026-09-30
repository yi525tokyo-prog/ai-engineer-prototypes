"""The ``software`` tool: building, verifying and activating capabilities as Regent operations.

Every action runs in its own DB session (tools run in worker threads). ``verify`` is Regent's
acceptance suite -- its ``passed`` output is what the loop's verifier checks -- and
``delegate_build`` hands a brief to a coding-agent worker only under the principal's
authorization (COMMIT authority: an agent writing and running code is not something Regent
may start on its own).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from regent.schemas import CostEstimate, ToolResult
from regent.tools.base import ActionSpec, Tool, ToolContext


def mission_context(s, mission_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from regent.software.domain import latest_plan
    from regent.db import Mission

    m = s.get(Mission, mission_id)
    need = ((m.attrs or {}).get("need") if m else None) or {}
    inv = latest_plan(s, mission_id, "inventory") or {}
    return need, inv


def software_tool() -> Tool:
    def handler(action: str, inputs: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from regent import db as dbm
        from regent.software import capability as K
        from regent.software import compose as P
        from regent.software import verify as V
        from regent.software.need import signature

        s = dbm.session()
        try:
            if action == "compose":
                need, inv = mission_context(s, ctx.mission_id)
                if not need or not inv:
                    return ToolResult(status="failed", error="need analysis and inventory are required first")
                include = list(inputs.get("include") or [])
                spec = P.compose(need, inv, include=include)
                existing = s.scalar(select(K.SwCapability).where(
                    K.SwCapability.mission_id == ctx.mission_id, K.SwCapability.status != "retired"))
                cap = K.save_version(s, mission_id=ctx.mission_id, need=need, signature=signature(need), spec=spec,
                                     implementation="composed", reason=f"composed (include={include})",
                                     provenance={"need_analysis": need.get("analysis"),
                                                 "field_semantics": inv.get("semantics"),
                                                 "composer": "regent.software.compose (deterministic)",
                                                 "operation": ctx.operation_id},
                                     capability_id=existing.id if existing else None)
                s.commit()
                return ToolResult(status="ok", outputs={
                    "capability_id": cap.id, "slug": cap.slug, "version": cap.version,
                    "metrics": [m["id"] for m in spec["metrics"]], "sources": [x["id"] for x in spec["sources"]],
                    "unanswered": [u["question"] for u in spec["unanswered"]]},
                    claims=[f"composed {cap.slug} v{cap.version}: {len(spec['metrics'])} metrics from "
                            f"{len(spec['sources'])} sources"])
            if action in ("verify", "upgrade"):
                cap = K.get(s, inputs["capability_id"])
                if cap is None:
                    return ToolResult(status="failed", error="no such capability")
                if action == "upgrade":
                    from regent.software import secrets

                    conn = inputs.get("connector")
                    miss = [c for src in cap.spec.get("sources", []) if src["connector"] == conn
                            for c in src.get("credentials", []) if not secrets.present(c)]
                    if miss:
                        return ToolResult(status="failed", error=f"missing credential {miss[0]}",
                                          outputs={"missing_credential": miss[0]})
                from regent.runtime import get_services

                res = V.run(s, cap, services=ctx.services or get_services())
                if action == "upgrade" and res["passed"]:
                    cap.status = "usable" if res["coverage"] >= 0.8 else "degraded"
                s.commit()
                return ToolResult(status="ok", outputs={"capability_id": cap.id, **{k: res[k] for k in (
                    "passed", "coverage", "summary", "version")}, "checks": [
                    {k: c[k] for k in ("check", "passed", "critical", "detail")} for c in res["checks"]]},
                    claims=[res["summary"]])
            if action == "activate":
                cap = K.get(s, inputs["capability_id"])
                if cap is None or not (cap.verification or {}).get("passed"):
                    return ToolResult(status="failed", error="refusing to activate an unverified capability")
                cap.status = "usable" if (cap.coverage or 0) >= 0.8 else "degraded"
                from regent.runtime import get_services

                svc = ctx.services or get_services()
                K.register_tool(svc, cap)
                r = K.read(s, cap, count_use=False)
                s.commit()
                return ToolResult(status="ok", outputs={
                    "capability_id": cap.id, "tool": cap.tool_name, "status": cap.status, "coverage": r["coverage"],
                    "view": f"/software/{cap.slug}", "api": f"/api/software/capabilities/{cap.slug}",
                    "headline": {m["id"]: m["display"] for m in r["metrics"] if m.get("headline")}},
                    facts=K.facts(cap, r) + [
                        {"key": f"software.need.{ctx.mission_id}.usable", "value": True, "confidence": 1.0},
                        {"key": f"software.need.{ctx.mission_id}.coverage", "value": r["coverage"], "confidence": 1.0},
                        {"key": f"software.need.{ctx.mission_id}.capability", "value": cap.slug, "confidence": 1.0}],
                    claims=[f"{cap.tool_name} registered; status {cap.status}; coverage {r['coverage']:.2f}"])
            if action == "reuse":
                cap = K.get(s, inputs["capability_id"])
                if cap is None or cap.status not in ("usable", "degraded"):
                    return ToolResult(status="failed", error="capability not usable")
                if K.due(cap):
                    K.collect(s, cap)
                r = K.read(s, cap)
                passed = any(m.get("value") is not None for m in r["metrics"])
                s.commit()
                return ToolResult(status="ok", outputs={"capability_id": cap.id, "passed": passed,
                                                        "coverage": r["coverage"], "view": f"/software/{cap.slug}",
                                                        "metrics": {m["id"]: m["display"] for m in r["metrics"]}},
                                  facts=K.facts(cap, r) + [
                                      {"key": f"software.need.{ctx.mission_id}.usable", "value": passed},
                                      {"key": f"software.need.{ctx.mission_id}.coverage", "value": r["coverage"]},
                                      {"key": f"software.need.{ctx.mission_id}.capability", "value": cap.slug}])
            if action == "delegate_build":
                from regent.software.resources import coding_agent

                agent = coding_agent()
                if not agent.authorized():
                    return ToolResult(status="failed", error="coding agent not authorized by the principal",
                                      outputs={"authorization_needed": agent.describe()})
                need, inv = mission_context(s, ctx.mission_id)
                return agent.build_capability(s, ctx.mission_id, need, inv, list(inputs.get("include") or []))
            return ToolResult(status="failed", error=f"unknown action {action}")
        finally:
            s.close()

    auto = dict(latency_s=5, reliability=0.9)
    actions = {
        "compose": ActionSpec("compose", "Compose a capability spec from the verified inventory", "AUTO",
                              capabilities=["software.compose"], **auto),
        "verify": ActionSpec("verify", "Regent's acceptance suite (sources, independent reads, honesty, interface, "
                             "rendered view)", "AUTO", capabilities=["software.verify"], latency_s=60, reliability=0.9),
        "activate": ActionSpec("activate", "Register a verified capability as tool, view and world facts", "AUTO",
                               capabilities=["software.activate"], **auto),
        "upgrade": ActionSpec("upgrade", "Read a newly unlocked source and re-verify", "AUTO",
                              capabilities=["software.verify"], latency_s=60, reliability=0.85),
        "reuse": ActionSpec("reuse", "Answer from an existing verified capability", "AUTO",
                            capabilities=["software.reuse"], **auto),
        "delegate_build": ActionSpec("delegate_build", "Let a coding agent write and run code in a sandboxed "
                                     "workspace to build the capability", "COMMIT", cost_usd=5.0, latency_s=1800,
                                     reliability=0.6, capabilities=["software.delegate"]),
    }
    return Tool("software", "code", "Software capabilities: compose, verify, activate, reuse, delegate", actions,
                handler, backend="live")
