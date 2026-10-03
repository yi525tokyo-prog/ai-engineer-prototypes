"""Constitution Model.

The human does not write a philosophical constitution. Items are inferred and
updated from explicit preferences, accepted/rejected proposals, overrides and
repeated decisions. Each item carries a Beta(alpha, beta) belief; confidence is
its mean. Items are grouped into hard constraints, strong/weak preferences,
current priorities and unresolved conflicts.

Items influence the evaluator in two ways:

* ``dimension`` in an evaluator component (``optionality``, ``money_cost``...)
  scales that component's weight;
* ``dimension`` of the form ``tag:<name>`` adds a bonus/penalty to routes
  carrying that tag.

Hard constraints carry a ``rule`` and remove violating routes from contention.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.core.observe.events import EventStore
from regent.db import ConstitutionItem, Route
from regent.ids import new_id, utcnow

STRONG_THRESHOLD = 0.72
# Dimensions where "more" is better for the principal. Cost-like dimensions are
# handled by direction=-1 on the item ("prefer less money_cost" etc.).
INFERABLE_DIMENSIONS = {
    "optionality": 1.0, "reversibility": 1.0, "information_gain": 1.0,
    "risk": -1.0, "money_cost": -1.0, "time_cost_hours": -1.0, "expected_upside": 1.0,
}


class ConstitutionModel:
    def __init__(self, db: Session):
        self.db = db
        self.events = EventStore(db)

    # ------------------------------------------------------------- reading

    def items(self, include_retired: bool = False) -> list[ConstitutionItem]:
        q = select(ConstitutionItem)
        if not include_retired:
            q = q.where(ConstitutionItem.status == "active")
        return list(self.db.scalars(q.order_by(ConstitutionItem.id)))

    def grouped(self) -> dict[str, list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {
            "hard_constraints": [], "strong_preferences": [], "weak_preferences": [],
            "priorities": [], "conflicts": [],
        }
        for it in self.items():
            key = {
                "hard_constraint": "hard_constraints", "strong_preference": "strong_preferences",
                "weak_preference": "weak_preferences", "priority": "priorities", "conflict": "conflicts",
            }.get(it.type, "weak_preferences")
            groups[key].append(self.as_dict(it))
        return groups

    @staticmethod
    def as_dict(it: ConstitutionItem) -> dict[str, Any]:
        return {
            "id": it.id, "type": it.type, "statement": it.statement, "confidence": round(it.confidence, 3),
            "dimension": it.dimension, "direction": it.direction, "rule": it.rule,
            "source": it.sources or [], "evidence_count": round((it.alpha or 1) + (it.beta or 1) - 2, 2),
        }

    # ------------------------------------------------------------- writing

    def upsert(self, *, id: str | None = None, type: str, statement: str, dimension: str | None = None,
               direction: float = 1.0, confidence: float | None = None, rule: dict | None = None,
               source: dict | None = None, strength: float = 4.0) -> ConstitutionItem:
        """Explicit statement from the principal (or seed). Confidence becomes a Beta prior."""
        item_id = id or new_id("con")
        existing = self.db.get(ConstitutionItem, item_id)
        conf = confidence if confidence is not None else (0.95 if type == "hard_constraint" else 0.7)
        alpha = max(conf * strength, 0.01)
        beta = max((1 - conf) * strength, 0.01)
        payload: dict[str, Any] = {
            "id": item_id, "type": type, "statement": statement, "dimension": dimension,
            "direction": direction, "rule": rule or {}, "confidence": conf,
            "alpha": alpha, "beta": beta, "status": "active",
        }
        if existing is None:
            payload["sources"] = [source or {"kind": "explicit", "at": utcnow().isoformat()}]
        else:
            payload["add_source"] = source or {"kind": "explicit", "at": utcnow().isoformat()}
        self.events.append("constitution_item_upserted", payload, source="constitution")
        self._refresh_conflicts()
        return self.db.get(ConstitutionItem, item_id)  # type: ignore[return-value]

    def _update_belief(self, item_id: str, *, statement: str, dimension: str, direction: float,
                       support: float, against: float, source: dict) -> ConstitutionItem:
        it = self.db.get(ConstitutionItem, item_id)
        alpha = (it.alpha if it else 1.0) + support
        beta = (it.beta if it else 1.0) + against
        conf = alpha / (alpha + beta)
        type_ = "strong_preference" if conf >= STRONG_THRESHOLD and alpha + beta >= 5 else "weak_preference"
        if it is not None and it.type in ("hard_constraint", "priority"):
            type_ = it.type
        payload = {
            "id": item_id, "type": type_, "statement": statement, "dimension": dimension,
            "direction": direction, "alpha": alpha, "beta": beta, "confidence": round(conf, 4),
            "status": "active",
        }
        if it is None:
            payload["sources"] = [source]
        else:
            payload["add_source"] = source
        self.events.append("constitution_item_upserted", payload, source="constitution")
        return self.db.get(ConstitutionItem, item_id)  # type: ignore[return-value]

    def observe_choice(self, accepted: Route, rejected: list[Route], *, kind: str,
                       decision_id: str | None = None) -> list[dict[str, Any]]:
        """Infer preferences from the principal choosing ``accepted`` over ``rejected``.

        For every inferable dimension where the accepted route differs materially
        from a rejected one, add support for "prefer more/less of that dimension".
        Tags present on one side only become tag preferences.
        """
        updates: list[dict[str, Any]] = []
        acc = accepted.effective or accepted.estimates or {}
        for rej in rejected:
            rv = rej.effective or rej.estimates or {}
            for dim, polarity in INFERABLE_DIMENSIONS.items():
                a, b = float(acc.get(dim, 0) or 0), float(rv.get(dim, 0) or 0)
                scale = max(abs(a), abs(b), 1e-9)
                if dim in ("money_cost", "time_cost_hours", "expected_upside"):
                    rel = (a - b) / scale
                else:
                    rel = a - b
                if abs(rel) < 0.15:
                    continue
                # prefers_more: accepted has more of dim than rejected
                prefers_more = rel > 0
                direction = 1.0 if prefers_more == (polarity > 0) else -1.0
                word = "higher" if prefers_more else "lower"
                item_id = f"inferred:{dim}"
                existing = self.db.get(ConstitutionItem, item_id)
                if existing is not None and existing.direction != direction:
                    support, against = 0.0, min(abs(rel), 1.0)
                    statement, dir_ = existing.statement, existing.direction
                else:
                    support, against = min(abs(rel), 1.0), 0.0
                    statement, dir_ = f"prefer {word} {dim.replace('_', ' ')}", direction
                it = self._update_belief(item_id, statement=statement, dimension=dim, direction=dir_,
                                         support=support, against=against,
                                         source={"kind": kind, "decision_id": decision_id,
                                                 "accepted": accepted.id, "rejected": rej.id,
                                                 "at": utcnow().isoformat()})
                updates.append(self.as_dict(it))
            for tag in set(accepted.tags or []) - set(rej.tags or []):
                it = self._update_belief(f"inferred:tag:{tag}", statement=f"favour routes that {tag.replace('_', ' ')}",
                                         dimension=f"tag:{tag}", direction=1.0, support=0.6, against=0.0,
                                         source={"kind": kind, "decision_id": decision_id, "accepted": accepted.id})
                updates.append(self.as_dict(it))
            for tag in set(rej.tags or []) - set(accepted.tags or []):
                it = self._update_belief(f"inferred:tag:{tag}", statement=f"favour routes that {tag.replace('_', ' ')}",
                                         dimension=f"tag:{tag}", direction=1.0, support=0.0, against=0.6,
                                         source={"kind": kind, "decision_id": decision_id, "rejected": rej.id})
                updates.append(self.as_dict(it))
        self._refresh_conflicts()
        return updates

    def _refresh_conflicts(self) -> None:
        """Two confident items pulling the same dimension in opposite directions."""
        items = [i for i in self.items() if i.type != "conflict" and i.dimension]
        live_conflicts = set()
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if a.dimension == b.dimension and a.direction * b.direction < 0 \
                        and a.confidence > 0.5 and b.confidence > 0.5:
                    cid = f"conflict:{min(a.id, b.id)}:{max(a.id, b.id)}"
                    live_conflicts.add(cid)
                    if self.db.get(ConstitutionItem, cid) is None:
                        self.events.append("constitution_item_upserted", {
                            "id": cid, "type": "conflict", "status": "active",
                            "statement": f"'{a.statement}' conflicts with '{b.statement}'",
                            "dimension": None, "confidence": round(min(a.confidence, b.confidence), 3),
                            "sources": [{"kind": "detected", "items": [a.id, b.id]}],
                        }, source="constitution")
        for c in [i for i in self.items() if i.type == "conflict" and not i.id.startswith("conflict:plan:")]:
            if c.id not in live_conflicts:
                self.events.append("constitution_item_upserted", {"id": c.id, "status": "resolved"},
                                   source="constitution")

    # ------------------------------------------------------- use in evaluation

    @staticmethod
    def in_scope(it: ConstitutionItem, mission_tags: list[str] | None) -> bool:
        """Items may be scoped to missions carrying certain tags (rule.scope_tags)."""
        scope = (it.rule or {}).get("scope_tags")
        return not scope or mission_tags is None or bool(set(scope) & set(mission_tags))

    def weight_multipliers(self, mission_tags: list[str] | None = None) -> tuple[dict[str, float], list[dict[str, Any]]]:
        mult: dict[str, float] = {}
        why: list[dict[str, Any]] = []
        for it in self.items():
            if not self.in_scope(it, mission_tags):
                continue
            if not it.dimension or it.dimension.startswith("tag:") or it.type in ("hard_constraint", "conflict"):
                continue
            k = {"strong_preference": 1.0, "weak_preference": 0.5, "priority": 1.5}.get(it.type, 0.5)
            # direction +1: the principal cares more about this dimension (in its natural
            # sense: more optionality, less risk); -1: they care less than the default.
            factor = 1.0 + k * it.confidence * (1.0 if it.direction > 0 else -0.5)
            mult[it.dimension] = mult.get(it.dimension, 1.0) * max(factor, 0.2)
            why.append({"item": it.id, "statement": it.statement, "dimension": it.dimension,
                        "factor": round(factor, 3), "confidence": round(it.confidence, 3)})
        return mult, why

    def tag_bonuses(self, mission_tags: list[str] | None = None) -> dict[str, tuple[float, str]]:
        out: dict[str, tuple[float, str]] = {}
        for it in self.items():
            if not self.in_scope(it, mission_tags):
                continue
            if it.dimension and it.dimension.startswith("tag:") and it.type != "conflict":
                k = {"strong_preference": 0.2, "weak_preference": 0.08, "priority": 0.25,
                     "hard_constraint": 0.0}.get(it.type, 0.06)
                strength = (it.confidence - 0.5) * 2  # -1..1: below 0.5 confidence means dispreferred
                out[it.dimension[4:]] = (k * strength * it.direction, it.id)
        return out

    def hard_constraint_violations(self, route: Route, facts: dict[str, Any] | None = None) -> list[str]:
        out = []
        eff = route.effective or route.estimates or {}
        for it in self.items():
            if it.type != "hard_constraint" or not it.rule:
                continue
            r = it.rule
            if "forbid_tag" in r and r["forbid_tag"] in (route.tags or []):
                out.append(f"{it.statement} (route tagged '{r['forbid_tag']}')")
            if "max" in r:
                f, v = r["max"]["field"], float(r["max"]["value"])
                if float(eff.get(f, 0) or 0) > v:
                    out.append(f"{it.statement} ({f}={eff.get(f)} > {v})")
            if "min" in r:
                f, v = r["min"]["field"], float(r["min"]["value"])
                if float(eff.get(f, 0) or 0) < v:
                    out.append(f"{it.statement} ({f}={eff.get(f)} < {v})")
        return out
