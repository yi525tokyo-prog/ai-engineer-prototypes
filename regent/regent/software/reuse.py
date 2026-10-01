"""Finding a capability Regent already has for a newly stated need.

Wording changes; needs recur. A capability is a candidate when it is usable and is about the
same subject. Whether it actually answers the new need is a judgement (the reasoning worker
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
        "capability", "same_need", "covered_questions", "gaps"],
        "properties": {"capability": {"type": "string"},
                       "same_need": {"type": "boolean", "description": "it was built for what the new need asks "
                                     "(same subject, same quantity or verdict), however well it answers today"},
                       "covered_questions": {"type": "array", "items": {"type": "string"}},
                       "gaps": {"type": "array", "items": {"type": "string"}}}}}},
}

MATCH_INSTRUCTIONS = """A principal stated a need. Regent already runs the capabilities listed (what each was built to
answer, and what it shows). For each, say whether it was built for the same need as the new one -- the same subject
and the same quantity or verdict, whatever the wording, window or how complete its answer is today -- which of the
new questions it covers (by id), and what it lacks. Regent improves a capability rather than building a duplicate,
so judge the need, not today's coverage."""


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", s).lower())


def candidates(db: Session, need: dict[str, Any]) -> list[K.SwCapability]:
    subj = {_key(s["name"]) for s in need.get("subjects", [])}
    out = []
    for c in db.scalars(select(K.SwCapability).where(K.SwCapability.status.in_(("usable", "degraded")))):
        theirs = {_key(s["name"]) for s in (c.need or {}).get("subjects", [])}
        if subj & theirs:
            out.append(c)
    return out


def match(db: Session, need: dict[str, Any], *, reasoner: Reasoner | None = None,
          mission_id: str | None = None) -> list[dict[str, Any]]:
    caps = candidates(db, need)
    if not caps:
        return []
    r = reasoner or get_reasoner()
    core = [q for q in need.get("questions", []) if q.get("priority", "core") == "core"] or need.get("questions", [])
    verdicts: dict[str, dict[str, Any]] = {}
    if r.available():
        payload = {"new_need": {"sentence": need.get("sentence"), "questions": need.get("questions")},
                   "capabilities": [{"capability": c.slug, "answers": (c.need or {}).get("sentence"),
                                     "questions": [q.get("question") for q in (c.need or {}).get("questions", [])],
                                     "metrics": [m.get("label") for m in (c.spec or {}).get("metrics", [])][:20]}
                                    for c in caps]}
        try:
            ans = r.ask("reuse_match", MATCH_INSTRUCTIONS, payload, MATCH_SCHEMA, budget_usd=0.5, mission_id=mission_id)
            verdicts = {m["capability"]: {**m, "by": ans.provider} for m in ans.output.get("matches", [])}
        except ReasonerUnavailable:
            verdicts = {}
    out = []
    for c in caps:
        v = verdicts.get(c.slug)
        if v is None:      # no judge available: the same questions, by wording
            mine = (c.need or {})
            covered = [q["id"] for q in need.get("questions", []) if question_id(mine, q.get("question"))]
            v = {"capability": c.slug, "same_need": bool(core) and core[0]["id"] in covered,
                 "covered_questions": covered, "gaps": [], "by": "question overlap"}
        if not v.get("same_need"):
            continue
        # Regent's own test: the capability answers right now
        r_ = K.read(db, c, count_use=False)
        live = [m for m in r_["metrics"] if m.get("value") is not None]
        out.append({"id": c.id, "slug": c.slug, "title": c.title, "status": c.status, "coverage": r_["coverage"],
                    "covered_questions": v["covered_questions"], "gaps": v.get("gaps", []), "judged_by": v["by"],
                    "live_values": len(live), "signature": c.signature})
    return [m for m in out if m["live_values"]]
