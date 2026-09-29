"""Claim store and belief computation: a world of competing, evidence-backed hypotheses.

For every (entity, attribute) the store keeps *all* claims. A belief is
computed, never stored as truth:

    weight(claim)   = reliability(source) x extractor_confidence x freshness(age, ttl)
    support(value)  = 1 - prod(1 - max weight per independent source)
    share(value)    = support(value) / sum(support)
    confidence(top) = share(top) x support(top)

* Values agree within the attribute's tolerance (e.g. rent within 0.5 %).
* Claims from the same host are not independent: per host only its latest
  observation window counts (a source that updated its own listing supersedes
  itself, not other sources).
* A conflict is two or more hypotheses each holding >= 20 % of the support.
  Conflicts are recorded, never silently resolved by overwriting.
* Freshness: past its TTL a claim's weight decays exponentially and the
  attribute is flagged stale -- which schedules a recheck.
"""

from __future__ import annotations

import math
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.acquisition.tables import AcqClaim, AcqConflict, AcqEntity, AcqSource
from regent.acquisition.types import SOURCE_KIND_PRIOR, AttrSpec, ClaimIn, FreshnessPolicy
from regent.ids import new_id, utcnow

SAME_WINDOW = timedelta(hours=1)
CONFLICT_SHARE = 0.2


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def value_key(spec: AttrSpec | None, value: Any) -> str:
    if value is None:
        return "null"
    if spec is not None and spec.kind == "number":
        try:
            return f"{float(value):g}"
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value, sort_keys=True, ensure_ascii=False)[:200]
    return unicodedata.normalize("NFKC", str(value)).strip().lower()[:200]


def freshness(age_s: float, ttl_s: float) -> float:
    if age_s <= ttl_s:
        return 1.0
    return max(0.05, math.exp(-(age_s - ttl_s) / max(ttl_s, 1.0)))


