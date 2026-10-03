"""The front door: decide what each sentence should make happen in the world.

Regent does not sort requests by what kind of information they are about ("a number, so track it").
It asks what the sentence should cause:

  answer       think it through and reply now; nothing to fetch, keep, watch or do
  investigate  find out something about the world as it is now, reply once, keep nothing running
  remember     keep something the person told (an appointment, a fact), or remind them at a time
  watch        keep an eye on something and speak up only when it matters (a condition, a schedule)
  act          change something in the world on their behalf (send, reply, book, build, buy)

One call decides, and for an answer it also writes the answer, so a question to think through
comes back in the time of a single reply. Remembering is done right here. Investigating, watching
and acting continue into Regent's longer path (sources, capabilities, routes and authority).
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from regent.software.reasoner import Reasoner, ReasonerUnavailable, get_reasoner

MODES = ("answer", "investigate", "remember", "watch", "act")

ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["mode", "language", "why"],
    "properties": {
        "mode": {"type": "string", "enum": list(MODES)},
        "language": {"type": "string", "description": "the language of the sentence, e.g. ja, en"},
        "why": {"type": "string", "description": "one short clause: what this sentence should make happen"},
        "reply": {"type": ["string", "null"],
                  "description": "only for answer: the answer itself, in the person's language, plain text for "
                                 "a phone (short paragraphs, '- ' bullets, no markdown symbols), under ~300 words"},
        "unsure": {"type": "array", "items": {"type": "string"},
                   "description": "only for answer: contested points or facts that may have changed, in the "
                                  "person's language"},
        "remember": {"type": ["object", "null"],
                     "description": "only for remember",
                     "properties": {
                         "note": {"type": "string",
                                  "description": "what to keep, short, in the person's language"},
                         "date_local": {"type": ["string", "null"], "description": "YYYY-MM-DD if it has a date"},
                         "time_local": {"type": ["string", "null"], "description": "HH:MM if it has a time"},
                         "remind_at_local": {"type": ["string", "null"],
                                             "description": "YYYY-MM-DDTHH:MM to tell the person: when they "
                                                            "asked to be reminded, the time they asked for; for a "
                                                            "dated appointment they only told you about, 08:00 "
                                                            "that day; otherwise null"},
                         "daily_at_local": {"type": ["string", "null"],
                                            "description": "HH:MM only if they asked to be reminded every day"},
                         "message": {"type": ["string", "null"],
                                     "description": "what the reminder will say, in the person's language"}}},
    },
}

INSTRUCTIONS = """You are the front door of a personal agent. Decide what this sentence should make happen in the
world, not what topic it is about:
- answer: it asks you to explain, advise, brainstorm, plan, compare or give a how-to, and thinking it through is
  enough. Nothing needs fetching, keeping, watching or doing. Then write the reply yourself, now: lead with the
  answer, give the reasoning that matters, be fair where views differ, never invent figures or sources.
