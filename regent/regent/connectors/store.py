"""Persistence helper for local connector backends.

Local backends (mailbox, calendar, bookings, search index, sim portal) keep
their state in ``connector_records`` using short, independent transactions so
tools can run concurrently outside the loop's session.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from regent import db as dbm
from regent.db import ConnectorRecord
from regent.ids import new_id


def put(connector: str, kind: str, data: dict[str, Any], rid: str | None = None) -> dict[str, Any]:
    s = dbm.session()
    try:
        rec = s.get(ConnectorRecord, rid) if rid else None
        if rec is None:
            rec = ConnectorRecord(id=rid or new_id(kind[:4]), connector=connector, kind=kind, data=data)
            s.add(rec)
        else:
            rec.data = {**(rec.data or {}), **data}
        s.commit()
        return {"id": rec.id, **(rec.data or {})}
    finally:
        s.close()


def get(rid: str) -> dict[str, Any] | None:
    s = dbm.session()
    try:
        rec = s.get(ConnectorRecord, rid)
        return None if rec is None else {"id": rec.id, "kind": rec.kind, **(rec.data or {})}
    finally:
        s.close()


def find(connector: str, kind: str | None = None, **match: Any) -> list[dict[str, Any]]:
    s = dbm.session()
    try:
        q = select(ConnectorRecord).where(ConnectorRecord.connector == connector)
        if kind:
            q = q.where(ConnectorRecord.kind == kind)
        out = []
        for rec in s.scalars(q.order_by(ConnectorRecord.created_at)):
            d = rec.data or {}
            if all(d.get(k) == v for k, v in match.items()):
                out.append({"id": rec.id, "kind": rec.kind, **d})
        return out
    finally:
        s.close()
