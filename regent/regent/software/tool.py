"""The ``software`` tool: building, verifying and activating capabilities as Regent operations.

Every action runs in its own DB session (tools run in worker threads). ``verify`` is Regent's
acceptance suite -- its ``passed`` output is what the loop's verifier checks. ``design_app``,
``build_app``, ``extend_app`` and ``use_app`` are the application lifecycle: Regent designs the
interface and its own acceptance scenarios, a coding agent writes the code (only under the
principal's authorization: COMMIT authority), Regent builds, tests, runs, accepts, repairs,
promotes and registers it, and later missions use it through its API.
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
                use = tuple(inputs.get("use") or P.ORIGINS)
                spec = P.compose(need, inv, include=include, use=use)
                existing = s.scalar(select(K.SwCapability).where(
                    K.SwCapability.mission_id == ctx.mission_id, K.SwCapability.status != "retired"))
                cap = K.save_version(s, mission_id=ctx.mission_id, need=need, signature=signature(need), spec=spec,
                                     implementation="composed", reason=f"composed (use={list(use)}, include={include})",
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
            if action in ("design_app", "build_app", "extend_app"):
                return _app_action(s, action, inputs, ctx)
            if action == "use_app":
                return _use_app(s, inputs, ctx)
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
        "design_app": ActionSpec("design_app", "Design the application Regent will have built: interface, UI hooks, "
                                 "runtime contract, Regent's own acceptance scenarios", "AUTO",
                                 capabilities=["software.design"], latency_s=120, reliability=0.85),
        "build_app": ActionSpec("build_app", "Delegate construction to a coding agent, then inspect, build, test, "
                                "run, accept (API + browser + restart + negative), repair, promote and register",
                                "COMMIT", cost_usd=15.0, latency_s=3600, reliability=0.6,
                                capabilities=["software.delegate"]),
        "extend_app": ActionSpec("extend_app", "Upgrade an application in use: new version by the coding agent, "
                                 "tested on a copy of the real data, promoted with backup and rollback", "COMMIT",
                                 cost_usd=15.0, latency_s=3600, reliability=0.55, capabilities=["software.delegate"]),
        "use_app": ActionSpec("use_app", "Do what the principal asked through an application's API, then read "
                              "back that it happened", "AUTO", capabilities=["software.use"], latency_s=60,
                              reliability=0.85),
    }
    return Tool("software", "code", "Software capabilities: compose, verify, activate, reuse, delegate", actions,
                handler, backend="live")


def _app_action(s, action: str, inputs: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from regent.schemas import CostEstimate
    from regent.software import appbuild as B
    from regent.software import appcap
    from regent.software import appdesign as D
    from regent.software import capability as K
    from regent.software.need import signature
    from regent.software.resources import coding_agent
    from regent.db import Mission

    need, inv = mission_context(s, ctx.mission_id)
    m = s.get(Mission, ctx.mission_id)
    attrs = dict(m.attrs or {})
    base = K.get(s, inputs["capability_id"]) if inputs.get("capability_id") else None
    previous = None
    if base is not None:
        previous = {"version": base.spec["app"]["version"], "workspace": base.spec["app"]["workspace"],
                    "design": base.spec["design"], "port": base.spec["app"].get("port")}
        need = _scoped_need(need, previous["version"] + 1)
    if action == "design_app":
        sources = inv.get("app_sources", [])
        d = D.design(need, {"sources": sources, "constraints": [c["statement"] for c in inv.get("constraints", [])
                                                                 if c.get("evidence_found")]},
                     previous=previous["design"] if previous else None, mission_id=ctx.mission_id)
        if d.get("ok") and previous:
            d["design"] = with_regression(d["design"], previous["design"])
            d["problems"] = D.check(d["design"], need, previous["design"])
            d["ok"] = not d["problems"]
        if not d.get("ok"):
            return ToolResult(status="failed", error="design rejected: " + "; ".join(d.get("problems") or [d.get("error", "")]),
                              outputs={"problems": d.get("problems")})
        attrs["app_design"] = d["design"]
        m.attrs = attrs
        s.commit()
        return ToolResult(status="ok", outputs={"designed": True, "design": d["design"],
                                                "endpoints": len(d["design"]["api"]),
                                                "scenarios": len(d["design"]["scenarios"]), "name": d["design"]["name"]},
                          claims=[f"design {d['design']['name']}: {len(d['design']['api'])} endpoints, "
                                  f"{len(d['design']['scenarios'])} acceptance scenarios"])
    design = _accepted_design(s, ctx.mission_id) or attrs.get("app_design")
    if not design:
        return ToolResult(status="failed", error="no accepted design")
    agent = coding_agent()
    if not agent.authorized():
        return ToolResult(status="failed", error="the principal has not opted in to a coding agent",
                          outputs={"authorization_needed": agent.describe()})
    slug = (base and appcap.app_slug(base)) or K.slugify(design["name"])
    version = (previous["version"] + 1) if previous else 1
    notes: list[str] = []
    b = B.AppBuild(slug=slug, need=need, design=design, sources=inv.get("app_sources", []), agent=agent,
                   version=version, previous=previous, log=lambda st, msg, **k: notes.append(f"{st}: {msg}"))
    res = b.run()
    cost = sum(float((r.get("worker") or {}).get("cost_usd") or 0) for r in res["rounds"])
    summary = {"accepted": res["accepted"], "version": version, "rounds": len(res["rounds"]), "worker_cost_usd": round(cost, 2),
               "failures_by_round": [[f["stage"] + ": " + f["summary"][:160] for f in r["failures"]] for r in res["rounds"]],
               "inspect": {k: res["rounds"][-1]["inspect"].get(k) for k in ("files", "lines", "languages", "tests")},
               "disputes": res.get("disputes") or [], "scenario_revisions": [
                   {k: x.get(k) for k in ("scenario", "round", "why")} for x in design.get("revisions", [])],
               "log": notes}
    if not res["accepted"]:
        return ToolResult(status="ok", outputs={**summary, "passed": False}, cost=CostEstimate(api_usd=cost),
                          claims=[f"v{version} not accepted after {len(res['rounds'])} rounds"])
    promo = b.promote()
    summary["promote"] = promo
    if not promo.get("live"):
        return ToolResult(status="ok", outputs={**summary, "passed": False}, cost=CostEstimate(api_usd=cost))
    acc = res["rounds"][-1].get("acceptance") or {}
    full_need = need if base is None else {**base.need, "requirements": base.need.get("requirements", [])
                                           + need.get("requirements", [])}
    cov = D.requirement_coverage(design, full_need, acc.get("by_id", {}))
    cap = appcap.register(s, mission_id=ctx.mission_id if base is None else base.mission_id, need=full_need,
                          signature=signature(need) + (base.signature if base else []), design=design, build=res,
                          promote=promo, capability_id=base.id if base else None, coverage=cov,
                          provenance={"worker_runs": [r.get("worker") for r in res["rounds"]], "design_by": "reasoner",
                                      "verified_by": "regent.software.appbuild"})
    from regent.runtime import get_services

    appcap.register_tool(ctx.services or get_services(), cap)
    from regent.core.observe.events import EventStore

    EventStore(s).append("principal_notified", {
        "capability": cap.slug, "channel": "regent_inbox",
        "text": (f"{design['name']} v{version} is running at {promo['url']} (on this machine only; reaching it from "
                 "another device needs hosting you approve). Sign in with the passphrase Regent generated, kept in "
                 f"Regent's secret store as {res.get('credential')}." if res.get("credential") else
                 f"{design['name']} v{version} is running at {promo['url']}.")},
        source=f"capability:{cap.slug}", mission_id=ctx.mission_id)
    s.commit()
    return ToolResult(status="ok", outputs={**summary, "passed": True, "capability_id": cap.id, "tool": cap.tool_name,
                                            "url": promo["url"], "coverage": cov},
                      cost=CostEstimate(api_usd=cost),
                      facts=[{"key": f"software.need.{ctx.mission_id}.usable", "value": True},
                             {"key": f"software.need.{ctx.mission_id}.coverage", "value": cov},
                             {"key": f"software.need.{ctx.mission_id}.capability", "value": cap.slug}],
                      claims=[f"application {cap.slug} v{version} live at {promo['url']}; coverage {cov:.2f}"])


def _scoped_need(need: dict[str, Any], version: int) -> dict[str, Any]:
    """A later mission's requirements, with ids that cannot collide with the ones the application
    was first built for (both sets stay on the capability)."""
    return {**need, "requirements": [{**r, "id": f"v{version}-{r['id']}"} for r in need.get("requirements", [])]}


def with_regression(new: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    """The next version stays accountable to everything the version in use was accepted for:
    its scenarios run again (endpoint ids mapped by method and path) and its UI hooks are kept."""
    by_route = {(a["method"], a["path"]): a["id"] for a in new.get("api", [])}
    ids = {a["id"]: by_route.get((a["method"], a["path"]), a["id"]) for a in old.get("api", [])}
    mine = {s["id"] for s in new.get("scenarios", [])}
    carried = []
    for sc in old.get("scenarios", []):
        steps = [{**st, "api": ids.get(st["api"], st["api"])} if st.get("api") else st for st in sc.get("steps", [])]
        sid = sc["id"] if sc["id"] not in mine else f"prev-{sc['id']}"
        carried.append({**sc, "id": sid, "steps": steps, "regression": True})
    hooks = {u["testid"] for u in new.get("ui", [])}
    return {**new, "scenarios": new.get("scenarios", []) + carried,
            "external_hosts": sorted(set(new.get("external_hosts") or []) | set(old.get("external_hosts") or [])),
            "ui": new.get("ui", []) + [u for u in old.get("ui", []) if u["testid"] not in hooks]}


def _accepted_design(s, mission_id: str) -> dict[str, Any] | None:
    """The design the mission's own design_app operation produced and Regent checked (the
    operation record is the durable copy; the mission's attrs may be rewritten by the loop)."""
    from regent.db import Operation

    for o in s.scalars(select(Operation).where(Operation.mission_id == mission_id, Operation.tool == "software",
                                               Operation.action == "design_app")
                       .order_by(Operation.created_at.desc())):
        if o.status in ("succeeded", "verified") and (o.outputs or {}).get("design"):
            return o.outputs["design"]
    return None


