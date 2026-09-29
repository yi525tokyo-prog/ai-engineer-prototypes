"""Human Interrupt Manager.

The human is a callable real-world interface. An interrupt is a structured,
bounded request -- never "what should I do?". It names the reason, the exact
action, an estimated time, the blocking operation, and a machine-checkable
resume condition. Regent resumes automatically either when the principal
responds or when the resume condition is observed to hold (e.g. the page
state changed after they solved a CAPTCHA on their own device).

Resume condition types::

    {"type": "response"}                                   # explicit response only
    {"type": "fact", "fact": "...", "op": "exists"}         # world fact appears
    {"type": "page_state", "url": "...", "selector": "#x", "expect": "confirmed"}
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.core.observe.events import EventStore
from regent.db import HumanInterrupt, Operation
from regent.ids import new_id, utcnow
from regent.schemas import Blocked, Condition

BLOCKER_ACTIONS: dict[str, tuple[str, str, int]] = {
    # type: (kind, required action template, estimated seconds)
    "captcha": ("identity", "Open {url} and complete the verification challenge", 20),
    "login": ("identity", "Sign in at {url} (Regent does not hold this credential)", 45),
    "biometric": ("identity", "Complete the biometric / passkey confirmation at {url}", 20),
    "signature": ("identity", "Sign the document at {url}", 60),
    "payment": ("identity", "Enter payment details at {url}", 60),
    "physical": ("physical", "{detail}", 300),
    "rate_limit": ("information", "Nothing to do: waiting for rate limit at {url}", 0),
}


class HumanInterruptManager:
    def __init__(self, db: Session, services: Any = None):
        self.db = db
        self.events = EventStore(db)
        self.services = services

    def open(self, mission_id: str | None = None) -> list[HumanInterrupt]:
        q = select(HumanInterrupt).where(HumanInterrupt.status == "open")
        if mission_id:
            q = q.where(HumanInterrupt.mission_id == mission_id)
        return list(self.db.scalars(q.order_by(HumanInterrupt.created_at)))

    def for_operation(self, op_id: str) -> HumanInterrupt | None:
        return self.db.scalar(select(HumanInterrupt).where(HumanInterrupt.operation_id == op_id,
                                                           HumanInterrupt.status == "open"))

    def raise_interrupt(self, op: Operation | None, *, mission_id: str, kind: str, reason: str,
                        required_action: str, estimated_time_seconds: int, resume_condition: dict[str, Any],
                        response_schema: dict[str, Any] | None = None, context: dict[str, Any] | None = None
                        ) -> HumanInterrupt:
        if op is not None:
            existing = self.for_operation(op.id)
            if existing is not None:
                return existing
        hi = HumanInterrupt(
            id=new_id("hi"), mission_id=mission_id, operation_id=op.id if op else None, kind=kind, reason=reason,
            required_action=required_action, estimated_time_seconds=estimated_time_seconds,
            blocking_operation=f"{op.tool}.{op.action}: {op.goal}" if op else None,
            resume_condition=resume_condition, response_schema=response_schema or {}, context=context or {},
        )
        self.db.add(hi)
        if op is not None:
            op.status = "waiting_human"
        self.db.flush()
        self.events.append("human_interrupt_raised", {
            "interrupt_id": hi.id, "operation_id": hi.operation_id, "kind": kind, "reason": reason,
            "required_action": required_action, "estimated_time_seconds": estimated_time_seconds,
            "resume_condition": resume_condition}, source="regent", mission_id=mission_id)
        return hi

    def from_blocker(self, op: Operation, blocker: Blocked) -> HumanInterrupt:
        kind, tpl, secs = BLOCKER_ACTIONS.get(blocker.type, ("identity", "Resolve blocker at {url}: {detail}", 60))
        url = blocker.url or (op.inputs or {}).get("url") or ""
        resume: dict[str, Any] = {"type": "response"}
        if op.tool == "browser" and url:
            # Resume when the page no longer shows the blocker (the human cleared it).
            resume = {"type": "page_state", "url": url, "blocker_absent": blocker.type}
        return self.raise_interrupt(
            op, mission_id=op.mission_id, kind=kind,
            reason=f"{blocker.detail or blocker.type} while executing '{op.goal}'",
            required_action=tpl.format(url=url, detail=blocker.detail), estimated_time_seconds=secs,
            resume_condition=resume,
            response_schema={"done": {"type": "boolean", "label": "Done"}},
            context={"url": url, "blocker": blocker.model_dump()},
        )

    def for_authorization(self, op: Operation, reason: str) -> HumanInterrupt:
        amount = (op.inputs or {}).get("amount")
        to = (op.inputs or {}).get("to")
        what = f"{op.tool}.{op.action}"
        detail = f" amount {amount:,.0f}" if isinstance(amount, (int, float)) else ""
        detail += f" to {', '.join(to) if isinstance(to, list) else to}" if to else ""
        return self.raise_interrupt(
            op, mission_id=op.mission_id, kind="authorization", reason=reason,
            required_action=f"Approve or deny: {op.goal} ({what}{detail})", estimated_time_seconds=10,
            resume_condition={"type": "response"},
            response_schema={"approve": {"type": "boolean", "label": "Approve"},
                             "grant_standing": {"type": "boolean", "label": "Always allow this kind of action"}},
            context={"operation": what, "inputs": op.inputs},
        )

    def for_human_operation(self, op: Operation) -> HumanInterrupt:
        i = op.inputs or {}
        return self.raise_interrupt(
            op, mission_id=op.mission_id, kind=i.get("kind", "physical"), reason=i.get("reason", op.goal),
            required_action=i.get("required_action", op.goal),
            estimated_time_seconds=int(i.get("estimated_time_seconds", 60)),
            resume_condition=i.get("resume_condition", {"type": "response"}),
            response_schema=i.get("response_schema", {"done": {"type": "boolean"}}), context=i.get("context", {}),
        )

    # ------------------------------------------------------------ resolve

    def resolve(self, interrupt_id: str, response: dict[str, Any], *, resolution: str = "completed",
                source: str = "principal") -> HumanInterrupt:
        hi = self.db.get(HumanInterrupt, interrupt_id)
        if hi is None:
            raise KeyError(interrupt_id)
        if hi.status != "open":
            return hi
        hi.status = "resolved"
        hi.resolution = resolution
        hi.response = response
        hi.resolved_at = utcnow()
        op = self.db.get(Operation, hi.operation_id) if hi.operation_id else None
        if op is not None and op.status == "waiting_human":
            if hi.kind == "authorization":
                if response.get("approve"):
                    op.authority_decision = {**(op.authority_decision or {}), "approved_once": True,
                                             "approved_by": source, "interrupt_id": hi.id}
                    op.status = "pending"
                    if response.get("grant_standing"):
                        from regent.core.authority.manager import AuthorityManager
                        from regent.runtime import get_services

                        AuthorityManager(self.db, get_services().tools).grant(
                            f"{op.tool}.{op.action}", "COMMIT", note=f"granted while approving {hi.id}")
                else:
                    op.status = "cancelled"
                    op.error = "denied by principal"
            elif op.tool == "human":
                op.status = "unverified"
                op.outputs = {**(op.outputs or {}), **response, "resolved_by": source}
            else:
                op.status = "pending"  # re-execute; the tool resumes from where the human unblocked it
                op.inputs = {**(op.inputs or {}), "_after_human": hi.id}
        self.db.flush()
        if source == "principal" or resolution == "condition_observed":
            from regent.core.treasury.treasury import Treasury

            Treasury(self.db).spend("attention", (hi.estimated_time_seconds or 0) / 60.0,
                                    reason=f"human interrupt: {hi.required_action[:80]}",
                                    operation_id=hi.operation_id, mission_id=hi.mission_id)
        self.events.append("human_completed_action", {"interrupt_id": hi.id, "operation_id": hi.operation_id,
                                                      "resolution": resolution, "response": response,
                                                      "kind": hi.kind},
                           source=source, mission_id=hi.mission_id)
        return hi

    def check_resume_conditions(self, facts: dict[str, Any], present: set[str]) -> list[HumanInterrupt]:
        """Auto-resolve interrupts whose resume condition now holds."""
        resolved = []
        for hi in self.open():
            rc = hi.resume_condition or {}
            t = rc.get("type")
            if t == "fact":
                cond = Condition(fact=rc["fact"], op=rc.get("op", "exists"), value=rc.get("value"))
                if cond.holds(facts, present):
                    resolved.append(self.resolve(hi.id, {"observed": rc}, resolution="condition_observed",
                                                 source="regent:resume_condition"))
            elif t == "page_state" and self.services is not None:
                if self._page_cleared(rc):
                    resolved.append(self.resolve(hi.id, {"observed": rc}, resolution="condition_observed",
                                                 source="regent:resume_condition"))
        return resolved

    def _page_cleared(self, rc: dict[str, Any]) -> bool:
        from regent.browser.driver import detect_blockers
        import httpx

        # Cheap check first: fetch the page and look for the blocker. The full browser
        # re-runs the operation afterwards, so a false positive only costs one retry.
        try:
            r = httpx.get(rc["url"], timeout=10, follow_redirects=True)
        except Exception:
            return False
        b = detect_blockers(r.text, rc["url"], r.status_code)
        if b is not None and b.type == rc.get("blocker_absent"):
            return False
        if rc.get("selector") and rc.get("expect"):
            return rc["expect"] in r.text
        return True