- investigate: it needs a fact about the world as it is now (a current figure, a status, today's information) to
  be found and told once. Not tracked afterwards.
- remember: the person tells you something to keep (an appointment, a fact about them, a preference) or asks to be
  reminded of something at a time. Resolve dates and times against 'now_local'.
- watch: keep an eye on something over time and tell them when a condition is met, when it changes, or on a
  schedule ('every morning tell me...', 'tell me if X goes above Y').
- act: do something in the world for them: send or reply to someone, book, buy, create, change, build a tool.
Reply in the person's language. A number in the sentence does not make it a watch; a question about now is an
investigation unless they ask to be told again later."""

_busy: set[str] = set()
_lock = threading.Lock()
# answers being written right now, by request: shown on the page from memory, saved once finished
WRITING: dict[str, str] = {}


LATER = """
When the mode is answer, leave 'reply' null: the answer is written separately, right after you decide."""

SPEAK = """The person asked you something to think through. Answer it the way a thoughtful, knowledgeable
friend would: directly and concretely, in the SAME LANGUAGE they wrote in. Lead with the answer, then the few
points that support it; where views genuinely differ, say so briefly and fairly; never invent figures, quotes or
sources, and say so where something depends on facts that may have changed. Plain text for a phone screen: short
paragraphs and '- ' bullets, no markdown symbols. Under about 300 words."""


def route(sentence: str, *, now_local: str, timezone: str | None, mission_id: str | None = None,
          reasoner: Reasoner | None = None, answer_later: bool = False) -> dict[str, Any]:
    r = reasoner or get_reasoner()
    ans = r.ask("route", INSTRUCTIONS + (LATER if answer_later else ""),
                {"sentence": sentence, "now_local": now_local, "timezone": timezone},
                ROUTE_SCHEMA, budget_usd=0.5, mission_id=mission_id, effort="low" if answer_later else None)
    out = dict(ans.output)
    if out.get("mode") not in MODES:
        out["mode"] = "act"
    return out


def route_mission(s, m, *, stream: bool = False) -> str:
    """Route one waiting request and do what can be done at once. Returns 'handled' (answered or
    remembered: finished), 'continue' (investigate/watch/act: the longer path takes it from here),
    'paused' (could not think right now) or 'busy' (already being routed)."""
    decision = decide(m.id, m.objective or m.title, dict(m.attrs or {}), stream=stream)
    return apply(s, m, decision)


def decide(mission_id: str, sentence: str, attrs: dict[str, Any], *, stream: bool = False) -> dict[str, Any]:
    """The slow part (thinking, writing an answer), touching no database: a long loop pass elsewhere
    can never hold it up."""
    from regent.ids import utcnow

    with _lock:
        if mission_id in _busy:
            return {"outcome": "busy"}
        _busy.add(mission_id)
    try:
        if attrs.get("route") != "pending":
            return {"outcome": "continue"}
        tz = attrs.get("timezone")
        try:
            zone = ZoneInfo(tz) if tz else None
        except Exception:
            zone = None
        now_local = datetime.now(zone).strftime("%Y-%m-%dT%H:%M (%A)") if zone else utcnow().strftime(
            "%Y-%m-%dT%H:%M UTC (%A)")
        r = get_reasoner()
        live = stream and r.backend == "api"        # decide fast, then write the answer where it can be read
        try:
            out = route(sentence, now_local=now_local, timezone=tz, mission_id=mission_id, reasoner=r,
                        answer_later=live)
            if live and out["mode"] == "answer":
                out["reply"] = _speak(mission_id, sentence, r)
        except ReasonerUnavailable as e:
            return {"outcome": "paused", "why": "Regent's reasoning service did not answer: " + str(e)[:200]}
        return {"outcome": "routed", "out": out, "tz": tz}
    finally:
        with _lock:
            _busy.discard(mission_id)


def apply(s, m, decision: dict[str, Any]) -> str:
    """The quick part: record what was decided (and keep or schedule what was to be remembered)."""
    from regent import reminders as RM
    from regent.ids import utcnow

    if decision["outcome"] in ("busy", "continue"):
        return decision["outcome"]
    attrs = dict(m.attrs or {})
    if attrs.get("route") != "pending":
        return "continue"                               # someone else already routed it
    if decision["outcome"] == "paused":
        m.attrs = {**attrs, "paused": {"why": decision["why"], "since": utcnow().isoformat()}}
        return "paused"
    out, tz = decision["out"], decision.get("tz")
    attrs.pop("paused", None)
    attrs.update({"route": "done", "mode": out["mode"], "language": out.get("language"),
                  "routed": {"why": out.get("why")}})
    if out["mode"] == "answer" and (out.get("reply") or "").strip():
        attrs["reply"] = {"text": out["reply"].strip(), "unsure": [str(x) for x in out.get("unsure") or []][:5],
                          "language": out.get("language")}
        m.attrs, m.status = attrs, "completed"
        return "handled"
    if out["mode"] == "remember" and out.get("remember"):
        rem = out["remember"]
        kept = {k: rem.get(k) for k in ("note", "date_local", "time_local")}
        plan = {"message": rem.get("message") or rem.get("note")}
        if rem.get("daily_at_local"):
            plan["daily_at_local"] = rem["daily_at_local"]
        elif rem.get("remind_at_local"):
            plan["once_at_local"] = rem["remind_at_local"]
        if len(plan) > 1:
            try:
                RM.schedule(s, m.id, plan, tz or "UTC")
                kept["remind"] = plan
            except ValueError as e:                     # a time already past: say so, keep the note anyway
                kept["remind_problem"] = str(e)[:200]
        attrs["remembered"] = kept
        m.attrs, m.status = attrs, "completed"
        return "handled"
    m.attrs = attrs
    return "continue"


def _speak(mission_id: str, sentence: str, r: Reasoner) -> str:
    """Write the answer where the page can show it while it is being written (memory, not the
    database: a long-running loop pass must never hold up the first words)."""
    WRITING[mission_id] = ""

    def show(text: str) -> None:
        WRITING[mission_id] = text

    return r.stream_text(SPEAK, sentence, show)


def shape_need(need: dict[str, Any], mode: str | None) -> dict[str, Any]:
    """What the mode says about the deliverable, whatever the need analysis guessed from the topic."""
    if not mode:
        return need
    d = dict(need.get("deliverable") or {})
    if mode == "investigate":
        d.update({"form": "answer_once", "refresh": "once"})
    elif mode == "watch" and d.get("form") not in ("alert", "report"):
        d["form"] = "alert"
    if mode in ("investigate", "watch") and need.get("handled_as") in ("conversation", "other"):
        need = {**need, "handled_as": "software_capability"}
    return {**need, "deliverable": d, "mode": mode}
