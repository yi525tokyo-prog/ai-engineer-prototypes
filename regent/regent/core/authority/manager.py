"""Authority Manager.

Three levels:

* ``AUTO``     -- Regent may execute independently (research, analysis, local files, drafts).
* ``COMMIT``   -- external-world mutation (send, book, buy, cancel, publish, spend).
* ``IDENTITY`` -- requires the human's actual identity or body (CAPTCHA, biometric,
                  signature, regulated confirmation, physical inspection).

Grants are configurable, persistent and event-sourced (``permission_changed``).
When authority already exists Regent does not ask. When it does not, the
result is a *bounded* interrupt (approve/deny a specific action), never an
open-ended question.
"""

from __future__ import annotations

import fnmatch
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.core.observe.events import EventStore
from regent.core.world.state import WorldView
from regent.db import AuthorityGrant, Operation
from regent.ids import new_id
from regent.schemas import AUTHORITY_ORDER
from regent.tools.registry import ToolRegistry, max_level

MUTATING_BROWSER_STEPS = {"click", "type", "upload"}


@dataclass
class AuthorityDecision:
    level: str
    allowed: bool
    reason: str
    grant_id: str | None = None
    needs: str | None = None  # None | "approval" | "identity"
    constraints_checked: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuthorityManager:
    def __init__(self, db: Session, tools: ToolRegistry):
        self.db = db
        self.tools = tools
        self.events = EventStore(db)

    # ---------------------------------------------------------- classify

    def classify(self, tool: str, action: str, inputs: dict[str, Any] | None = None,
                 declared: str | None = None) -> str:
        level = self.tools.action_authority(tool, action)
        if tool == "browser" and action == "run":
            steps = (inputs or {}).get("steps", [])
            if any(s.get("do") in MUTATING_BROWSER_STEPS for s in steps):
                level = max_level(level, "COMMIT")
        if declared:
            level = max_level(level, declared)  # a spec may raise, never lower, authority
        return level

    # ------------------------------------------------------------ grants

    def grants(self) -> list[AuthorityGrant]:
        return list(self.db.scalars(select(AuthorityGrant).order_by(AuthorityGrant.id)))

    def grant(self, scope: str, level: str = "COMMIT", constraints: dict | None = None, note: str = "",
              grant_id: str | None = None, granted: bool = True, source: str = "principal") -> AuthorityGrant:
        gid = grant_id or new_id("grant")
        self.events.append("permission_changed", {"id": gid, "scope": scope, "level": level, "granted": granted,
                                                  "constraints": constraints or {}, "note": note}, source=source)
        return self.db.get(AuthorityGrant, gid)  # type: ignore[return-value]

    def revoke(self, grant_id: str, source: str = "principal") -> None:
        self.events.append("permission_changed", {"id": grant_id, "granted": False}, source=source)

    # ------------------------------------------------------------ decide

    def decide(self, op: Operation, world: WorldView) -> AuthorityDecision:
        level = op.required_authority or self.classify(op.tool, op.action, op.inputs)
        if level == "AUTO":
            return AuthorityDecision(level, True, "AUTO: within Regent's independent authority")
        if level == "IDENTITY":
            return AuthorityDecision(level, False, "IDENTITY: requires the principal in person", needs="identity")
        # one-shot approval given through a resolved authorization interrupt
        if (op.authority_decision or {}).get("approved_once"):
            return AuthorityDecision(level, True, "COMMIT: approved by principal for this operation",
                                     grant_id="one-shot-approval")
        scope = f"{op.tool}.{op.action}"
        for g in self.grants():
            if not g.granted or AUTHORITY_ORDER.get(g.level, 0) < AUTHORITY_ORDER["COMMIT"]:
                continue
            if not fnmatch.fnmatch(scope, g.scope):
                continue
            ok, checked, why = self._constraints_ok(g.constraints or {}, op, world)
            if ok:
                return AuthorityDecision(level, True, f"COMMIT: standing grant '{g.scope}' ({g.note or g.id})",
                                         grant_id=g.id, constraints_checked=checked)
            last_fail = why
            _ = last_fail
        return AuthorityDecision(level, False, f"COMMIT: no standing grant covers {scope} with these inputs",
                                 needs="approval")

    def _constraints_ok(self, c: dict[str, Any], op: Operation, world: WorldView) -> tuple[bool, list[str], str]:
        checked: list[str] = []
        inputs = op.inputs or {}
        if "max_amount" in c:
            amount = float(inputs.get("amount", op.cost_estimate.get("money", 0) if op.cost_estimate else 0) or 0)
            checked.append(f"amount {amount:,.0f} <= {c['max_amount']:,.0f}")
            if amount > float(c["max_amount"]):
                return False, checked, "amount exceeds grant"
        if c.get("recipients") == "known_contacts":
            to = inputs.get("to") or []
            if isinstance(to, str):
                to = [to]
            unknown = [r for r in to if not (world.entities.get(r) and world.entities[r].attrs.get("known_contact"))]
            checked.append(f"recipients known: {not unknown}")
            if not to or unknown:
                return False, checked, f"unknown recipients {unknown}"
        if "domains" in c:
            url = str(inputs.get("url", ""))
            ok = any(d in url for d in c["domains"])
            checked.append(f"url domain allowed: {ok}")
            if not ok:
                return False, checked, "domain not allowed"
        return True, checked, ""
