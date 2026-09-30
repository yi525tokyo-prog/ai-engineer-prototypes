"""The ``acquire`` tool: World Acquisition as an ordinary Regent tool (AUTO authority:
read-only access to the public web under robots.txt, rate limits and no blocker bypass)."""

from __future__ import annotations

from regent.schemas import CostEstimate, ToolResult
from regent.tools.base import ActionSpec, Tool, ToolContext


def acquire_tool() -> Tool:
    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        from regent.acquisition import service

        params = {k: v for k, v in inputs.items() if k != "domain"}
        out = service.run_action(action, mission_id=ctx.mission_id, params=params,
                                 domain=inputs.get("domain", "housing"))
        claims = [f"{action}: {out['pages']} pages, {out['mentions']} records, {out['claims']} claims",
                  f"funnel: {out.get('funnel')}"]
        return ToolResult(status="ok" if out["status"] == "done" else "failed", outputs=out,
                          error=None if out["status"] == "done" else "acquisition failed; see request log",
                          claims=claims, cost=CostEstimate(minutes=round(out["pages"] * 0.05, 2)))

    common = dict(latency_s=120, reliability=0.8, capabilities=["world.acquisition"])
    actions = {
        "discover": ActionSpec("discover", "Discover live candidates from the public web (market scan, navigation, "
                               "extraction, entity resolution, funnel)", input_schema={"params": "dict"},
                               output_schema={"request_id": "str", "funnel": "dict"}, **common),
        "enrich": ActionSpec("enrich", "Deep research on shortlisted entities (detail pages, source verification, "
                             "geocoding, public datasets)", input_schema={"entity_ids": "list"}, **common),
        "recheck": ActionSpec("recheck", "Re-observe time-sensitive claims past their TTL",
                              input_schema={"entity_ids": "list"}, **common),
        "analyze": ActionSpec("analyze", "Analyse the principal's sentence into explicit information needs",
                              input_schema={"sentence": "str"}, **common),
        "inventory": ActionSpec("inventory", "Which sources could answer the need, on what terms",
                                input_schema={}, **common),
        "observe": ActionSpec("observe", "Refresh capabilities past their refresh period",
                              input_schema={"capabilities": "list"}, **common),
    }
    return Tool("acquire", "search", "World Acquisition: evidence-backed world state from the public web", actions,
                handler, backend="live")
