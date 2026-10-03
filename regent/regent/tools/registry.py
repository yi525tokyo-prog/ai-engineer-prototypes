"""Tool / Service registry."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.db import BuiltTool
from regent.schemas import AUTHORITY_ORDER
from regent.tools.base import Tool
from regent.tools.builtin import built_tool, default_tools


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self.tools: dict[str, Tool] = {}
        for t in tools if tools is not None else default_tools():
            self.register(t)

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def has_action(self, tool: str, action: str) -> bool:
        t = self.tools.get(tool)
        return t is not None and action in t.actions

    def action_authority(self, tool: str, action: str) -> str:
        t = self.tools.get(tool)
        if t is None or action not in t.actions:
            return "COMMIT"  # unknown actions are treated as external mutations
        return t.actions[action].authority

    def providers_of(self, capability: str) -> list[tuple[Tool, str]]:
        out = []
        for t in self.tools.values():
            for a in t.actions.values():
                if capability in a.capabilities:
                    out.append((t, a.name))
        return out

    def capability_status(self) -> dict[str, dict[str, Any]]:
        caps: dict[str, dict[str, Any]] = {}
        for t in self.tools.values():
            for a in t.actions.values():
                for c in a.capabilities:
                    entry = caps.setdefault(c, {"providers": [], "status": "unavailable"})
                    entry["providers"].append(f"{t.name}.{a.name}")
                    status = {"live": "available", "local": "degraded", "mock": "degraded"}.get(t.backend, "unavailable")
                    order = {"available": 2, "degraded": 1, "unavailable": 0}
                    if order[status] > order[entry["status"]]:
                        entry["status"] = status
        return caps

    def catalog(self) -> list[dict[str, Any]]:
        """Compact tool.action list for model prompts."""
        return [{"tool": t.name, "action": a.name, "authority": a.authority, "description": a.description,
                 "backend": t.backend}
                for t in self.tools.values() for a in t.actions.values()]

    def describe(self) -> list[dict[str, Any]]:
        return [t.describe() for t in sorted(self.tools.values(), key=lambda t: t.name)]

    def load_built(self, db: Session) -> int:
        n = 0
        for bt in db.scalars(select(BuiltTool).where(BuiltTool.verified.is_(True))):
            s = bt.spec
            self.register(built_tool(s["tool_name"], bt.code_path, s["actions"], s["capabilities"]))
            n += 1
        return n


def max_level(*levels: str) -> str:
    return max(levels, key=lambda lv: AUTHORITY_ORDER.get(lv, 1))
