"""Delegatable resources: who or what Regent can hand work to, and on what terms.

Regent decides; resources do. Each resource states whether it is available, what it costs,
what authority using it consumes, and why it might not be usable right now -- so route
generation can weigh "compose it myself" against "have an agent build it" against "ask the
principal" with the same currency.

The coding agent is the Claude Code CLI working in a sandboxed workspace with file-edit tools
only. Starting it is a COMMIT-level act (an autonomous agent writing code that Regent will
later run), so it needs the principal's opt-in (``REGENT_CODING_AGENT=claude-code``) *and* an
approved operation. Whatever it produces is run by Regent in a subprocess with a timeout and
then put through the same acceptance suite as anything Regent composed itself.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from regent.config import settings
from regent.software import secrets
from regent.software.reasoner import get_reasoner


class CodingAgent:
    name = "coding_agent"

    def __init__(self, model: str | None = None, budget_usd: float = 5.0):
        self.model = model or os.environ.get("REGENT_CODING_AGENT_MODEL", "claude-sonnet-5-5")
        self.budget_usd = budget_usd

    def installed(self) -> bool:
        return shutil.which("claude") is not None

    def authorized(self) -> bool:
        return os.environ.get("REGENT_CODING_AGENT", "").strip() == "claude-code"

    def describe(self) -> dict[str, Any]:
        why = None
        if not self.installed():
            why = "Claude Code CLI not installed"
        elif not self.authorized():
            why = ("the principal has not opted in to autonomous coding agents "
                   "(set REGENT_CODING_AGENT=claude-code); each run also needs an approved operation")
        return {"resource": self.name, "backend": "claude-code", "model": self.model, "available": why is None,
                "unavailable_because": why, "authority": "COMMIT", "budget_usd_per_run": self.budget_usd,
                "sandbox": "own workspace directory; tools Read/Write/Edit/Glob/Grep only; Regent runs the code"}

    def brief(self, need: dict[str, Any], inv: dict[str, Any], include: list[str]) -> str:
        return (
            "Write a Python 3.11 file `connector.py` (standard library only) in the current directory.\n"
            "Contract: `python connector.py <source_id>` prints one JSON object "
            '{"fields": {<dotted field path>: <value>}, "url": <what was read>} and exits 0; on failure it prints '
            '{"error": "..."} and exits 1. It must only read (HTTP GET) and must never store or print identities.\n'
            "Also write `spec.json`: {\"sources\": [{\"id\", \"connector\": \"script\", \"params\": {\"source\": id, "
            "\"fields\": [...]}, \"title\"}], \"metrics\": [{\"id\", \"label\", \"expr\", \"form\", \"answers\", "
            "\"definition\", \"window\", \"unit\", \"caveats\", \"headline\"}], \"unanswered\": [...]} where expr uses "
            "only latest/increase/delta/max_over/min_over/count_items/distinct_items over \"source:field\" refs.\n"
            "Regent will run your code itself and reject it unless every value agrees with its own reads.\n\n"
            f"NEED:\n{json.dumps(need, ensure_ascii=False, indent=1)[:12000]}\n\n"
            f"WHAT REGENT OBSERVED:\n{json.dumps(inv, ensure_ascii=False, indent=1, default=str)[:20000]}\n\n"
            f"Platform sources to include: {include}\n")

    def run(self, workspace: Path, brief: str) -> dict[str, Any]:
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "BRIEF.md").write_text(brief)
        env = {k: os.environ[k] for k in ("ANTHROPIC_BASE_URL", "HTTPS_PROXY", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE",
                                          "PATH") if k in os.environ}
        env["HOME"] = str(workspace / ".home")
        t0 = time.time()
        proc = subprocess.run(
            ["claude", "-p", "Read BRIEF.md and do exactly what it asks.", "--output-format", "json",
             "--permission-mode", "acceptEdits", "--tools", "Read,Write,Edit,Glob,Grep",
             "--max-budget-usd", f"{self.budget_usd:.2f}", "--model", self.model],
            cwd=workspace, env=env, capture_output=True, text=True, timeout=1800)
        try:
            raw = json.loads(proc.stdout)
        except ValueError:
            raw = {"is_error": True, "result": (proc.stderr or proc.stdout)[:500]}
        transcript = workspace / "agent_result.json"
        transcript.write_text(json.dumps(raw, indent=1))
        return {"claimed_done": not raw.get("is_error"), "cost_usd": raw.get("total_cost_usd"),
                "seconds": round(time.time() - t0), "files": sorted(p.name for p in workspace.iterdir()),
                "transcript": str(transcript)}

    def build_capability(self, s, mission_id: str, need: dict[str, Any], inv: dict[str, Any], include: list[str]):
        """Brief the agent, then treat what it wrote as untrusted input to Regent's own pipeline."""
        from regent.schemas import CostEstimate, ToolResult
        from regent.software import capability as K
        from regent.software import verify as V
        from regent.software.need import signature

        ws = settings.workspace / "agents" / f"{mission_id}-{int(time.time())}"
        run = self.run(ws, self.brief(need, inv, include))
        spec_path = ws / "spec.json"
        if not spec_path.exists() or not (ws / "connector.py").exists():
            return ToolResult(status="failed", error="agent claimed completion but produced no connector/spec",
                              outputs={"agent": run})
        spec = json.loads(spec_path.read_text())
        for src in spec.get("sources", []):
            src["connector"] = "script"
            src.setdefault("params", {})["path"] = str(ws / "connector.py")
        cap = K.save_version(s, mission_id=mission_id, need=need, signature=signature(need), spec=spec,
                             implementation="delegated", reason="built by coding agent",
                             provenance={"agent": run, "workspace": str(ws)})
        res = V.run(s, cap)
        s.commit()
        return ToolResult(status="ok", outputs={"capability_id": cap.id, "passed": res["passed"],
                                                "coverage": res["coverage"], "summary": res["summary"],
                                                "agent_claimed_done": run["claimed_done"]},
                          cost=CostEstimate(api_usd=float(run.get("cost_usd") or 0)))


def coding_agent() -> CodingAgent:
    return CodingAgent()


def inventory() -> list[dict[str, Any]]:
    """Every resource Regent could delegate to, with its current availability."""
    from regent.software import connectors as C

    out = [get_reasoner().describe(), coding_agent().describe(),
           {"resource": "http", "backend": "httpx via acquisition fetcher", "available": True, "authority": "AUTO",
            "note": "read-only, robots.txt respected, honest user agent"},
           {"resource": "browser", "backend": "playwright chromium", "available": True, "authority": "AUTO",
            "note": "renders pages; stops at CAPTCHAs and logins (human interrupt)"},
           {"resource": "human", "backend": "principal via interrupts", "available": True, "authority": "IDENTITY",
            "note": "identity, credentials, payment approval, irreversible commitments only"}]
    for c in C.CONNECTORS.values():
        if c.access == "credential":
            out.append({"resource": f"connector:{c.id}", "available": not secrets.missing(c.credentials),
                        "missing_credentials": secrets.missing(c.credentials), "authority": "AUTO",
                        "note": c.footprint})
    return out
