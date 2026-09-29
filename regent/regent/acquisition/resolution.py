"""Generic entity resolution.

A domain resolver supplies blocking keys, a pairwise probability model and a
feature merge. This module does the bookkeeping:

* candidates come from the same block (and the same parent, for child entities);
* the best candidate's probability decides: >= ``merge_at`` merge,
  ``ambiguous_at``..``merge_at`` keep separate but record an *ambiguous* link,
  below that create a new entity;
* every decision, including non-merges, is written to ``acq_links`` with the
  features that produced it -- resolution is auditable, never silent.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.acquisition.tables import AcqEntity, AcqLink, AcqMention
from regent.ids import new_id, utcnow


class DomainResolver(Protocol):
    def block_keys(self, entity_type: str, features: dict[str, Any]) -> tuple[str, list[str]]:
        """(key to store on a new entity, key prefixes to search for candidates)."""

    def score(self, entity_type: str, mention: dict[str, Any], entity: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Probability that the mention and the entity are the same real-world thing."""

    def merge(self, entity_type: str, entity: dict[str, Any], mention: dict[str, Any]) -> dict[str, Any]: ...

    def label(self, entity_type: str, features: dict[str, Any]) -> str: ...


class EntityResolution:
    def __init__(self, db: Session, domain: str, resolver: DomainResolver, *, merge_at: float = 0.85,
                 ambiguous_at: float = 0.5):
        self.db = db
        self.domain = domain
        self.resolver = resolver
        self.merge_at = merge_at
        self.ambiguous_at = ambiguous_at
        self.stats = {"merged": 0, "new": 0, "ambiguous": 0}

    def candidates(self, entity_type: str, prefixes: list[str], parent_id: str | None) -> list[AcqEntity]:
        q = select(AcqEntity).where(AcqEntity.domain == self.domain, AcqEntity.entity_type == entity_type,
                                    AcqEntity.status == "active")
        if parent_id is not None:
            q = q.where(AcqEntity.parent_id == parent_id)
        out: dict[str, AcqEntity] = {}
        for p in prefixes:
            if p:
                for e in self.db.scalars(q.where(AcqEntity.block_key.like(p + "%"))):
                    out[e.id] = e
            elif parent_id is not None:
                for e in self.db.scalars(q):
                    out[e.id] = e
        return list(out.values())

    def rank(self, entity_type: str, features: dict[str, Any], parent_id: str | None = None):
        store_key, prefixes = self.resolver.block_keys(entity_type, features)
        scored = []
        for e in self.candidates(entity_type, prefixes, parent_id):
            p, detail = self.resolver.score(entity_type, features, e.features or {})
            scored.append((p, e, detail))
        scored.sort(key=lambda x: -x[0])
        return store_key, scored

    def resolve(self, mention: AcqMention, entity_type: str, features: dict[str, Any], *,
                parent_id: str | None = None, request_id: str | None = None,
                child: tuple[str, dict[str, Any]] | None = None) -> tuple[AcqEntity, str, float]:
        """``child``: (child_type, child_features) of a record nested in this mention. When the
        parent match is ambiguous, a near-certain child match inside a candidate is combined
        with it (log-odds sum) -- two partial agreements make strong joint evidence."""
        store_key, scored = self.rank(entity_type, features, parent_id)
        if child is not None and scored and self.ambiguous_at <= scored[0][0] < self.merge_at:
            import math

            rescored = []
            for p, e, detail in scored:
                if p >= self.ambiguous_at:
                    kids = self.db.scalars(select(AcqEntity).where(AcqEntity.parent_id == e.id,
                                                                   AcqEntity.entity_type == child[0],
                                                                   AcqEntity.status == "active"))
                    best_child = max((self.resolver.score(child[0], child[1], k.features or {})[0] for k in kids),
                                     default=0.0)
                    if best_child >= 0.9:
                        lo = math.log(p / (1 - p)) + math.log(best_child / (1 - best_child)) - 1.0
                        p2 = 1 / (1 + math.exp(-lo))
                        detail = {**detail, "joint_child_match": round(best_child, 3), "joint_p": round(p2, 4)}
                        p = p2
                rescored.append((p, e, detail))
            scored = sorted(rescored, key=lambda x: -x[0])
        best = scored[0] if scored else None
        if best is not None and best[0] >= self.merge_at:
            p, ent, detail = best
            ent.features = self.resolver.merge(entity_type, ent.features or {}, features)
            ent.mention_count = (ent.mention_count or 0) + 1
            ent.updated_at = utcnow()
            decision = "merged"
        else:
            ent = AcqEntity(id=new_id(entity_type[:2]), domain=self.domain, entity_type=entity_type,
                            parent_id=parent_id, block_key=store_key,
                            features=self.resolver.merge(entity_type, {}, features),
                            label=self.resolver.label(entity_type, features), mention_count=1,
                            request_id=request_id, beliefs={}, source_hosts=[])
            self.db.add(ent)
            decision = "ambiguous" if best is not None and best[0] >= self.ambiguous_at else "new"
            p = best[0] if best else 0.0
        self.stats[decision] += 1
        for sp, se, sd in scored[:3]:
            self.db.add(AcqLink(id=new_id("lnk"), mention_id=mention.id, entity_id=se.id, probability=round(sp, 4),
                                decision=("merged" if se is ent else ("ambiguous" if sp >= self.ambiguous_at else "rejected")),
                                features=sd))
        if decision != "merged":
            # the creation itself is a decision too: "no candidate was close enough"
            self.db.add(AcqLink(id=new_id("lnk"), mention_id=mention.id, entity_id=ent.id, probability=1.0,
                                decision="new", features={"candidates": len(scored), "best_p": round(p, 4),
                                                          "block": store_key}))
        mention.entity_id = ent.id
        mention.resolution = {"decision": decision, "probability": round(p, 4),
                              "candidates": [{"entity_id": se.id, "p": round(sp, 4)} for sp, se, _ in scored[:3]]}
        self.db.flush()
        return ent, decision, p
