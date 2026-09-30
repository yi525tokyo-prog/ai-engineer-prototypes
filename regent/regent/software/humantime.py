"""How much of the principal's active time a mission consumed (and is still waiting for).

Counted: writing the mission sentence (typing time at 40 words/minute), and every human
interrupt -- its measured active seconds when the interface reported them, else Regent's own
estimate for that action. Waiting time is not active time: an interrupt that sits open for a
day while the principal does other things costs only the seconds the action itself takes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.db import HumanInterrupt, Mission

WORDS_PER_MINUTE = 40.0


def ledger(db: Session, mission_id: str) -> dict[str, Any]:
    m = db.get(Mission, mission_id)
    ids = [mission_id] + [c.id for c in db.scalars(select(Mission).where(Mission.parent_id == mission_id))]
    sentence = (m.objective or m.title) if m else ""
    words = max(1, len(sentence.split())) if sentence.isascii() else max(1, len(sentence) // 3)
    typing = round(words / WORDS_PER_MINUTE * 60, 1)
    items = [{"what": "wrote the mission sentence", "kind": "statement", "seconds": typing, "measured": False,
              "status": "done"}]
    spent, pending = typing, 0.0
    for hi in db.scalars(select(HumanInterrupt).where(HumanInterrupt.mission_id.in_(ids))
                         .order_by(HumanInterrupt.created_at)):
        measured = (hi.response or {}).get("active_seconds")
        secs = float(measured) if isinstance(measured, (int, float)) else float(hi.estimated_time_seconds or 0)
        row = {"what": hi.required_action[:200], "kind": hi.kind, "seconds": secs,
               "measured": isinstance(measured, (int, float)), "status": hi.status,
               "resolution": hi.resolution, "interrupt_id": hi.id}
        if hi.status == "resolved" and hi.resolution != "condition_observed":
            spent += secs
        elif hi.status == "resolved":
            spent += secs          # the principal did it elsewhere; Regent saw the result
        elif hi.status == "open":
            pending += secs
        items.append(row)
    return {"mission_id": mission_id, "active_seconds_spent": round(spent, 1),
            "active_seconds_requested_open": round(pending, 1), "items": items,
            "method": f"statement typing at {WORDS_PER_MINUTE:.0f} wpm + interrupts (measured when reported, "
                      "else Regent's estimate)"}
