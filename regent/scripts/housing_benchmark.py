"""Housing benchmark: an unseen query with nothing seeded.

A fresh world receives one sentence -- 「住居を安定させたい」 ("I want to stabilize my
housing") -- and nothing else: no tags, no candidate list, no areas, no budget. The
Regent loop must notice what it does not know, go to the public web, build the world
model (claims -> entities -> beliefs) and compete housing strategies on it.

    python scripts/housing_benchmark.py --ticks 6 --out docs/benchmarks/housing-live.md
    python scripts/housing_benchmark.py --report-only --out docs/benchmarks/housing-live.md

Uses REGENT_DATABASE_URL (a dedicated database: it is reset unless --report-only/--resume).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MISSION = "住居を安定させたい"


def advance_and_recheck(hours: float, ticks: int) -> list[dict]:
    """Simulate time passing for the acquisition layer only (claims age past their TTL); the
    re-observation itself is live. The loop must notice staleness on its own."""
    from datetime import timedelta

    from sqlalchemy import select

    from regent import db as dbm
    from regent.acquisition import service
    from regent.core.loop import RegentLoop
    from regent.db import Mission
    from regent.ids import utcnow

    service.CLOCK = lambda: utcnow() + timedelta(hours=hours)
    s = dbm.session()
    m = s.scalars(select(Mission)).first()
    out = []
    t0 = time.time()
    for _ in range(ticks):
        r = RegentLoop(s).tick(m.id)
        out.append({"tick": r.tick, "status": r.status, "idle": r.idle, "progress": r.progress,
                    "seconds": round(time.time() - t0), "phases": r.phases, "clock_offset_h": hours})
        print(f"[+{hours}h] tick {r.tick}: status={r.status} progress={r.progress} idle={r.idle}", flush=True)
        if r.idle:
            break
    s.close()
    service.CLOCK = None
    return out


def run(ticks: int, pages: int, resume: bool) -> list[dict]:
    from sqlalchemy import select

    from regent import db as dbm
    from regent.acquisition import service
    from regent.core.goals.missions import MissionGraph
    from regent.core.loop import RegentLoop
    from regent.db import Mission
    from regent.runtime import get_services

    dbm.configure()
    dbm.init_db(drop=not resume)
    service.ENGINE_KW = {"max_pages": pages, "deadline_s": 1500}
    s = dbm.session()
    get_services().tools.load_built(s)
    m = s.scalars(select(Mission)).first() if resume else None
    if m is None:
        m = MissionGraph(s).create(title=MISSION, objective=MISSION)
        s.commit()
    ticks_out = []
    t0 = time.time()
    for i in range(ticks):
        r = RegentLoop(s).tick(m.id, force=(i == 0 and not resume))
        ticks_out.append({"tick": r.tick, "status": r.status, "idle": r.idle, "progress": r.progress,
                          "seconds": round(time.time() - t0), "phases": r.phases})
        print(f"tick {r.tick}: status={r.status} progress={r.progress} idle={r.idle} t={round(time.time() - t0)}s",
              flush=True)
        if r.idle:
            break
    s.close()
    return ticks_out


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)) and abs(v) >= 1000:
        return f"¥{int(v):,}"
    if isinstance(v, list):
        return ", ".join(f"{x.get('station')} {x.get('walk_min')}min" if isinstance(x, dict) and "station" in x
                         else json.dumps(x, ensure_ascii=False) for x in v[:4])
    return str(v)


def report(ticks: list[dict] | None) -> str:
    from sqlalchemy import func, select

    from regent import db as dbm
    from regent.acquisition.tables import (AcqClaim, AcqConflict, AcqDocument, AcqEntity, AcqJob, AcqLink,
                                           AcqRequest, AcqSource)
    from regent.db import Decision, Mission, Operation, Route

    dbm.configure()
    s = dbm.session()
    m = s.scalars(select(Mission)).first()
    L = [f"# Housing benchmark — 「{MISSION}」", "",
         "A fresh world received only the sentence above: no tags, no candidates, no areas, no budget.",
         "Everything below was acquired by Regent from the public web during the run.", ""]
    reqs = list(s.scalars(select(AcqRequest).order_by(AcqRequest.created_at)))
    L += ["## Acquisition requests (created by the loop's ACQUIRE phase)", "",
          "| request | action | status | pages | mentions | claims |", "|---|---|---|---|---|---|"]
    for r in reqs:
        st = r.stats or {}
        L.append(f"| {r.id} | {r.goal} | {r.status} | {st.get('pages', 0)} | {st.get('mentions', 0)} | {st.get('claims', 0)} |")
    disc = next((r for r in reqs if r.goal == "discover"), None)
    if disc is not None:
        L += ["", "### Plan and assumptions", "", f"- areas: {', '.join((disc.plan or {}).get('areas', []))}",
              f"- why: {(disc.plan or {}).get('area_rationale', '')}"]
        for a in (disc.params or {}).get("assumptions", []):
            if a.get("why"):
                L.append(f"- assumption: {a['key']} = {a['value']} ({a['why']})")
        f = (disc.stats or {}).get("funnel", {})
        L += ["", "### Funnel", "",
              f"{f.get('mentions')} listing mentions → {f.get('units')} units after identity resolution "
              f"({f.get('buildings')} buildings) → {f.get('passed_filters')} pass basic filters → "
              f"{f.get('filtered')} filtered → {f.get('shortlisted')} deep research", "",
              "Rejection reasons: " + "; ".join(f"{k} ×{v}" for k, v in (f.get("rejection_reasons") or {}).items())]
    L += ["", "## Sources", "", "| host | kind | pages ok/fetched | records | blocked | robots-disallowed | status |",
          "|---|---|---|---|---|---|---|"]
    for src in s.scalars(select(AcqSource).order_by(AcqSource.fetches.desc())):
        L.append(f"| {src.host} | {src.kind} | {src.ok}/{src.fetches} | {src.records} | {src.blocked} | "
                 f"{src.disallowed} | {src.last_status} |")
    links = dict(s.execute(select(AcqLink.decision, func.count()).group_by(AcqLink.decision)).all())
    units = list(s.scalars(select(AcqEntity).where(AcqEntity.entity_type == "unit")))
    multi = [u for u in units if len(u.source_hosts or []) >= 2]
    conf = Counter(c.attribute for c in s.scalars(select(AcqConflict).where(AcqConflict.status == "open")))
    L += ["", "## World model", "",
          f"- documents: {s.scalar(select(func.count()).select_from(AcqDocument))}, "
          f"claims: {s.scalar(select(func.count()).select_from(AcqClaim))}",
          f"- identity decisions: {links}",
          f"- units seen on ≥2 sites: {len(multi)} ({Counter(tuple(u.source_hosts) for u in multi).most_common(4)})",
          f"- open conflicts (kept, not overwritten): {sum(conf.values())} {dict(conf)}",
          f"- enrichment jobs: {dict(Counter(f'{j.kind}:{j.status}' for j in s.scalars(select(AcqJob))))}"]
    short = sorted(s.scalars(select(AcqEntity).where(AcqEntity.stage == "shortlisted")), key=lambda e: -e.score)
    L += ["", "## Deep-research shortlist", ""]
    for i, e in enumerate(short, 1):
        p = s.get(AcqEntity, e.parent_id) if e.parent_id else None
        pb, b = (p.beliefs if p else {}) or {}, e.beliefs or {}
        name = (pb.get("name") or {}).get("value") or (pb.get("address") or {}).get("value") or e.label
        L.append(f"### {i}. {name} — {e.label} (score {e.score:.3f}; sources: {', '.join(e.source_hosts or [])})")
        L += ["", "| attribute | belief | confidence | sources | fresh | conflict |", "|---|---|---|---|---|---|"]
        for attr, bel in [(a, b.get(a)) for a in ("rent", "management_fee", "deposit", "key_money", "availability",
                                                   "move_in", "info_updated_at", "earliest_move_in_est")] + \
                         [(a, pb.get(a)) for a in ("address", "stations", "built_year", "structure", "coords",
                                                   "nearest_stations_public", "walk_check", "rail_distance_m",
                                                   "libraries_nearby", "universities_nearby", "hub_minutes_est")]:
            if not bel:
                continue
            hosts = sorted({h for hy in bel.get("hypotheses", []) for h in hy.get("hosts", [])})
            alt = ""
            if bel.get("conflict"):
                alt = " vs ".join(f"{_fmt(h['value'])} [{','.join(h['hosts'])}]" for h in bel["hypotheses"][:3])
            L.append(f"| {attr} | {_fmt(bel.get('value'))[:80]} | {bel.get('confidence', 0):.2f} | {', '.join(hosts)} | "
                     f"{'yes' if bel.get('fresh') else 'STALE'} | {alt[:160]} |")
        rej = (e.score_detail or {}).get("rejections")
        if rej:
            L.append(f"\nRejected later: {'; '.join(rej)}")
        L.append("")
    if m is not None:
        L += ["## Strategies (routes) competing on this world", "", "| rank | score | route | status |",
              "|---|---|---|---|"]
        for r in s.scalars(select(Route).where(Route.mission_id == m.id).order_by(Route.rank)):
            L.append(f"| {r.rank} | {r.score:.3f} | {r.title} | {r.status} |")
        sel = s.get(Route, m.selected_route_id) if m.selected_route_id else None
        L += ["", f"Selected: **{sel.title if sel else 'none'}**"]
        d = s.scalars(select(Decision).where(Decision.mission_id == m.id).order_by(Decision.created_at.desc())).first()
        if d is not None:
            L += ["", f"Last decision ({d.kind}): {d.summary}"]
        L += ["", "### Operations", "", "| op | tool.action | status | note |", "|---|---|---|---|"]
        for o in s.scalars(select(Operation).where(Operation.mission_id == m.id).order_by(Operation.created_at)):
            note = (o.error or (o.outputs or {}).get("summary") or "")[:100].replace("|", "/")
            L.append(f"| {o.key} | {o.tool}.{o.action} | {o.status} | {note} |")
    if ticks:
        L += ["", "## Loop ticks", ""]
        for t in ticks:
            ph = {p["phase"]: p for p in t["phases"]}
            acq = ph.get("acquire", {})
            clock = f" [acquisition clock +{t['clock_offset_h']}h, simulated]" if t.get("clock_offset_h") else ""
            L.append(f"- tick {t['tick']} (+{t['seconds']}s){clock}: status {t['status']}; acquire "
                     f"{acq.get('operations', [])}; ran before planning {acq.get('executed_before_planning', [])}; "
                     f"generated {ph.get('generate', {}).get('created', [])}; "
                     f"selected {ph.get('select', {}).get('selected')} ({ph.get('select', {}).get('kind')})")
    s.close()
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=6)
    ap.add_argument("--pages", type=int, default=110)
    ap.add_argument("--out", default="docs/benchmarks/housing-live.md")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--recheck-after-hours", type=float, default=0.0,
                    help="then advance the acquisition clock this far (e.g. 7 > availability TTL 6h) and keep ticking")
    a = ap.parse_args()
    if not os.environ.get("REGENT_DATABASE_URL"):
        sys.exit("set REGENT_DATABASE_URL to a dedicated database (it is reset)")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    ticks = None if a.report_only else run(a.ticks, a.pages, a.resume)
    if ticks is not None and a.recheck_after_hours:
        ticks += advance_and_recheck(a.recheck_after_hours, 4)
    if ticks is not None:
        Path(a.out).with_suffix(".ticks.json").write_text(json.dumps(ticks, ensure_ascii=False, default=str,
                                                                       indent=1))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report(ticks))
    print(f"report: {out}")


if __name__ == "__main__":
    main()
