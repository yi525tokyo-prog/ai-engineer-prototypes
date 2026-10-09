"""Does a measurement support the concept it is used for?

A field matching a path is not evidence about people. "2 payments" is not "2 users"; "180
distinct IPs" is not "at most 180 humans" (a household or an office behind one address is many
people); an operator's own test payment is not a real user. Regent therefore owns the inference
rules, and the reasoning worker only supplies *premises* -- facts about how the counted things
relate to the population the principal asked about -- each with a verbatim quote Regent can find
in what it observed.

Counting distinct members of a population P (the question's population, with its exclusions)
from a count N of some unit U:

  lower bound  N <= |P| needs   membership    every counted U was produced by a member of P
                                              (real, external -- not bots, the operator, tests)
                                distinctness  no member of P is counted twice
                                window        every counted U falls in the question's window
  "at least one"                membership for at least one counted U, and N >= 1
  upper bound  |P| <= N needs   coverage      every member of P (in the window) produced a U
                                no_merging    no single U stands for two members of P
                                window
  measure      all of the above

Anything else is a **proxy**: it may move with the concept, but no number about P follows
from it, and Regent says so instead of converting it into a bound.
"""

from __future__ import annotations

import re
from typing import Any

PREMISES = ("membership", "distinctness", "window", "coverage", "no_merging")
REQUIRED = {
    "measure": ("membership", "distinctness", "window", "coverage", "no_merging"),
    "lower_bound": ("membership", "distinctness", "window"),
    "upper_bound": ("coverage", "no_merging", "window"),
}

PREMISE_SCHEMA = {"type": "object", "required": ["holds", "why", "evidence"],
                  "properties": {"holds": {"type": "string", "enum": ["yes", "no", "unknown"]},
                                 "why": {"type": "string"},
                                 "evidence": {"type": "string", "description": "verbatim quote from the input that "
                                              "shows it, or '' if nothing observed shows it"}}}

PREMISE_DOC = {
    "membership": "every counted unit was produced by a member of the question's population: a real, external "
                  "person as the question defines it (not a bot, crawler, the operator, staff or a test)",
    "distinctness": "no member of the population is counted more than once (an official count of a population is "
                    "distinct by its definition)",
    "window": "every counted unit falls inside the question's time window",
    "coverage": "every member of the population (in the window) produced at least one counted unit",
    "no_merging": "no single counted unit stands for two or more members of the population",
}


def _found(quote: str, haystack: str) -> bool:
    q = re.sub(r"\s+", " ", (quote or "").replace('\\"', '"')).strip().lower()
    return len(q) >= 6 and q in haystack


def audit(field: dict[str, Any], haystack: str) -> dict[str, Any]:
    """Regent's verdict on what a field can honestly be used as. ``haystack`` is everything that
    was observed (normalized lower-case text) -- premises count only with evidence found in it."""
    claimed = field.get("relation_to_need") or "proxy"
    premises = field.get("premises") or {}
    held: dict[str, bool] = {}
    notes: list[str] = []
    for p in PREMISES:
        x = premises.get(p) or {}
        ok = x.get("holds") == "yes" and _found(x.get("evidence", ""), haystack)
        held[p] = ok
        if x.get("holds") == "yes" and not ok:
            notes.append(f"{p}: claimed, but the quoted evidence was not found in what was observed")
    if claimed in ("unrelated", "context"):
        return {"claimed": claimed, "relation": claimed, "held": held, "missing": [], "notes": notes}
    granted = "proxy"
    if all(held[p] for p in REQUIRED["measure"]):
        granted = "measure"
    elif claimed in ("lower_bound", "measure") and all(held[p] for p in REQUIRED["lower_bound"]):
        granted = "lower_bound"
    elif claimed in ("upper_bound", "measure") and all(held[p] for p in REQUIRED["upper_bound"]):
        granted = "upper_bound"
    elif claimed in ("lower_bound", "measure") and held["membership"]:
        granted = "at_least_one"         # someone in the population acted, how many is unknown
    need = REQUIRED.get(claimed, REQUIRED["lower_bound"])
    missing = [p for p in need if not held[p]]
    if granted != claimed:
        notes.append(f"claimed {claimed.replace('_', ' ')}, granted {granted.replace('_', ' ')}: not established "
                     + ", ".join(f"{p} ({PREMISE_DOC[p]})" for p in missing))
    return {"claimed": claimed, "relation": granted, "held": held, "missing": missing, "notes": notes}


FORM_OF = {"measure": "estimate", "lower_bound": "lower_bound", "upper_bound": "upper_bound",
           "at_least_one": "lower_bound", "proxy": "proxy", "activity_signal": "proxy", "direct": "estimate",
           "context": "context"}
