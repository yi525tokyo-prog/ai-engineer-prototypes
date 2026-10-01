"""Finding a capability Regent already has for a newly stated need.

Wording changes; needs recur. A dashboard is a candidate when it is usable and is about the
same subject; an application is always a candidate (it is about whatever its data is about), and
the judge says whether the new request is the same need, something it can already do, or
something it should be extended to do. Whether it actually answers the new need is a judgement (the reasoning worker
compares the new questions with what the capability answers) that Regent then tests the
cheap way: by reading the capability and getting live values. Without a reasoning worker,
question overlap decides.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.software import capability as K
from regent.software.need import question_id
from regent.software.reasoner import Reasoner, ReasonerUnavailable, get_reasoner

MATCH_SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["matches"],
    "properties": {"matches": {"type": "array", "items": {"type": "object", "required": [
        "capability", "relation", "covered", "gaps"],
        "properties": {"capability": {"type": "string"},
                       "relation": {"type": "string", "enum": ["same_need", "can_do", "extend", "unrelated"],
                                    "description": "same_need: built to answer what the new need asks (however well "
                                    "it answers today); can_do: the new request is something it can already do "
                                    "through its interface, as it is; extend: it holds the data or is the place the "
                                    "principal uses for this, but lacks something the request needs; unrelated"},
                       "covered": {"type": "array", "items": {"type": "string"},
                                   "description": "ids of the new questions or requirements it covers"},
                       "gaps": {"type": "array", "items": {"type": "string"},
                                "description": "what the new need asks that it cannot do today"}}}}},
}

MATCH_INSTRUCTIONS = """A principal stated a need. Regent already runs the capabilities listed: dashboards that answer
questions, and applications with an API (their endpoints are listed). For each, judge its relation to the new need:
- same_need: it was built for the same need -- same subject and same quantity or verdict, whatever the wording;
- can_do: the new request can be carried out through its existing endpoints, exactly as they are;
- extend: it is the place this belongs (it holds the data the request is about) but it cannot do it yet;
- unrelated.
Regent improves or uses what it has rather than building a duplicate, so judge what the request is about, not
today's coverage. List the new question/requirement ids it covers and, concretely, what it lacks."""


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", s).lower())


def candidates(db: Session, need: dict[str, Any]) -> list[K.SwCapability]:
    """Every usable capability is a candidate when a judge is available (an application is about
    whatever its data is about, not about a named product); without one, subject overlap."""
    caps = list(db.scalars(select(K.SwCapability).where(K.SwCapability.status.in_(("usable", "degraded")))
                           .order_by(K.SwCapability.created_at, K.SwCapability.id)))
    subj = {_key(s["name"]) for s in need.get("subjects", [])}
    return [c for c in caps if c.implementation == "application"
            or subj & {_key(s["name"]) for s in (c.need or {}).get("subjects", [])}]


def _describe(c: K.SwCapability) -> dict[str, Any]:
    d = {"capability": c.slug, "kind": "application" if c.implementation == "application" else "dashboard",
         "built_for": (c.need or {}).get("sentence"),
         "questions": [q.get("question") for q in (c.need or {}).get("questions", [])]}
    if c.implementation == "application":
        d["requirements"] = [r.get("capability") for r in (c.spec or {}).get("requirements", [])]
        d["endpoints"] = [{k: a.get(k) for k in ("id", "method", "path", "purpose")}
                          for a in (c.spec or {}).get("design", {}).get("api", [])]
        d["entities"] = (c.spec or {}).get("design", {}).get("entities")
    else:
        d["metrics"] = [m.get("label") for m in (c.spec or {}).get("metrics", [])][:20]
    return d


def match(db: Session, need: dict[str, Any], *, reasoner: Reasoner | None = None,
          mission_id: str | None = None) -> list[dict[str, Any]]:
    caps = candidates(db, need)
    if not caps:
        return []
    r = reasoner or get_reasoner()
    core = [q for q in need.get("questions", []) if q.get("priority", "core") == "core"] or need.get("questions", [])
    verdicts: dict[str, dict[str, Any]] = {}
    if r.available():
        payload = {"new_need": {"sentence": need.get("sentence"), "questions": need.get("questions"),
                                "requirements": need.get("requirements")},
                   "capabilities": [_describe(c) for c in caps]}
        try:
            ans = r.ask("reuse_match", MATCH_INSTRUCTIONS, payload, MATCH_SCHEMA, budget_usd=0.5, mission_id=mission_id)
            verdicts = {m["capability"]: {**m, "by": ans.provider} for m in ans.output.get("matches", [])}
        except ReasonerUnavailable:
            verdicts = {}
    out = []
    for c in caps:
        v = verdicts.get(c.slug)
        if v is None:      # no judge available: the same questions, by wording
            if c.implementation == "application":
                continue
            mine = (c.need or {})
            covered = [q["id"] for q in need.get("questions", []) if question_id(mine, q.get("question"))]
            v = {"capability": c.slug, "relation": "same_need" if core and core[0]["id"] in covered else "unrelated",
                 "covered": covered, "gaps": [], "by": "question overlap"}
        rel = v.get("relation") or ("same_need" if v.get("same_need") else "unrelated")
        if rel == "unrelated":
            continue
        # Regent's own test: the capability works right now
        if c.implementation == "application":
            from regent.software import appcap

            try:
                appcap.live(c)
                live_values, coverage = 1, c.coverage
            except Exception:      # noqa: BLE001 -- a dead application is not reusable as it is
                continue
        else:
            r_ = K.read(db, c, count_use=False)
            live_values = sum(1 for m in r_["metrics"] if m.get("value") is not None)
            coverage = r_["coverage"]
            if not live_values:
                continue
        out.append({"id": c.id, "slug": c.slug, "title": c.title, "status": c.status, "coverage": coverage,
                    "implementation": c.implementation, "relation": rel,
                    "covered_questions": v.get("covered", v.get("covered_questions", [])), "gaps": v.get("gaps", []),
                    "judged_by": v["by"], "live_values": live_values, "signature": c.signature})
    return out
