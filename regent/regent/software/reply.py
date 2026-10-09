"""Answering a question by thinking it through: explanations, advice, ideas, plans, how-to.

Not every request is a figure to look up or something to keep track of. "How could work be made
unnecessary?" wants a considered answer, written for the person, in their language, said once.
Regent answers these directly and says where the answer rests on things that are contested or may
have changed; it does not dress the answer up as a tracked metric.
"""

from __future__ import annotations

from typing import Any

from regent.software.reasoner import Reasoner, get_reasoner

REPLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["reply", "language"],
    "properties": {
        "reply": {"type": "string",
                  "description": "the answer itself, in the language the person wrote in; short paragraphs or "
                                 "'- ' bullets, readable on a phone"},
        "language": {"type": "string", "description": "the language of the person's sentence, e.g. ja, en"},
        "unsure": {"type": "array", "items": {"type": "string"},
                   "description": "in the person's language: points that are contested, or facts that may have "
                                  "changed since you learned them"},
    },
}

INSTRUCTIONS = """The person asked you something to think through: an explanation, advice, ideas, a plan, a
comparison or a how-to. Answer it the way a thoughtful, knowledgeable friend would: directly, concretely, with the
reasoning that matters, in the SAME LANGUAGE the person wrote in. Lead with the answer, then the few points that
support it. Where views genuinely differ, say so briefly and fairly. Do not invent figures, quotes or sources; if
something depends on current facts you may not have, say so in 'unsure'. Write plain text for a phone screen: short
paragraphs and '- ' bullets, numbered headings like '1. ...' if useful, but no markdown symbols (no **, #, tables).
Keep it under about 350 words."""


def reply(sentence: str, *, mission_id: str | None = None, reasoner: Reasoner | None = None) -> dict[str, Any]:
    """The answer, as {text, language, unsure, cost_usd}. Raises ReasonerUnavailable if it cannot think now."""
    r = reasoner or get_reasoner()
    ans = r.ask("reply", INSTRUCTIONS, {"sentence": sentence}, REPLY_SCHEMA, budget_usd=0.5, mission_id=mission_id)
    out = ans.output
    return {"text": str(out.get("reply") or "").strip(), "language": out.get("language"),
            "unsure": [str(x) for x in (out.get("unsure") or [])][:5], "cost_usd": ans.cost_usd}


TELL_INSTRUCTIONS = """The person asked a question about the world as it is now. Regent has looked it up; the
findings are below, each with what exactly it measures. Tell them the answer in the SAME LANGUAGE they asked in,
in one to four short sentences: the figure or verdict first, then what it covers and how current it is. Use only
what the findings say. If the findings do not answer exactly what they asked (another area, another period, only a
bound), say so plainly; if nothing answers it, say that Regent could not find it. Plain text, no markdown."""


def tell(sentence: str, findings: dict[str, Any], *, mission_id: str | None = None,
         reasoner: Reasoner | None = None) -> dict[str, Any]:
    """An investigation's result, said once in the person's words. Raises ReasonerUnavailable."""
    r = reasoner or get_reasoner()
    ans = r.ask("tell_findings", TELL_INSTRUCTIONS, {"question": sentence, "findings": findings}, REPLY_SCHEMA,
                budget_usd=0.3, mission_id=mission_id)
    out = ans.output
    return {"text": str(out.get("reply") or "").strip(), "language": out.get("language"),
            "unsure": [str(x) for x in (out.get("unsure") or [])][:5]}


WATCH_INSTRUCTIONS = """Regent is keeping watch for the person (what they asked is below) and has just looked.
Say in one or two short sentences, in the SAME LANGUAGE they asked in: where things stand now (the figure with its
unit, the period it covers and who published it, as the findings give them) and what will make Regent tell them.
Use only what the findings say; if something they asked about is not known, say so in a few words. Plain text, no
markdown."""


def watch_line(sentence: str, findings: dict[str, Any], *, mission_id: str | None = None,
               reasoner: Reasoner | None = None) -> dict[str, Any]:
    """Where a watch stands, said in the person's words. Raises ReasonerUnavailable."""
    r = reasoner or get_reasoner()
    ans = r.ask("watch_line", WATCH_INSTRUCTIONS, {"asked": sentence, "findings": findings}, REPLY_SCHEMA,
                budget_usd=0.2, mission_id=mission_id)
    return {"text": str(ans.output.get("reply") or "").strip(), "language": ans.output.get("language")}


def findings_of(read: dict[str, Any]) -> dict[str, Any]:
    keep = ("label", "display", "form", "window", "definition", "why", "status", "unit")
    return {"metrics": [{k: m.get(k) for k in keep if m.get(k) is not None} for m in read.get("metrics", [])][:12],
            "not_answered": [u.get("question") for u in read.get("unanswered", [])][:5]}
