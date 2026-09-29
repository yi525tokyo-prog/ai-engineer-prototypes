"""World State: a read model over the projections.

``WorldView`` is the structured representation of reality the rest of the loop
reasons over. It is loaded fresh every tick, so it always reflects the event log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.db import Capability, ConstitutionItem, Entity, Fact, Relation, Resource, Snapshot

UNKNOWN = object()


@dataclass
class WorldView:
    entities: dict[str, Entity] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)
    facts: dict[str, Fact] = field(default_factory=dict)
    resources: dict[str, Resource] = field(default_factory=dict)
    capabilities: dict[str, Capability] = field(default_factory=dict)
    constitution: list[ConstitutionItem] = field(default_factory=list)
    event_seq: int = 0
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ---- loading

    @classmethod
    def load(cls, db: Session) -> "WorldView":
        from regent.core.observe.events import EventStore

        return cls(
            entities={e.id: e for e in db.scalars(select(Entity))},
            relations=list(db.scalars(select(Relation))),
            facts={f.key: f for f in db.scalars(select(Fact))},
            resources={r.id: r for r in db.scalars(select(Resource))},
            capabilities={c.id: c for c in db.scalars(select(Capability))},
            constitution=list(db.scalars(select(ConstitutionItem).where(ConstitutionItem.status == "active"))),
            event_seq=EventStore(db).head(),
        )

    # ---- queries

    def fact(self, key: str, default: Any = None) -> Any:
        f = self.facts.get(key)
        return default if f is None else f.value

    def fact_map(self) -> tuple[dict[str, Any], set[str]]:
        """Facts plus derived facts (capability.<id> -> status, treasury.*) for condition checks."""
        d: dict[str, Any] = {k: f.value for k, f in self.facts.items()}
        for c in self.capabilities.values():
            d[f"capability.{c.id}"] = c.status
        rm = self.runway_months()
        if rm is not None and "treasury.runway_months" not in d:
            d["treasury.runway_months"] = rm
        return d, set(d.keys())

    def has_fact(self, key: str) -> bool:
        return key in self.facts

    def of_kind(self, kind: str) -> list[Entity]:
        return sorted((e for e in self.entities.values() if e.kind == kind), key=lambda e: e.id)

    def related(self, eid: str, rel: str | None = None, direction: str = "out") -> list[Entity]:
        out = []
        for r in self.relations:
            if rel and r.rel != rel:
                continue
            if direction == "out" and r.src_id == eid and r.dst_id in self.entities:
                out.append(self.entities[r.dst_id])
            elif direction == "in" and r.dst_id == eid and r.src_id in self.entities:
                out.append(self.entities[r.src_id])
        return out

    def money(self) -> Resource | None:
        for r in self.resources.values():
            if r.kind == "money":
                return r
        return None

    def capability_available(self, cap_id: str) -> bool:
        c = self.capabilities.get(cap_id)
        return c is not None and c.status in ("available", "degraded")

    def unanswered_messages(self) -> list[Entity]:
        return [m for m in self.of_kind("message")
                if m.attrs.get("requires_reply") and not m.attrs.get("answered")]

    def runway_months(self) -> float | None:
        m = self.money()
        if m is None:
            return None
        burn = (m.attrs or {}).get("monthly_burn")
        if not burn:
            return None
        return round(float(m.balance) / float(burn), 2)

    def summary(self, max_entities: int = 60) -> dict[str, Any]:
        """Compact, provider-neutral description used in model prompts and the UI."""
        ents = sorted(self.entities.values(), key=lambda e: (e.kind, e.id))[:max_entities]
        return {
            "event_seq": self.event_seq,
            "entities": [{"id": e.id, "kind": e.kind, "name": e.name, "attrs": e.attrs} for e in ents],
            "relations": [{"src": r.src_id, "rel": r.rel, "dst": r.dst_id} for r in self.relations[:200]],
            "facts": {k: f.value for k, f in sorted(self.facts.items()) if not k.startswith("tool.")},
            "resources": [{"id": r.id, "kind": r.kind, "name": r.name, "balance": r.balance,
                           "unit": r.unit, "attrs": r.attrs} for r in self.resources.values()],
            "capabilities": {c.id: c.status for c in self.capabilities.values()},
            "runway_months": self.runway_months(),
        }


def diff_states(before: dict[str, list[dict]], after: dict[str, list[dict]]) -> dict[str, Any]:
    """Structural diff between two serialized projections (snapshots)."""
    keys = {"entities": "id", "relations": "id", "facts": "key", "resources": "id",
            "capabilities": "id", "constitution": "id", "authority_grants": "id"}
    out: dict[str, Any] = {}
    ignore = {"updated_at", "last_event_seq", "version", "created_at"}
    for table, pk in keys.items():
        a = {r[pk]: r for r in before.get(table, [])}
        b = {r[pk]: r for r in after.get(table, [])}
        added = [b[k] for k in b.keys() - a.keys()]
        removed = [a[k] for k in a.keys() - b.keys()]
        changed = []
        for k in a.keys() & b.keys():
            fields = {f: {"from": a[k].get(f), "to": b[k].get(f)}
                      for f in b[k] if f not in ignore and a[k].get(f) != b[k].get(f)}
            if fields:
                changed.append({pk: k, "fields": fields})
        if added or removed or changed:
            out[table] = {"added": added, "removed": removed, "changed": changed}
    return out


def what_changed(db: Session, snapshot_a: str, snapshot_b: str) -> dict[str, Any]:
    a = db.get(Snapshot, snapshot_a)
    b = db.get(Snapshot, snapshot_b)
    if a is None or b is None:
        raise KeyError("snapshot not found")
    return diff_states(a.state, b.state)
