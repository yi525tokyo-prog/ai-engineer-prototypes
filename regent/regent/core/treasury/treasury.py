"""Treasury / Resource model.

Tracks scarce resources (money, API spend, compute, principal attention, ...)
as event-sourced balances with a ledger. The treasury does not optimize for
lowest cost: it exposes *scarcity pressure* so the evaluator can trade cost
against expected progress, and budget guards that stop clearly wasteful
execution.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.core.observe.events import EventStore
from regent.db import LedgerEntry, Operation, Resource


class Treasury:
    def __init__(self, db: Session):
        self.db = db
        self.events = EventStore(db)

    def resources(self) -> list[Resource]:
        return list(self.db.scalars(select(Resource).order_by(Resource.id)))

    def by_kind(self, kind: str) -> Resource | None:
        return self.db.scalar(select(Resource).where(Resource.kind == kind))

    def set_resource(self, rid: str, kind: str, name: str, unit: str, balance: float,
                     limit: float | None = None, attrs: dict | None = None, source: str = "user") -> Resource:
        self.events.append("resource_changed", {"id": rid, "kind": kind, "name": name, "unit": unit,
                                                "balance": balance, "limit": limit, "attrs": attrs or {}},
                           source=source)
        return self.db.get(Resource, rid)  # type: ignore[return-value]

    def spend(self, kind: str, amount: float, *, reason: str, operation_id: str | None = None,
              mission_id: str | None = None) -> None:
        if amount <= 0:
            return
        r = self.by_kind(kind)
        if r is None:
            return
        self.events.append("resource_spent", {"resource_id": r.id, "amount": amount, "reason": reason,
                                              "operation_id": operation_id}, source="treasury", mission_id=mission_id)

    def record_operation_cost(self, op: Operation, cost: dict[str, float]) -> dict[str, float]:
        spent = {}
        mapping = {"money": "money", "api_usd": "api_spend", "attention_s": "attention"}
        for field, kind in mapping.items():
            amt = float(cost.get(field, 0) or 0)
            if kind == "attention":
                amt = amt / 60.0  # attention is budgeted in minutes
            if amt > 0:
                self.spend(kind, amt, reason=f"{op.tool}.{op.action}: {op.goal[:80]}", operation_id=op.id,
                           mission_id=op.mission_id)
                spent[kind] = amt
        return spent

    # ------------------------------------------------------------ signals

    def api_budget_left(self) -> float | None:
        r = self.by_kind("api_spend")
        return None if r is None else float(r.balance)

    def can_afford(self, cost: dict[str, float]) -> tuple[bool, str]:
        money = self.by_kind("money")
        if money is not None and float(cost.get("money", 0) or 0) > float(money.balance):
            return False, f"needs {cost['money']:,.0f} {money.unit}, have {money.balance:,.0f}"
        api = self.by_kind("api_spend")
        if api is not None and float(cost.get("api_usd", 0) or 0) > float(api.balance):
            return False, f"API budget exhausted ({api.balance:.2f} USD left)"
        return True, "ok"

    def scarcity(self) -> dict[str, Any]:
        """Pressure in [0, 1] per dimension, used to re-weight evaluation."""
        out: dict[str, Any] = {"money": 0.0, "attention": 0.0, "api": 0.0, "notes": []}
        money = self.by_kind("money")
        if money is not None:
            burn = float((money.attrs or {}).get("monthly_burn", 0) or 0)
            if burn > 0:
                runway = float(money.balance) / burn
                out["runway_months"] = round(runway, 2)
                # <1 month: full pressure; >6 months: none
                out["money"] = round(max(0.0, min(1.0, (6 - runway) / 5)), 3)
                out["notes"].append(f"runway {runway:.1f} months -> money pressure {out['money']}")
        att = self.by_kind("attention")
        if att is not None and att.limit:
            out["attention"] = round(max(0.0, 1 - float(att.balance) / float(att.limit)), 3)
        api = self.by_kind("api_spend")
        if api is not None and api.limit:
            out["api"] = round(max(0.0, 1 - float(api.balance) / float(api.limit)), 3)
        return out

    def ledger(self, limit: int = 50) -> list[LedgerEntry]:
        return list(self.db.scalars(select(LedgerEntry).order_by(LedgerEntry.event_seq.desc()).limit(limit)))
