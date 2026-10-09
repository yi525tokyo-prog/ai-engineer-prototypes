"""Regent's own messages to the person at a time: reminders, nudges, follow-ups.

When what the person asked is really "tell me X at time T" (once, or every day at HH:MM), Regent
does it itself -- no account anywhere, no app to build, none of their time. Delivery is a
``principal_notified`` event, which the page shows (and the browser announces when allowed).
Times are the person's local times; their time zone comes from their browser.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Boolean, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from regent.db import AwareDateTime, Base, _ts
from regent.ids import new_id, utcnow

REMIND_SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["regent_can_do_it", "why"],
    "properties": {
        "regent_can_do_it": {"type": "boolean", "description": "true only if sending the principal a message at "
                             "the right time(s) fully does what they asked"},
        "why": {"type": "string"},
        "message": {"type": "string", "description": "the message, written to the principal, short"},
        "once_at_local": {"type": ["string", "null"], "description": "YYYY-MM-DDTHH:MM in their time zone"},
        "daily_at_local": {"type": ["string", "null"], "description": "HH:MM for a daily message"},
        "assumed": {"type": ["string", "null"], "description": "what you assumed (e.g. a time of day not given)"},
    },
}

REMIND_INSTRUCTIONS = """An operational agent can send the person a message at a time of its choosing (shown in its
page and as a browser notification). Decide whether doing that fully satisfies the request -- e.g. a reminder or a
nudge -- and if so, when (their local time; now is given) and what the message says. If no time of day was given,
choose a sensible one and say so in 'assumed'. If the request needs anything beyond a message at a time (finding
information, keeping records, acting somewhere), answer false."""


class Reminder(Base):
    __tablename__ = "reminders"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mission_id: Mapped[str] = mapped_column(String(40), index=True)
    text: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(AwareDateTime())
    daily_at: Mapped[str | None] = mapped_column(String(5), nullable=True)
    tz: Mapped[str] = mapped_column(String(64), default="UTC")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    delivered_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    created_at: Mapped[datetime] = _ts()


def _zone(tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _next_daily(hhmm: str, tz: str, after: datetime) -> datetime:
    z = _zone(tz)
    h, m = (int(x) for x in hhmm.split(":")[:2])
    local = after.astimezone(z)
    cand = local.replace(hour=h, minute=m, second=0, microsecond=0)
    if cand <= local:
        cand += timedelta(days=1)
    return cand.astimezone(timezone.utc)


def schedule(s: Session, mission_id: str, plan: dict[str, Any], tz: str) -> Reminder:
    now = utcnow()
    if plan.get("daily_at_local"):
        due, daily = _next_daily(plan["daily_at_local"], tz, now), plan["daily_at_local"][:5]
    else:
        local = datetime.fromisoformat(plan["once_at_local"])
        due = (local.replace(tzinfo=_zone(tz)) if local.tzinfo is None else local).astimezone(timezone.utc)
        daily = None
        if due < now - timedelta(minutes=1):
            raise ValueError("that time has already passed")
    r = Reminder(id=new_id("rem"), mission_id=mission_id, text=plan["message"], due_at=due, daily_at=daily, tz=tz)
    s.add(r)
    s.flush()
    return r


def deliver_due(s: Session) -> list[str]:
    from regent.core.observe.events import EventStore

    out = []
    now = utcnow()
    for r in s.scalars(select(Reminder).where(Reminder.active.is_(True), Reminder.due_at <= now)):
        EventStore(s).append("principal_notified", {"channel": "regent_inbox", "text": r.text, "reminder": r.id},
                             source="regent:reminders", mission_id=r.mission_id)
        r.delivered_at = now
        if r.daily_at:
            r.due_at = _next_daily(r.daily_at, r.tz, now)
        else:
            r.active = False
        out.append(r.id)
    s.flush()
    return out


def describe(r: Reminder) -> str:
    local = r.due_at.astimezone(_zone(r.tz))
    when = local.strftime("%a %d %b, %H:%M")
    if r.daily_at:
        return f"Every day at {r.daily_at} (next: {when})" if r.active else "Stopped"
    return f"{when}" + ("" if r.active else " — delivered")


def for_mission(s: Session, mission_id: str) -> list[Reminder]:
    return list(s.scalars(select(Reminder).where(Reminder.mission_id == mission_id).order_by(Reminder.created_at)))