_CALL = {"api": {"type": "string"}, "path_params": {"type": ["object", "null"]}, "query": {"type": ["object", "null"]},
         "body": {"type": ["object", "null"]}}
USE_SCHEMA = {"type": "object", "required": ["lookups", "calls", "verify", "report"], "properties": {
    "lookups": {"type": "array", "description": "GET calls whose answers you need before you can plan the changes "
                "(e.g. find an id); if non-empty, leave calls/verify empty -- you will be asked again with the "
                "answers", "items": {"type": "object", "required": ["api"], "properties": _CALL}},
    "calls": {"type": "array", "items": {"type": "object", "required": ["api"], "properties": {
        **_CALL, "save": {"type": ["object", "null"]}, "why": {"type": "string"}}}},
    "verify": {"type": "array", "items": {"type": "object", "required": ["api", "expect_json_contains"], "properties": {
        **_CALL, "expect_json_contains": {"type": "object"}}}},
    "report": {"type": "string", "description": "what to tell the principal when it is done; may use {var} values "
               "saved from calls and {base_url} (the application's address)"}}}

USE_INSTRUCTIONS = """The principal asked for something to be done with an application an operational agent runs.
Using only the listed endpoints, give the calls that do it (reuse existing records instead of creating duplicates --
the current data is included), and read-back checks that prove it happened. If you need answers you do not have yet
(e.g. an id from a search), ask for them as lookups first. Use {var} for values saved from earlier calls. Do only
what was asked, and say in the report what the principal needs to know (e.g. a link they asked for)."""


