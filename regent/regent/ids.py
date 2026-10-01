from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


_last_stamp: datetime | None = None
_stamp_lock = __import__("threading").Lock()


def monotonic_now() -> datetime:
    """utcnow(), but strictly increasing within the process: rows created in the same microsecond
    still get distinct creation times, so "creation order" is a total order (ids are random)."""
    global _last_stamp
    with _stamp_lock:
        t = utcnow()
        if _last_stamp is not None and t <= _last_stamp:
            t = _last_stamp + timedelta(microseconds=1)
        _last_stamp = t
        return t
