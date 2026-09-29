"""Local deterministic provider ("local strategist").

Always available, zero cost, fully inspectable. It generates routes from
playbooks over the world model, criticizes estimates with explicit rules,
drafts text from templates, and builds small tools from vetted code templates.
It is weaker than a frontier model and says so in its outputs; it exists so
the loop is never blocked on credentials.
"""

from __future__ import annotations

import re
from typing import Any

from regent.models import playbooks
from regent.models.base import ModelProvider
from regent.models.codegen_templates import TEMPLATES
from regent.schemas import Critique, OperationSpec, RouteEstimates, RouteProposal, VerificationResult


class LocalStrategist(ModelProvider):
    name = "local"
    model = "regent-local-strategist-v1"
    kind = "local"

    def generate_routes(self, mission, world, context) -> list[RouteProposal]:
        return playbooks.propose(mission, world)

    def criticize_routes(self, mission, world, routes) -> list[Critique]:
        out = []
        money = next((r for r in world.get("resources", []) if r["kind"] == "money"), None)
        balance = float(money["balance"]) if money else None
        for r in routes:
            est = r.get("effective") or r.get("estimates") or {}
            issues, adj = [], {}
            p = float(est.get("success_probability", 0.5))
            if p >= 0.8 and not r.get("evidence_ids"):
                issues.append(f"success probability {p:.2f} is not yet supported by evidence")
                adj["success_probability"] = round(p - 0.05, 3)
            if balance is not None and float(est.get("money_cost", 0)) > 0.5 * balance:
                issues.append("consumes more than half of available cash")
            if float(est.get("optionality", 0.5)) < 0.25:
                issues.append("locks in a commitment: low optionality")
            if r.get("uncertainty") and not any(o.get("kind") == "probe" for o in r.get("operation_specs", [])):
                issues.append("has open uncertainties but no information-gathering operation")
            missing = [c for c in r.get("required_capabilities", [])
                       if world.get("capabilities", {}).get(c) not in ("available", "degraded")]
            if missing:
                issues.append(f"requires capabilities not yet available: {', '.join(missing)}")
            if issues:
                out.append(Critique(route_key=r["key"], provider=self.name, issues=issues, adjustments=adj,
                                    confidence=0.6))
        return out

    def extract_world_state(self, text: str, world) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for m in re.finditer(r"([\w.+-]+@[\w-]+\.[\w.]+)", text):
            email = m.group(1)
            events.append({"type": "entity_upserted", "id": f"person_{email.split('@')[0]}", "kind": "person",
                           "name": email.split("@")[0], "attrs": {"email": email}})
        for m in re.finditer(r"(?:¥|JPY\s?)([\d,]+)", text):
            events.append({"type": "fact_observed", "key": "text.amount_mentioned",
                           "value": int(m.group(1).replace(",", "")), "confidence": 0.6})
        return events

    def estimate_route(self, mission, world, route) -> tuple[RouteEstimates, dict[str, str]]:
        est = RouteEstimates(**(route.get("estimates") or {}))
        return est, {"note": "local strategist keeps playbook estimates; evidence adjusts them via sensitivities"}

    def summarize_evidence(self, evidence) -> str:
        if not evidence:
            return "No evidence recorded."
        lines = [f"- {e.get('claim')} (source: {e.get('source')}, conf {e.get('confidence', 0):.2f})"
                 for e in evidence[:12]]
        return "\n".join(lines)

    def generate_operation(self, goal: str, tools, world) -> OperationSpec:
        g = goal.lower()
        if any(w in g for w in ("search", "research", "find", "look up")):
            return OperationSpec(key="gen.search", goal=goal, tool="search", action="web", inputs={"query": goal})
        if any(w in g for w in ("test", "build", "run")):
            return OperationSpec(key="gen.code", goal=goal, tool="code", action="run_tests", inputs={"path": "."})
        if any(w in g for w in ("email", "reply", "message")):
            return OperationSpec(key="gen.draft", goal=goal, tool="email", action="draft",
                                 inputs={"to": [], "intent": goal, "compose": True})
        return OperationSpec(key="gen.plan", goal=goal, tool="llm", action="complete",
                             inputs={"task": "plan", "context": {"objective": goal}})

    def verify_result(self, operation, outputs) -> VerificationResult:
        bad = [k for k, v in outputs.items() if isinstance(v, str) and v.lower().startswith("error")]
        if not outputs:
            return VerificationResult(verdict="fail", method="model", detail="empty outputs")
        if bad:
            return VerificationResult(verdict="fail", method="model", detail=f"error values in {bad}")
        return VerificationResult(verdict="pass", method="model", detail="outputs present and well-formed")

    # ---------------------------------------------------------------- work

    def complete(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        fn = getattr(self, f"_task_{task}", None)
        if fn is None:
            return {"text": f"[{self.model}] no template for task '{task}'. Context keys: {sorted(context)}",
                    "degraded": True}
        return fn(context)

    def _task_draft_reply(self, c: dict[str, Any]) -> dict[str, Any]:
        name = (c.get("recipient_name") or "there").split()[0]
        subject = c.get("subject") or ""
        intent = c.get("intent", "follow up")
        points = c.get("points") or []
        facts = c.get("facts") or {}
        body = [f"Hi {name},", ""]
        intro = {
            "confirm attendance and demo": f"Thanks for your message about \"{subject}\". I'm confirmed for the meeting and will bring a working demo.",
            "accept next step": f"Thank you for the offer regarding \"{subject}\". I'd like to move to the next step.",
            "request payment deferral": f"Regarding \"{subject}\": could we agree to split this month's payment into two instalments?",
            "accept the desk offer": "Thank you, I'd love to take you up on the desk this week.",
            "keep the door open": f"Thank you for the update on \"{subject}\" - and for being upfront.",
        }.get(intent, f"Following up on \"{subject}\".")
        body.append(intro)
        for p in points:
            body.append(f"- {p}")
        if facts.get("meeting_format") == "remote":
            body.append("Happy to do this remotely; a video link works for me.")
        body += ["", "Best regards"]
        return {"text": "\n".join(body), "subject": f"Re: {subject}" if subject else intent}

    def _task_analyze_test_failures(self, c: dict[str, Any]) -> dict[str, Any]:
        out = str(c.get("output") or "")
        failed = re.findall(r"FAILED ([\w/.\-]+::[\w\[\]\-]+)", out) or list(c.get("failing") or [])
        errors = re.findall(r"^E\s+(.+)$", out, flags=re.M)
        n = len(failed) if failed else int(c.get("failed") or 0)
        hours = round(1.5 * n + (0.5 if errors else 0), 1)
        lines = [f"{n} failing test(s)."] + [f"- {f}" for f in failed[:10]]
        if errors:
            lines.append("Key assertion errors:")
            lines += [f"  {e}" for e in errors[:5]]
        lines.append(f"Estimated fix effort: ~{hours}h (1.5h per failing test heuristic).")
        return {"text": "\n".join(lines), "failing": failed, "fix_hours": hours}

    def _task_draft_proposal(self, c: dict[str, Any]) -> dict[str, Any]:
        results = c.get("results") or []
        if isinstance(results, str):
            results = []
        skills = ", ".join(c.get("skills") or ["software development"])
        titles = [r.get("title", "") for r in results[:3] if isinstance(r, dict)]
        text = ("# Proposal\n\n"
                f"I build and ship production software ({skills}). "
                "Short engagements, fixed scope, weekly demos.\n\n"
                + ("Targets:\n" + "\n".join(f"- {t}" for t in titles) + "\n\n" if titles else "")
                + "Availability: 20h/week. Rate negotiable for fixed-scope work.\n")
        return {"text": text}

    def _task_plan(self, c: dict[str, Any]) -> dict[str, Any]:
        obj = c.get("objective", "objective")
        approach = c.get("approach", "direct")
        steps = {
            "direct": ["Define done", "Execute core work", "Verify against success criteria"],
            "information-first": ["List unknowns", "Research each", "Re-estimate", "Commit to best path"],
            "delegate": ["Specify outcome", "Find provider", "Agree price", "Verify delivery"],
        }.get(approach, ["Clarify", "Act", "Verify"])
        return {"text": f"# Plan ({approach})\n\nObjective: {obj}\n\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))}

    def _task_summarize(self, c: dict[str, Any]) -> dict[str, Any]:
        text = str(c.get("text", ""))
        sents = re.split(r"(?<=[.!?])\s+", text)
        return {"text": " ".join(sents[:3])}

    def generate_code(self, spec: dict[str, Any]) -> dict[str, Any]:
        cap = spec.get("capability")
        tpl = TEMPLATES.get(cap)
        if tpl is None:
            raise ValueError(f"local strategist has no vetted template for capability '{cap}'; "
                             "a remote model provider is required to synthesize it")
        return tpl()
