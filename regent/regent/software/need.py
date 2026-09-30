"""From one sentence to an explicit information need.

The principal says what they want in their own words. Regent turns that into a *need*:
who or what it is about (subjects), the questions that must be answered and exactly how each
quantity is defined (population, exclusions, time windows), the form the answer has to take
(a glance view that stays current, an alert, a tool...), and the definitions that must be
stated honestly because the words are ambiguous ("real people", "actually using").

The reasoning worker proposes the need; Regent checks it against the sentence (every subject
must appear in the sentence, every question must have a defined quantity) and keeps the
proposal, the checks and the fallback reasoning together. Without a reasoning worker a
local parser produces a weaker need and marks it as such.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from regent.software.reasoner import Reasoner, ReasonerUnavailable, get_reasoner

NEED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["handled_as", "subjects", "questions", "deliverable", "definitions_to_state"],
    "properties": {
        "handled_as": {"type": "string", "enum": ["software_capability", "housing", "other"],
                       "description": "software_capability: satisfying this needs some software to observe, "
                                      "compute, present or act on something; housing: finding a place to live; "
                                      "other: neither"},
        "subjects": {"type": "array", "items": {"type": "object", "required": ["name", "kind", "as_written"],
                     "properties": {"name": {"type": "string"},
                                    "kind": {"type": "string", "enum": ["product", "website", "organization",
                                                                        "person", "place", "dataset", "other"]},
                                    "as_written": {"type": "string",
                                                   "description": "the exact words in the sentence"},
                                    "owned_by_principal": {"type": ["boolean", "null"]}}}},
        "questions": {"type": "array", "items": {"type": "object",
                      "required": ["id", "question", "quantity", "unit", "population", "exclude", "windows"],
                      "properties": {
                          "id": {"type": "string", "description": "snake_case"},
                          "question": {"type": "string"},
                          "quantity": {"type": "string", "description": "what is counted/measured, precisely"},
                          "unit": {"type": "string"},
                          "population": {"type": "string", "description": "who/what qualifies"},
                          "exclude": {"type": "array", "items": {"type": "string"}},
                          "windows": {"type": "array", "items": {"type": "string"},
                                      "description": "time windows that make the answer meaningful, e.g. 24h, 7d, 30d, all_time"},
                          "acceptable_forms": {"type": "array", "items": {"type": "string"},
                                               "description": "exact, lower_bound, upper_bound, range, estimate"},
                          "priority": {"type": "string", "enum": ["core", "supporting"]}}}},
        "deliverable": {"type": "object", "required": ["form", "refresh", "why"],
                        "properties": {"form": {"type": "string", "enum": ["glance_view", "report", "alert", "tool",
                                                                           "answer_once"]},
                                       "refresh": {"type": "string", "enum": ["continuous", "daily", "on_demand",
                                                                              "once"]},
                                       "max_seconds_to_read": {"type": ["integer", "null"]},
                                       "why": {"type": "string"}}},
        "definitions_to_state": {"type": "array", "items": {"type": "string"},
                                 "description": "ambiguities the answer must resolve explicitly"},
        "success_looks_like": {"type": "string"},
    },
}

INSTRUCTIONS = """You are the need-analysis step of an operational agent. The principal wrote one sentence. Decide
what information (or ability) they actually need, without choosing any implementation, provider, tool,
metric source or architecture. Identify the subjects exactly as written. For each question, define the quantity
precisely enough that two engineers would count the same thing, including who must be excluded for the answer to
be true to the principal's words, and which time windows make it meaningful. State which forms of answer are
honest when an exact count is impossible. Choose the deliverable form from the wording (e.g. 'at a glance' means
a view readable in seconds that stays current)."""


def analyze(sentence: str, *, reasoner: Reasoner | None = None, mission_id: str | None = None) -> dict[str, Any]:
    r = reasoner or get_reasoner()
    proposal: dict[str, Any] | None = None
    meta: dict[str, Any] = {"by": "local-parser", "cost_usd": 0.0}
    if r.available():
        try:
            ans = r.ask("need_analysis", INSTRUCTIONS, {"sentence": sentence}, NEED_SCHEMA, budget_usd=0.8,
                        mission_id=mission_id)
            proposal = ans.output
            meta = {"by": ans.provider, "model": ans.model, "cost_usd": ans.cost_usd, "cached": ans.cached,
                    "answer_key": ans.key}
        except ReasonerUnavailable as e:
            meta["reasoner_error"] = str(e)[:300]
    if proposal is None:
        proposal = local_parse(sentence)
    checks = check(sentence, proposal)
    need = {**proposal, "sentence": sentence, "analysis": meta, "checks": checks}
    need["subjects"] = [s for s in proposal.get("subjects", []) if s.get("_ok", True)]
    return need


def check(sentence: str, need: dict[str, Any]) -> list[dict[str, Any]]:
    """Regent's own checks on a proposed need. Failed items are dropped, and the check says so."""
    out = []
    norm = _norm(sentence)
    for s in need.get("subjects", []):
        ok = bool(s.get("as_written")) and _norm(s["as_written"]) in norm
        s["_ok"] = ok
        out.append({"check": f"subject '{s.get('name')}' appears in the sentence", "passed": ok})
    qs = need.get("questions", [])
    out.append({"check": "at least one question", "passed": bool(qs)})
    for q in qs:
        ok = bool(q.get("quantity")) and bool(q.get("population"))
        out.append({"check": f"question {q.get('id')} defines quantity and population", "passed": ok})
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s).lower()).strip()


def local_parse(sentence: str) -> dict[str, Any]:
    """Weak fallback: proper names become subjects; 'how many' becomes a count question."""
    s = unicodedata.normalize("NFKC", sentence)
    words = re.findall(r"[A-Za-z][A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)*", s)
    first = words[0] if words else ""
    subjects = [{"name": w, "kind": "product" if re.search(r"[a-z][A-Z]", w) else "other", "as_written": w,
                 "owned_by_principal": None}
                for w in dict.fromkeys(words) if w != first and (w[0].isupper() or re.search(r"[a-z][A-Z]", w))
                and w not in ("I",)]
    low = s.lower()
    questions = []
    if re.search(r"how many|how much|number of|何人|いくつ|何件", low):
        questions.append({"id": "count", "question": sentence, "quantity": "count implied by the sentence",
                          "unit": "count", "population": "as written (not further defined: no reasoning worker)",
                          "exclude": [], "windows": ["all_time"], "acceptable_forms": ["lower_bound", "exact"],
                          "priority": "core"})
    glance = bool(re.search(r"at a glance|dashboard|一目|ひと目", low))
    return {"handled_as": "software_capability" if subjects and questions else "other", "subjects": subjects,
            "questions": questions,
            "deliverable": {"form": "glance_view" if glance else "answer_once",
                            "refresh": "continuous" if glance else "once", "max_seconds_to_read": 10 if glance else None,
                            "why": "wording" if glance else "no recurring wording"},
            "definitions_to_state": [], "success_looks_like": ""}


def signature(need: dict[str, Any]) -> list[str]:
    """Stable descriptors used to find an existing capability that answers the same need."""
    subs = sorted(_slug(s["name"]) for s in need.get("subjects", []))
    return sorted({f"{q.get('id')}@{','.join(subs)}" for q in need.get("questions", [])})


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", s).lower())
