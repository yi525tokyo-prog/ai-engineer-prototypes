"""Delegatable resources: who or what Regent can hand work to, and on what terms.

Regent decides; resources do. Each resource states whether it is available, what it costs,
what authority using it consumes, and why it might not be usable right now -- so route
generation can weigh "compose it myself" against "have an agent build it" against "ask the
principal" with the same currency.

The coding agent is the Claude Code CLI working in a sandboxed workspace with file-edit tools
only -- it cannot run anything. Starting it is a COMMIT-level act (an autonomous agent writing
code that Regent will later run), so it needs the principal's opt-in
(``REGENT_CODING_AGENT=claude-code``) *and* an approved operation. What it writes is an untrusted
artifact: Regent inspects, builds, tests, runs and accepts it itself (``appbuild``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from regent.software.claude_env import claude_env
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

    def run(self, workspace: Path, brief: str | None, *, prompt: str = "Read BRIEF.md and do exactly what it asks.",
            budget_usd: float | None = None) -> dict[str, Any]:
        """One worker session in ``workspace``: file tools only, no shell. Returns what it *claims*;
        Regent verifies separately."""
        workspace.mkdir(parents=True, exist_ok=True)
        if brief is not None:
            (workspace / "BRIEF.md").write_text(brief)
        (workspace / ".home").mkdir(exist_ok=True)
        env, isolate = claude_env(workspace / ".home")
        t0 = time.time()
        before = {p.relative_to(workspace).as_posix(): p.stat().st_mtime for p in workspace.rglob("*")
                  if p.is_file() and ".home" not in p.parts}
        try:
            proc = subprocess.run(
                ["claude", "-p", prompt, "--output-format", "json", "--permission-mode", "acceptEdits",
                 "--tools", "Read,Write,Edit,Glob,Grep", "--max-budget-usd", f"{budget_usd or self.budget_usd:.2f}",
                 "--model", self.model, *isolate],
                cwd=workspace, env=env, capture_output=True, text=True, timeout=3600)
            out = proc.stdout
        except subprocess.TimeoutExpired:
            out = json.dumps({"is_error": True, "result": "timeout after 3600s"})
        try:
            raw = json.loads(out)
        except ValueError:
            raw = {"is_error": True, "result": (out or "")[:500]}
        n = len(list(workspace.glob("agent_result*.json")))
        transcript = workspace / f"agent_result{n}.json"
        transcript.write_text(json.dumps(raw, indent=1))
        after = {p.relative_to(workspace).as_posix(): p.stat().st_mtime for p in workspace.rglob("*")
                 if p.is_file() and ".home" not in p.parts}
        changed = sorted(k for k, v in after.items() if before.get(k) != v and not k.startswith("agent_result"))
        return {"claimed_done": not raw.get("is_error"), "claim": str(raw.get("result") or "")[:600],
                "cost_usd": raw.get("total_cost_usd"), "turns": raw.get("num_turns"),
                "seconds": round(time.time() - t0), "files_changed": changed, "files": sorted(after),
                "transcript": str(transcript)}


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
