"""Software capabilities over HTTP: the glance views people read, the JSON other software reads."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.software import capability as K
from regent.software.tables import SwCapability

router = APIRouter()


def get_db():
    from regent.api.app import get_db as _get

    yield from _get()


def _cap(db: Session, ref: str) -> SwCapability:
    c = K.get(db, ref)
    if c is None:
        raise HTTPException(404, f"no capability {ref}")
    return c


@router.get("/software/{ref}", response_class=HTMLResponse)
def glance_view(ref: str, db: Session = Depends(get_db)):
    c = _cap(db, ref)
    if K.due(c) and c.status in ("usable", "degraded"):
        K.collect(db, c)
    page = K.render_html(K.read(db, c))
    db.commit()
    return HTMLResponse(page)


@router.get("/api/software/capabilities")
def list_capabilities(db: Session = Depends(get_db)):
    return [{"id": c.id, "slug": c.slug, "title": c.title, "status": c.status, "version": c.version,
             "implementation": c.implementation, "coverage": c.coverage, "signature": c.signature,
             "uses": c.uses, "tool": c.tool_name, "mission_id": c.mission_id,
             "last_collect_at": c.last_collect_at.isoformat() if c.last_collect_at else None}
            for c in db.scalars(select(SwCapability).order_by(SwCapability.created_at))]


@router.get("/api/software/capabilities/{ref}")
def read_capability(ref: str, db: Session = Depends(get_db)):
    c = _cap(db, ref)
    out = K.read(db, c)
    out["spec"] = c.spec
    out["verification_checks"] = (c.verification or {}).get("checks", [])
    out["provenance"] = c.provenance
    db.commit()
    return out


@router.post("/api/software/capabilities/{ref}/collect")
def collect_capability(ref: str, db: Session = Depends(get_db)):
    c = _cap(db, ref)
    if c.status not in ("usable", "degraded"):
        raise HTTPException(409, f"capability is {c.status}")
    K.collect(db, c)
    out = K.read(db, c)
    db.commit()
    return out


@router.get("/api/software/resources")
def resources():
    from regent.software.resources import inventory

    return inventory()


@router.get("/api/missions/{mission_id}/software")
def mission_software(mission_id: str, db: Session = Depends(get_db)):
    from regent.db import Mission
    from regent.software.domain import latest_plan
    from regent.software.humantime import ledger

    m = db.get(Mission, mission_id)
    if m is None:
        raise HTTPException(404)
    disc = latest_plan(db, mission_id, "discover") or {}
    caps = list(db.scalars(select(SwCapability).where(SwCapability.mission_id == mission_id)))
    return {"need": (m.attrs or {}).get("need"),
            "discovery": {"subjects": [{k: v for k, v in s.items() if k != "probe_log"} for s in disc.get("subjects", [])]},
            "inventory": latest_plan(db, mission_id, "inventory"),
            "capabilities": [{"slug": c.slug, "status": c.status, "version": c.version, "coverage": c.coverage,
                              "view": f"/software/{c.slug}"} for c in caps],
            "human_time": ledger(db, mission_id)}
