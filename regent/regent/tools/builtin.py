"""Built-in tools.

Each tool wraps a connector/driver and returns structured ``ToolResult``s.
Credentials that are missing are reported per tool (``missing_credentials``)
and a local backend is used instead, so the tool is marked degraded rather
than silently pretending to be live.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from regent.browser.driver import HttpDriver, make_driver
from regent.config import settings
from regent.connectors.services import (
    BraveSearch,
    GitHubConnector,
    LocalCalendar,
    LocalCommerce,
    LocalMailbox,
    LocalMaps,
    LocalSearchIndex,
)
from regent.schemas import Blocked, CostEstimate, ToolResult
from regent.tools.base import ActionSpec, MissingCredential, Tool, ToolContext


def _safe_path(ctx: ToolContext, rel: str) -> Path:
    root = Path(ctx.workspace).resolve()
    p = (root / rel).resolve()
    if root not in p.parents and p != root:
        raise PermissionError(f"path escapes workspace: {rel}")
    return p


# ------------------------------------------------------------------- llm


def llm_tool() -> Tool:
    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        reg = ctx.services.providers
        budget = ctx.facts.get("treasury.api_budget_left")
        provider = reg.select("complete", stakes=float(inputs.get("stakes", 0.5)), api_budget_left=budget)
        task = inputs.get("task", "summarize")
        out = reg.call(provider, f"complete:{task}", lambda: provider.complete(task, inputs.get("context", {})),
                       mission_id=ctx.mission_id)
        cost = float(out.pop("cost_usd", provider.cost_per_call_usd))
        return ToolResult(outputs={**out, "provider": provider.name, "model": provider.model},
                          cost=CostEstimate(api_usd=cost), degraded=provider.kind == "local")

    actions = {"complete": ActionSpec("complete", "Run a language task (draft, analyze, summarize, plan)",
                                      cost_usd=0.02, latency_s=4, reliability=0.9,
                                      input_schema={"task": "str", "context": "object"},
                                      output_schema={"text": "str"}, capabilities=["language.reasoning"])}
    return Tool("llm", "llm", "Language model work via the provider layer", actions, handler, backend="live")


# ---------------------------------------------------------------- search


def search_tool() -> Tool:
    local = LocalSearchIndex()

    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        q, limit = inputs.get("query", ""), int(inputs.get("limit", 5))
        if action == "web":
            results = BraveSearch().search(q, limit)  # raises MissingCredential when unconfigured
            return ToolResult(outputs={"results": results, "count": len(results), "backend": "brave"})
        results = local.search(q, limit)
        return ToolResult(outputs={"results": results, "count": len(results), "backend": "local-index"},
                          degraded=True)

    actions = {
        "web": ActionSpec("web", "Live web search (Brave Search API)", cost_usd=0.005, latency_s=1.5,
                          reliability=0.95, input_schema={"query": "str", "limit": "int"},
                          output_schema={"results": "list", "count": "int"}, capabilities=["web.search"]),
        "local": ActionSpec("local", "Search the local knowledge index", latency_s=0.1, reliability=0.99,
                            input_schema={"query": "str"}, output_schema={"results": "list", "count": "int"},
                            capabilities=["knowledge.search"]),
    }
    missing = [] if settings.search_api_key else ["REGENT_SEARCH_API_KEY"]
    return Tool("search", "search", "Web search (live) with local index fallback", actions, handler,
                backend="live" if not missing else "local", missing_credentials=missing)


# --------------------------------------------------------------- browser


def browser_tool() -> Tool:
    state: dict[str, Any] = {}

    def driver():
        if "d" not in state:
            state["d"] = make_driver()
        return state["d"]

    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        workdir = Path(ctx.workspace) / "browser" / ctx.operation_id
        workdir.mkdir(parents=True, exist_ok=True)
        if action == "run":
            steps = inputs["steps"]
        elif action == "navigate":
            steps = [{"do": "navigate", "url": inputs["url"]}, {"do": "inspect"}]
        elif action == "inspect":
            steps = [{"do": "navigate", "url": inputs["url"]}, {"do": "inspect"}]
        elif action == "extract":
            steps = [{"do": "navigate", "url": inputs["url"]}] + [
                {"do": "extract", "selector": s, "as": k} for k, s in inputs["selectors"].items()]
        elif action == "screenshot":
            steps = [{"do": "navigate", "url": inputs["url"]}, {"do": "screenshot", "path": "page.png"}]
        elif action == "detect_blockers":
            steps = [{"do": "navigate", "url": inputs["url"]}]
        else:
            return ToolResult(status="failed", error=f"unsupported browser action {action}")
        d = driver()
        try:
            run = d.run(steps, workdir)
        except Exception:  # browser could not launch: degrade to static HTTP inspection
            d = state["d"] = HttpDriver()
            run = d.run(steps, workdir)
        out = {**run.outputs, "url": run.url, "trace": run.trace, "driver": getattr(d, "name", "?")}
        if run.blocker:
            return ToolResult(status="blocked", outputs=out, blocker=run.blocker,
                              error=f"blocked by {run.blocker.type}")
        if run.error:
            return ToolResult(status="failed", outputs=out, error=run.error)
        claims = [f"{k} = {str(v)[:120]}" for k, v in run.outputs.items() if isinstance(v, (str, int, float))]
        return ToolResult(outputs=out, claims=claims)

    ro = dict(latency_s=3, reliability=0.85, capabilities=["web.browse"])
    actions = {
        "run": ActionSpec("run", "Run a step script (navigate/inspect/click/type/extract/wait/screenshot/"
                          "download/upload); mutating steps raise authority to COMMIT", **ro,
                          input_schema={"steps": "list"}, output_schema={"trace": "list"}),
        "navigate": ActionSpec("navigate", "Open a page and summarize it", **ro, input_schema={"url": "str"}),
        "inspect": ActionSpec("inspect", "Inspect page structure/text/forms", **ro, input_schema={"url": "str"}),
        "extract": ActionSpec("extract", "Extract selectors from a page", **ro,
                              input_schema={"url": "str", "selectors": "dict"}),
        "screenshot": ActionSpec("screenshot", "Capture a screenshot", **ro, input_schema={"url": "str"}),
        "detect_blockers": ActionSpec("detect_blockers", "Detect CAPTCHA/login/payment/identity walls", **ro,
                                      input_schema={"url": "str"}),
    }
    return Tool("browser", "browser", "Playwright browser automation (HTTP fallback)", actions, handler,
                backend="live")


# ------------------------------------------------------------ filesystem


def fs_tool() -> Tool:
    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        if action == "write":
            p = _safe_path(ctx, inputs["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            content = str(inputs.get("content", ""))
            p.write_text(content)
            return ToolResult(outputs={"path": str(p), "bytes": len(content.encode())})
        if action == "read":
            p = _safe_path(ctx, inputs["path"])
            if not p.exists():
                return ToolResult(status="failed", error=f"not found: {inputs['path']}")
            return ToolResult(outputs={"path": str(p), "content": p.read_text()[:20000]})
        if action == "list":
            p = _safe_path(ctx, inputs.get("path", "."))
            return ToolResult(outputs={"entries": sorted(x.name for x in p.iterdir()) if p.exists() else []})
        return ToolResult(status="failed", error=action)

    actions = {
        "write": ActionSpec("write", "Write a file inside the workspace", latency_s=0.01, reliability=0.999,
                            input_schema={"path": "str", "content": "str"}, output_schema={"bytes": "int"},
                            capabilities=["files.write"]),
        "read": ActionSpec("read", "Read a workspace file", latency_s=0.01, reliability=0.999,
                           capabilities=["files.read"]),
        "list": ActionSpec("list", "List a workspace directory", latency_s=0.01, reliability=0.999),
    }
    return Tool("fs", "os", "Sandboxed workspace filesystem", actions, handler, backend="live")


# --------------------------------------------------------- code execution


def code_tool() -> Tool:
    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        if action == "run_tests":
            p = _safe_path(ctx, inputs.get("path", "."))
            if not p.exists():
                return ToolResult(status="failed", error=f"project not found: {inputs.get('path')}")
            report = _safe_path(ctx, f"reports/{ctx.operation_id}-junit.xml")
            report.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header", "-rf", "-p",
                                   "no:cacheprovider", f"--junitxml={report}", str(p)],
                                  capture_output=True, text=True, timeout=float(inputs.get("timeout", 120)),
                                  cwd=str(p))
            out = proc.stdout + proc.stderr
            if not report.exists():
                return ToolResult(status="failed", error=f"pytest produced no report (exit {proc.returncode})",
                                  outputs={"output": out[-4000:]})
            import xml.etree.ElementTree as ET

            root = ET.parse(report).getroot()
            suite = root if root.tag == "testsuite" else root.find("testsuite")
            total = int(suite.get("tests", 0))
            failed = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
            skipped = int(suite.get("skipped", 0))
            passed = total - failed - skipped
            failing = [f"{c.get('classname')}::{c.get('name')}" for c in suite.iter("testcase")
                       if c.find("failure") is not None or c.find("error") is not None]
            summary = f"{passed} passed, {failed} failed" + (f", {skipped} skipped" if skipped else "")
            return ToolResult(outputs={"passed": passed, "failed": failed, "skipped": skipped,
                                       "exit_code": proc.returncode, "failing": failing, "summary": summary,
                                       "output": out[-6000:], "report": str(report)},
                              claims=[summary + (f" ({', '.join(failing)})" if failing else "")])
        if action == "run_python":
            code = inputs.get("code")
            p = _safe_path(ctx, inputs.get("cwd", "."))
            p.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                  timeout=float(inputs.get("timeout", 60)), cwd=str(p))
            return ToolResult(status="ok" if proc.returncode == 0 else "failed",
                              outputs={"stdout": proc.stdout[-6000:], "stderr": proc.stderr[-3000:],
                                       "exit_code": proc.returncode},
                              error=None if proc.returncode == 0 else proc.stderr[-500:])
        if action == "build_tool":
            return _build_tool(inputs, ctx)
        return ToolResult(status="failed", error=action)

    actions = {
        "run_tests": ActionSpec("run_tests", "Run a project's pytest suite", latency_s=5, reliability=0.97,
                                input_schema={"path": "str"}, output_schema={"passed": "int", "failed": "int"},
                                capabilities=["code.test"]),
        "run_python": ActionSpec("run_python", "Execute Python in the workspace sandbox", latency_s=2,
                                 capabilities=["code.execute"]),
        "build_tool": ActionSpec("build_tool", "Generate, test and package a new tool for a missing capability",
                                 latency_s=10, reliability=0.7, cost_usd=0.05,
                                 input_schema={"capability": "str", "description": "str"},
                                 output_schema={"tool_name": "str", "path": "str", "tests_passed": "bool"},
                                 capabilities=["capability.build"]),
    }
    return Tool("code", "code", "Code execution in the workspace sandbox", actions, handler, backend="live")


def _build_tool(inputs: dict, ctx: ToolContext) -> ToolResult:
    cap = inputs["capability"]
    reg = ctx.services.providers
    provider = reg.select("generate_code", stakes=0.6, api_budget_left=ctx.facts.get("treasury.api_budget_left"))
    try:
        spec = reg.call(provider, "generate_code", lambda: provider.generate_code(inputs), mission_id=ctx.mission_id)
    except Exception as e:
        if provider.name != "local":
            local = reg.providers["local"]
            spec = reg.call(local, "generate_code", lambda: local.generate_code(inputs), mission_id=ctx.mission_id)
            provider = local
        else:
            return ToolResult(status="failed", error=str(e))
    d = _safe_path(ctx, f"built_tools/{cap.replace('.', '_')}")
    d.mkdir(parents=True, exist_ok=True)
    for rel, src in spec["files"].items():
        (d / rel).write_text(src)
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(d / spec["test"])],
                          capture_output=True, text=True, timeout=120, cwd=str(d))
    ok = proc.returncode == 0
    out = {"tool_name": spec["tool_name"], "path": str(d), "tests_passed": ok, "capabilities": spec["capabilities"],
           "actions": spec["actions"], "generated_by": provider.name, "test_output": (proc.stdout + proc.stderr)[-2000:]}
    if not ok:
        return ToolResult(status="failed", outputs=out, error="generated tool failed its tests")
    return ToolResult(outputs=out, claims=[f"built tool '{spec['tool_name']}' providing {spec['capabilities']}"])


def built_tool(name: str, path: str, actions: dict[str, dict], capabilities: list[str]) -> Tool:
    """Adapter for a tool Regent built for itself: runs ``tool.py`` in a subprocess."""

    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        inputs = {**inputs}
        inputs.setdefault("out_dir", str(_safe_path(ctx, f"artifacts/{name}")))
        proc = subprocess.run([sys.executable, str(Path(path) / "tool.py"), action, json.dumps(inputs)],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return ToolResult(status="failed", error=proc.stderr[-800:])
        return ToolResult(outputs=json.loads(proc.stdout))

    specs = {a: ActionSpec(a, d.get("description", a), authority=d.get("authority", "AUTO"),
                           input_schema=d.get("input_schema", {}), output_schema=d.get("output_schema", {}),
                           capabilities=capabilities, reliability=0.9)
             for a, d in actions.items()}
    return Tool(name, "code", f"Built by Regent ({', '.join(capabilities)})", specs, handler, backend="live",
                built_by_regent=True)


# ---------------------------------------------------------------- github


def github_tool() -> Tool:
    gh = GitHubConnector()

    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        if action == "repo_status":
            repo = inputs["repo"]
            if not gh.live or not ("/" in repo and not repo.startswith(".")):
                repo = str(_safe_path(ctx, repo))
            return ToolResult(outputs=gh.repo_status(repo))
        if action == "create_issue":
            return ToolResult(outputs=gh.create_issue(inputs["repo"], inputs["title"], inputs.get("body", "")))
        return ToolResult(status="failed", error=action)

    actions = {
        "repo_status": ActionSpec("repo_status", "Inspect repository state", capabilities=["code.repo.read"]),
        "create_issue": ActionSpec("create_issue", "Open an issue", authority="COMMIT",
                                   capabilities=["code.repo.write"]),
    }
    missing = [] if gh.live else ["GITHUB_TOKEN"]
    return Tool("github", "api", "GitHub (live API or local git)", actions, handler,
                backend="live" if gh.live else "local", missing_credentials=missing)


# ----------------------------------------------------------------- email


def email_tool() -> Tool:
    box = LocalMailbox()

    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        if action == "draft":
            body = inputs.get("body")
            subject = inputs.get("subject")
            msg = ctx.world.get("entities", {}).get(inputs.get("reply_to_message") or "")
            to = inputs.get("to") or ([msg["attrs"].get("from")] if msg else [])
            if inputs.get("compose") or not body:
                recip = ctx.world.get("entities", {}).get(to[0] if to else "", {})
                reg = ctx.services.providers
                provider = reg.select("complete", stakes=0.4, api_budget_left=ctx.facts.get("treasury.api_budget_left"))
                context = {"recipient_name": recip.get("name"), "subject": (msg or {}).get("attrs", {}).get("subject"),
                           "original": (msg or {}).get("attrs", {}).get("body"), "intent": inputs.get("intent"),
                           "points": inputs.get("points", []), "facts": inputs.get("facts", {})}
                out = reg.call(provider, "complete:draft_reply", lambda: provider.complete("draft_reply", context),
                               mission_id=ctx.mission_id)
                body = out["text"]
                subject = subject or out.get("subject")
            d = box.draft(to, subject or "(no subject)", body, inputs.get("reply_to_message"))
            return ToolResult(outputs={"draft_id": d["id"], "to": to, "subject": d["subject"], "body": body})
        if action == "send":
            sent = box.send(inputs.get("draft_id"), **{k: inputs[k] for k in ("to", "subject", "body") if k in inputs
                                                        and not inputs.get("draft_id")})
            return ToolResult(outputs={"message_id": sent["id"], "sent": True, "to": sent.get("to"),
                                       "in_reply_to": inputs.get("in_reply_to") or sent.get("in_reply_to")},
                              claims=[f"sent message {sent['id']} to {sent.get('to')}"])
        if action == "outbox":
            return ToolResult(outputs={"messages": box.outbox(), "ids": [m["id"] for m in box.outbox()]})
        return ToolResult(status="failed", error=action)

    actions = {
        "draft": ActionSpec("draft", "Compose a draft (not sent)", latency_s=2, capabilities=["messaging.draft"],
                            input_schema={"to": "list", "reply_to_message": "str", "intent": "str"},
                            output_schema={"draft_id": "str", "body": "str"}),
        "send": ActionSpec("send", "Send a message (external mutation)", authority="COMMIT", latency_s=1,
                           capabilities=["messaging.send"], input_schema={"draft_id": "str", "to": "list"},
                           output_schema={"message_id": "str"}),
        "outbox": ActionSpec("outbox", "List sent messages", capabilities=["messaging.read"]),
    }
    missing = [] if os.environ.get("REGENT_SMTP_URL") else ["REGENT_SMTP_URL"]
    return Tool("email", "connector", "Email (local mailbox; SMTP/Gmail when configured)", actions, handler,
                backend="local", missing_credentials=missing)


# -------------------------------------------------------------- calendar


def calendar_tool() -> Tool:
    cal = LocalCalendar()

    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        if action == "list":
            return ToolResult(outputs={"events": cal.list()})
        if action == "hold":
            start, end = inputs.get("start"), inputs.get("end")
            ev = ctx.world.get("entities", {}).get(inputs.get("before_event") or "")
            if ev and not start:
                from datetime import datetime, timedelta

                t = datetime.fromisoformat(ev["attrs"]["start"])
                hours = float(inputs.get("hours", 2))
                start = (t - timedelta(days=1)).replace(hour=10, minute=0).isoformat()
                end = (datetime.fromisoformat(start) + timedelta(hours=min(hours, 8))).isoformat()
            rec = cal.hold(inputs["title"], start or "", end or "", inputs.get("notes", ""))
            return ToolResult(outputs={"event_id": rec["id"], "start": start, "end": end})
        if action == "invite":
            rec = cal.create_invite(inputs["title"], inputs["start"], inputs["end"], inputs.get("attendees", []))
            return ToolResult(outputs={"event_id": rec["id"]})
        return ToolResult(status="failed", error=action)

    actions = {
        "list": ActionSpec("list", "List calendar entries", capabilities=["calendar.read"]),
        "hold": ActionSpec("hold", "Place a private hold on my own calendar", capabilities=["calendar.hold"]),
        "invite": ActionSpec("invite", "Send a calendar invitation to others", authority="COMMIT",
                             capabilities=["calendar.invite"]),
    }
    missing = [] if os.environ.get("GOOGLE_CALENDAR_CREDENTIALS") else ["GOOGLE_CALENDAR_CREDENTIALS"]
    return Tool("calendar", "connector", "Calendar (local; Google Calendar when configured)", actions, handler,
                backend="local", missing_credentials=missing)


# ------------------------------------------------------------------ maps


def maps_tool() -> Tool:
    m = LocalMaps()

    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        if action == "route":
            return ToolResult(outputs=m.route(inputs["origin"], inputs["destination"], inputs.get("mode", "transit")))
        if action == "lookup":
            return ToolResult(outputs=m.lookup(inputs["place_id"]))
        return ToolResult(status="failed", error=action)

    actions = {
        "route": ActionSpec("route", "Travel time/fare between places", capabilities=["maps.route"],
                            input_schema={"origin": "str", "destination": "str", "mode": "str"},
                            output_schema={"minutes": "int", "fare": "int"}),
        "lookup": ActionSpec("lookup", "Place details", capabilities=["maps.places"]),
    }
    missing = [] if os.environ.get("GOOGLE_MAPS_API_KEY") else ["GOOGLE_MAPS_API_KEY"]
    return Tool("maps", "api", "Maps & places (local fixtures; Google Maps when configured)", actions, handler,
                backend="local", missing_credentials=missing)


# ------------------------------------------------------------------ http


def http_tool() -> Tool:
    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        method = "GET" if action == "get" else "POST"
        r = httpx.request(method, inputs["url"], json=inputs.get("json"), headers=inputs.get("headers"), timeout=20)
        body: Any
        try:
            body = r.json()
        except Exception:
            body = r.text[:5000]
        status = "ok" if r.status_code < 400 else "failed"
        return ToolResult(status=status, outputs={"status_code": r.status_code, "body": body},
                          error=None if status == "ok" else f"HTTP {r.status_code}")

    actions = {
        "get": ActionSpec("get", "HTTP GET (read-only)", capabilities=["http.read"]),
        "post": ActionSpec("post", "HTTP POST (external mutation)", authority="COMMIT", capabilities=["http.write"]),
    }
    return Tool("http", "api", "Generic HTTP API", actions, handler, backend="live")


# -------------------------------------------------------------- commerce


def commerce_tool() -> Tool:
    c = LocalCommerce()

    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        order = c.purchase(inputs["vendor"], inputs["item"], float(inputs["amount"]), inputs.get("currency", ""))
        return ToolResult(outputs={"order_id": order["id"], "status": order["status"], "amount": order["amount"]},
                          cost=CostEstimate(money=float(inputs["amount"])))

    actions = {"purchase": ActionSpec("purchase", "Buy/book something (spends money)", authority="COMMIT",
                                      capabilities=["commerce.purchase"],
                                      input_schema={"vendor": "str", "item": "str", "amount": "float"})}
    return Tool("commerce", "connector", "Purchases & bookings (local ledger; vendor APIs when configured)",
                actions, handler, backend="local", missing_credentials=["STRIPE_API_KEY"])


# ----------------------------------------------------------------- analysis


def analysis_tool() -> Tool:
    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        if action == "runway":
            res = ctx.world.get("resources", [])
            money = next((r for r in res if r["kind"] == "money"), None)
            if not money:
                return ToolResult(status="failed", error="no money resource in world model")
            burn = float(money["attrs"].get("monthly_burn", 0) or 0)
            months = round(money["balance"] / burn, 2) if burn else None
            return ToolResult(outputs={"balance": money["balance"], "monthly_burn": burn, "runway_months": months,
                                       "unit": money["unit"]},
                              claims=[f"runway {months} months at burn {burn:,.0f}"])
        return ToolResult(status="failed", error=action)

    actions = {"runway": ActionSpec("runway", "Compute runway from treasury", capabilities=["treasury.analysis"])}
    return Tool("analysis", "code", "Deterministic analyses over the world model", actions, handler, backend="live")


# -------------------------------------------------------------- payments


def payments_tool() -> Tool:
    """Real interface, credential-gated. No local fallback: taking payments is not
    something to simulate -- the capability is genuinely missing without Stripe."""

    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        if not os.environ.get("STRIPE_API_KEY"):
            raise MissingCredential("STRIPE_API_KEY", "payment links require a Stripe account")
        r = httpx.post("https://api.stripe.com/v1/payment_links", auth=(os.environ["STRIPE_API_KEY"], ""),
                       data={"line_items[0][price]": inputs["price_id"], "line_items[0][quantity]": 1}, timeout=20)
        r.raise_for_status()
        return ToolResult(outputs={"url": r.json()["url"]})

    actions = {"create_checkout": ActionSpec("create_checkout", "Create a payment link", authority="COMMIT",
                                             capabilities=["payments.checkout"])}
    live = bool(os.environ.get("STRIPE_API_KEY"))
    return Tool("payments", "api", "Payments (Stripe)", actions, handler, backend="live" if live else "unavailable",
                missing_credentials=[] if live else ["STRIPE_API_KEY"])


# ------------------------------------------------------------------ agent


def agent_tool() -> Tool:
    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        url = os.environ.get("REGENT_AGENT_ENDPOINT")
        if not url:
            raise MissingCredential("REGENT_AGENT_ENDPOINT", "no external agent configured")
        r = httpx.post(url, json={"task": inputs["task"], "context": inputs.get("context", {})}, timeout=120)
        r.raise_for_status()
        return ToolResult(outputs=r.json())

    actions = {"delegate": ActionSpec("delegate", "Delegate a bounded task to another agent", latency_s=60,
                                      reliability=0.7, capabilities=["agent.delegate"])}
    live = bool(os.environ.get("REGENT_AGENT_ENDPOINT"))
    return Tool("agent", "agent", "External agent delegation (A2A-style HTTP)", actions, handler,
                backend="live" if live else "unavailable", missing_credentials=[] if live else ["REGENT_AGENT_ENDPOINT"])


# ------------------------------------------------------------------ human


def human_tool() -> Tool:
    """Operations routed to the principal. Executing one never 'succeeds' by
    itself: the executor turns it into a structured human interrupt."""

    def handler(action: str, inputs: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(status="blocked", blocker=Blocked(type=inputs.get("blocker_type", "physical"),
                                                            detail=inputs.get("required_action", "")))

    actions = {
        "perform": ActionSpec("perform", "Bounded real-world action only the human can do", authority="IDENTITY",
                              latency_s=120, reliability=0.9, capabilities=["human.action"],
                              input_schema={"required_action": "str", "response_schema": "dict",
                                            "estimated_time_seconds": "int"}),
        "confirm": ActionSpec("confirm", "Identity/regulated confirmation", authority="IDENTITY",
                              capabilities=["human.identity"]),
    }
    return Tool("human", "human", "The principal, as a callable real-world interface", actions, handler,
                backend="live")


def default_tools() -> list[Tool]:
    from regent.acquisition.tool import acquire_tool

    return [acquire_tool(), llm_tool(), search_tool(), browser_tool(), fs_tool(), code_tool(), github_tool(), email_tool(),
            calendar_tool(), maps_tool(), http_tool(), commerce_tool(), analysis_tool(), payments_tool(),
            agent_tool(), human_tool()]