class ClaimStore:
    def __init__(self, db: Session, policy: FreshnessPolicy, *, now: datetime | None = None):
        self.db = db
        self.policy = policy
        self._now = now
        self._rel_cache: dict[str, float] = {}

    def now(self) -> datetime:
        return self._now or utcnow()

    # ---------------------------------------------------------------- write

    def add(self, entity_id: str, c: ClaimIn, *, source_host: str, source_kind: str, url: str = "",
            document_id: str | None = None, mention_id: str | None = None) -> AcqClaim:
        spec = self.policy.specs.get(c.attribute)
        row = AcqClaim(id=new_id("clm"), entity_id=entity_id, attribute=c.attribute, value=c.value,
                       value_key=value_key(spec, c.value), source_host=source_host, source_kind=source_kind,
                       document_id=document_id, mention_id=mention_id, url=url,
                       observed_at=c.observed_at or self.now(), ttl_s=self.policy.ttl(c.attribute),
                       confidence=max(0.0, min(1.0, c.confidence)), extractor=c.extractor, evidence=c.evidence[:500])
        self.db.add(row)
        return row

    def reassign(self, from_entity: str, to_entity: str) -> int:
        n = 0
        for c in self.db.scalars(select(AcqClaim).where(AcqClaim.entity_id == from_entity)):
            c.entity_id = to_entity
            n += 1
        return n

    # ----------------------------------------------------------- reliability

    def reliability(self, host: str, kind: str) -> float:
        key = f"{host}|{kind}"
        if key not in self._rel_cache:
            prior = SOURCE_KIND_PRIOR.get(kind, 0.6)
            src = self.db.get(AcqSource, host)
            if src is None:
                self._rel_cache[key] = prior
            else:
                learned = src.agree / (src.agree + src.disagree)
                n = src.agree + src.disagree
                # shrink the learned rate toward the kind prior until there is evidence
                w = min(1.0, (n - 2) / 20.0) if n > 2 else 0.0
                self._rel_cache[key] = round((1 - w) * prior + w * learned, 4)
        return self._rel_cache[key]

    # --------------------------------------------------------------- beliefs

    def claims(self, entity_id: str, attribute: str | None = None) -> list[AcqClaim]:
        q = select(AcqClaim).where(AcqClaim.entity_id == entity_id, AcqClaim.status == "active")
        if attribute:
            q = q.where(AcqClaim.attribute == attribute)
        return list(self.db.scalars(q.order_by(AcqClaim.observed_at)))

    def belief(self, attribute: str, claims: list[AcqClaim]) -> dict[str, Any]:
        spec = self.policy.specs.get(attribute)
        now = self.now()
        # 1. per host: keep only the latest observation window
        by_host: dict[str, list[AcqClaim]] = defaultdict(list)
        for c in claims:
            by_host[c.source_host].append(c)
        current: list[AcqClaim] = []
        superseded = 0
        for host, cs in by_host.items():
            latest = max(_aware(c.observed_at) for c in cs)
            keep = [c for c in cs if latest - _aware(c.observed_at) <= SAME_WINDOW]
            superseded += len(cs) - len(keep)
            current.extend(keep)
        # 2. group values within tolerance
        groups = self._group(spec, current)
        hyps = []
        for rep, members in groups:
            per_host: dict[str, float] = {}
            sources = []
            fresh_any = False
            for c in members:
                age = (now - _aware(c.observed_at)).total_seconds()
                fr = freshness(age, c.ttl_s)
                fresh_any = fresh_any or age <= c.ttl_s
                w = self.reliability(c.source_host, c.source_kind) * c.confidence * fr
                per_host[c.source_host] = max(per_host.get(c.source_host, 0.0), w)
                sources.append({"claim_id": c.id, "host": c.source_host, "kind": c.source_kind,
                                "observed_at": _aware(c.observed_at).isoformat(), "confidence": round(c.confidence, 3),
                                "weight": round(w, 4), "fresh": age <= c.ttl_s, "url": c.url,
                                "evidence": c.evidence[:160], "value": c.value})
            support = 1.0 - math.prod(1.0 - w for w in per_host.values())
            hyps.append({"value": rep, "support": support, "hosts": sorted(per_host), "fresh": fresh_any,
                         "sources": sources})
        total = sum(h["support"] for h in hyps) or 1.0
        for h in hyps:
            h["share"] = round(h["support"] / total, 4)
            h["support"] = round(h["support"], 4)
        hyps.sort(key=lambda h: (-h["support"], -len(h["hosts"])))
        top = hyps[0] if hyps else None
        contenders = [h for h in hyps if h["share"] >= CONFLICT_SHARE]
        latest = max((_aware(c.observed_at) for c in claims), default=None)
        return {
            "value": top["value"] if top else None,
            "confidence": round(top["share"] * top["support"], 4) if top else 0.0,
            "fresh": bool(top and top["fresh"]),
            "conflict": len(contenders) >= 2 and any(h["fresh"] for h in contenders),
            "n_sources": len({c.source_host for c in current}),
            "n_claims": len(claims),
            "superseded": superseded,
            "latest_observed": latest.isoformat() if latest else None,
            "ttl_s": self.policy.ttl(attribute),
            "hypotheses": hyps[:6],
        }

    def _group(self, spec: AttrSpec | None, claims: list[AcqClaim]) -> list[tuple[Any, list[AcqClaim]]]:
        if spec is not None and spec.kind == "number":
            nums = []
            for c in claims:
                try:
                    nums.append((float(c.value), c))
                except (TypeError, ValueError):
                    continue
            nums.sort(key=lambda x: x[0])
            groups: list[list[tuple[float, AcqClaim]]] = []
            for v, c in nums:
                if groups:
                    center = sum(x for x, _ in groups[-1]) / len(groups[-1])
                    tol = max(spec.abs_tol, spec.rel_tol * abs(center))
                    if abs(v - center) <= tol:
                        groups[-1].append((v, c))
                        continue
                groups.append([(v, c)])
            out = []
            for g in groups:
                vals = sorted(x for x, _ in g)
                rep = vals[len(vals) // 2]
                out.append((int(rep) if float(rep).is_integer() else rep, [c for _, c in g]))
            return out
        buckets: dict[str, list[AcqClaim]] = defaultdict(list)
        for c in claims:
            buckets[c.value_key].append(c)
        return [(cs[-1].value, cs) for cs in buckets.values()]

    def refresh(self, entity: AcqEntity) -> dict[str, Any]:
        """Recompute the entity's belief summary; upsert/resolve conflicts."""
        by_attr: dict[str, list[AcqClaim]] = defaultdict(list)
        for c in self.claims(entity.id):
            by_attr[c.attribute].append(c)
        beliefs = {a: self.belief(a, cs) for a, cs in by_attr.items()}
        entity.beliefs = beliefs
        entity.source_hosts = sorted({c.source_host for cs in by_attr.values() for c in cs})
        entity.updated_at = self.now()
        for a, b in beliefs.items():
            cid = f"{entity.id}:{a}"[:64]
            existing = self.db.get(AcqConflict, cid)
            if b["conflict"]:
                hyps = [{"value": h["value"], "share": h["share"], "hosts": h["hosts"], "fresh": h["fresh"]}
                        for h in b["hypotheses"]]
                if existing is None:
                    self.db.add(AcqConflict(id=cid, entity_id=entity.id, attribute=a, hypotheses=hyps, status="open"))
                else:
                    existing.hypotheses, existing.status, existing.resolved_at = hyps, "open", None
            elif existing is not None and existing.status == "open":
                existing.status, existing.resolved_at = "resolved", self.now()
        return beliefs

    def stale_attributes(self, entity: AcqEntity, attributes: Iterable[str]) -> list[str]:
        out = []
        for a in attributes:
            b = (entity.beliefs or {}).get(a)
            if b is None:
                continue
            if not b.get("fresh", True):
                out.append(a)
        return out

    def learn_reliability(self, entities: list[AcqEntity], since: datetime) -> dict[str, dict[str, int]]:
        """Hosts that agree with a confident multi-source consensus gain reliability;
        hosts that contradict it lose some. Only claims observed since ``since`` count."""
        tally: dict[str, dict[str, int]] = defaultdict(lambda: {"agree": 0, "disagree": 0})
        for e in entities:
            for a, b in (e.beliefs or {}).items():
                if b.get("n_sources", 0) < 2 or b.get("confidence", 0) < 0.6:
                    continue
                top = b["hypotheses"][0]
                for h in b["hypotheses"]:
                    for s in h["sources"]:
                        if datetime.fromisoformat(s["observed_at"]) < since:
                            continue
                        tally[s["host"]]["agree" if h is top else "disagree"] += 1
        for host, t in tally.items():
            src = self.db.get(AcqSource, host)
            if src is not None:
                src.agree += t["agree"]
                src.disagree += t["disagree"]
        self._rel_cache.clear()
        return dict(tally)