def _use_app(s, inputs: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from regent.core.observe.events import EventStore
    from regent.db import Mission
    from regent.software import appaccept as A
    from regent.software import appcap
    from regent.software import capability as K
    from regent.software.reasoner import ReasonerUnavailable, get_reasoner

    cap = K.get(s, inputs["capability_id"])
    m = s.get(Mission, ctx.mission_id)
    need = (m.attrs or {}).get("need") or {}
    r = appcap.runner(cap)
    current = A.snapshot(r.svc, cap.spec["design"], r.passphrase)
    api = {a["id"]: a for a in cap.spec["design"]["api"]}
    payload: dict[str, Any] = {"request": need.get("sentence"), "requirements": need.get("requirements"),
                               "endpoints": cap.spec["design"]["api"], "current_data": current}
    looked: list[dict[str, Any]] = []
    plan: dict[str, Any] = {}
    for _ in range(3):
        try:
            plan = get_reasoner().ask("app_use_plan", USE_INSTRUCTIONS, payload, USE_SCHEMA, budget_usd=0.8,
                                      mission_id=ctx.mission_id).output
        except ReasonerUnavailable as e:
            return ToolResult(status="failed", error=str(e)[:300])
        bad = [c["api"] for c in plan["lookups"] + plan["calls"] + plan["verify"] if c["api"] not in api]
        if bad:
            return ToolResult(status="failed", error=f"plan uses endpoints the application does not have: {bad}")
        if not plan["lookups"]:
            break
        if any(api[c["api"]]["method"] != "GET" for c in plan["lookups"]):
            return ToolResult(status="failed", error="a lookup must not change anything (GET only)")
        for c in plan["lookups"]:
            resp = r.call(c["api"], c.get("path_params"), None, query=c.get("query"))
            looked.append({**c, "status": resp.status_code, "answer": _json(resp)})
        payload = {**payload, "lookup_answers": looked}
    else:
        return ToolResult(status="failed", error="still looking things up after three rounds")
    steps = [{"do": "call", **{k: c.get(k) for k in ("api", "path_params", "query", "body", "save")}}
             for c in plan["calls"]]
    steps += [{"do": "call", **{k: v.get(k) for k in ("api", "path_params", "query", "expect_json_contains")}}
              for v in plan["verify"]]
    res = r.run([{"id": "use", "requirement": "request", "kind": "api", "steps": steps}])["scenarios"][0]
    vars_ = {**(res.get("vars") or {}), "base_url": r.svc.url}
    report = A._subst(plan.get("report") or "", vars_) if res["passed"] else None
    cap.uses = (cap.uses or 0) + 1
    if report:
        EventStore(s).append("principal_notified", {"capability": cap.slug, "channel": "regent_inbox", "text": report},
                             source=f"capability:{cap.slug}", mission_id=ctx.mission_id)
    s.commit()
    return ToolResult(status="ok", outputs={"passed": res["passed"], "capability_id": cap.id, "calls": len(plan["calls"]),
                                            "lookups": len(looked), "verified_by_read_back": len(plan["verify"]),
                                            "report": report, "responses": res.get("responses"),
                                            "log": res.get("log"), "error": res.get("error")},
                      facts=[{"key": f"software.need.{ctx.mission_id}.usable", "value": res["passed"]},
                             {"key": f"software.need.{ctx.mission_id}.coverage", "value": 1.0 if res["passed"] else 0.0},
                             {"key": f"software.need.{ctx.mission_id}.capability", "value": cap.slug}],
                      claims=[f"{len(plan['calls'])} calls through {cap.tool_name}; read-back "
                              f"{'confirms' if res['passed'] else 'does NOT confirm'} the request"])


def _json(resp) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:2000]
