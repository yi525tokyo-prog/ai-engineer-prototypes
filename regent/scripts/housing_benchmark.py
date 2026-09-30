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
    service.ENGINE_KW = {"max_pages": pages, "deadline_s": 3000}
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


def _fmt(v, cur: str = "JPY", attr: str = "") -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)) and attr in ("rent", "management_fee", "deposit", "key_money"):
        return f"{int(v):,} {cur}"
    if isinstance(v, (int, float)) and abs(v) >= 1000:
        return f"{int(v):,}"
    if isinstance(v, list):
        return ", ".join(f"{x.get('station')} {x.get('walk_min')}min" if isinstance(x, dict) and "station" in x
                         else json.dumps(x, ensure_ascii=False) for x in v[:4])
    return str(v)


def report(ticks: list[dict] | None) -> str:

    from sqlalchemy import func, select

    from regent import db as dbm
    from regent.acquisition.tables import (AcqClaim, AcqConflict, AcqDocument, AcqEntity, AcqJob, AcqLink,
                                           AcqRequest, AcqSource, AcqSourceRecipe)
    from regent.db import Decision, Mission, Operation, Route

    dbm.configure()
    s = dbm.session()
    m = s.scalars(select(Mission)).first()
    L = [f"# Housing benchmark — 「{MISSION}」", "",
         "A fresh world received only the sentence above: no tags, no candidates, no places, no budget, no",
         "country. Everything below was acquired by Regent from the public web during the run.", ""]
    reqs = list(s.scalars(select(AcqRequest).order_by(AcqRequest.created_at)))
    disc = next((r for r in reqs if r.goal == "discover"), None)
    plan = (disc.plan or {}) if disc else {}
    regions = list(s.scalars(select(AcqEntity).where(AcqEntity.entity_type == "region")))
    units = list(s.scalars(select(AcqEntity).where(AcqEntity.entity_type == "unit")))
    home = (plan.get("principal") or {}).get("home")
    units_by_region = Counter(u.region_id for u in units)
    foreign = [r for r in regions if (r.features or {}).get("country") != home and units_by_region.get(r.id, 0) >= 5]
    routes = list(s.scalars(select(Route).where(Route.mission_id == m.id))) if m else []
    abroad_routes = [r for r in routes if "relocation" in (r.tags or [])]
    checks = [
        ("regions came from the candidate world, not a default", bool(plan.get("regions")) and
         not (disc.params or {}).get("regions")),
        (">= 2 non-home regions with >= 5 live units each", len(foreign) >= 2),
        (">= 2 different countries acquired outside the home country",
         len({(r.features or {}).get("country") for r in foreign}) >= 2),
        ("strategies compete across borders (a relocation route exists)", bool(abroad_routes)),
        ("right to stay abroad is an explicit uncertainty",
         any(any(u.get("fact_key", "").startswith("principal.right_to_reside") for u in (r.uncertainty or []))
             for r in abroad_routes)),
    ]
    ok = all(v for _, v in checks)
    L += [f"## Result: {'PASS' if ok else 'FAIL'} — the world was {'not ' if ok else ''}silently narrowed to "
          f"{home or 'one country'}", ""]
    L += [f"- [{'x' if v else ' '}] {k}" for k, v in checks] + [""]
    L += ["## Acquisition requests (created by the loop's ACQUIRE phase)", "",
          "| request | action | status | pages | mentions | claims |", "|---|---|---|---|---|---|"]
    for r in reqs:
        st = r.stats or {}
        L.append(f"| {r.id} | {r.goal} | {r.status} | {st.get('pages', 0)} | {st.get('mentions', 0)} | {st.get('claims', 0)} |")
    if disc is not None:
        prin = plan.get("principal") or {}
        ref = plan.get("ref_currency")
        L += ["", "## Geography: where could the principal live?", "",
              f"- principal evidence: home={prin.get('home')} ({prin.get('home_evidence') or 'none'}); "
              f"citizenship={prin.get('citizenship') or 'unknown'}",
              f"- reference currency: {ref} (ECB rates: {(plan.get('fx') or {}).get('source')})",
              f"- method: {plan.get('geography_rationale')}"]
        for a in (disc.params or {}).get("assumptions", []):
            if a.get("why"):
                L.append(f"- assumption: {a['key']} — {a['why']}")
        L += ["", "| region | price signal (/month) | utility | P(may live there) | distance | units acquired |",
              "|---|---|---|---|---|---|"]
        acq = {x.get("id"): x for x in plan.get("regions_acquired") or []}
        for r in plan.get("regions") or []:
            L.append(f"| {r['name']} ({r['country']}){' — home evidence' if r.get('home') else ''} | "
                     f"{int(r['price_ref']):,} {ref} | {r.get('utility')} | {r.get('stay_p')} | "
                     f"{r.get('distance_km') if r.get('distance_km') is not None else '—'} km | "
                     f"{(acq.get(r['id']) or {}).get('units', 0)} |" if r.get("price_ref") else
                     f"| {r['name']} ({r['country']}) | — | {r.get('utility')} | {r.get('stay_p')} | — | "
                     f"{(acq.get(r['id']) or {}).get('units', 0)} |")
        if plan.get("regions_rejected"):
            L += ["", "Considered and not acquired:", ""]
            for r in plan["regions_rejected"][:12]:
                pr = f", {int(r['price_ref']):,} {ref}" if r.get("price_ref") else ""
                L.append(f"- {r['name']} ({r['country']}): {r['reason']}{pr}")
        L += ["", "### Per region: sources and discovery", ""]
        for x in plan.get("regions_acquired") or []:
            sm = x.get("summary") or {}
            L.append(f"**{x['name']} ({x['country']})** — {x.get('units', 0)} units"
                     + (f"; areas: {', '.join(sm.get('areas') or [])}" if sm.get("areas") else ""))
            for d in sm.get("discovery") or []:
                L.append(f"- discovery via {d.get('channel')}: {d.get('host')} → {d.get('status')}"
                         + (f" ({d.get('why')})" if d.get("why") else f", {d.get('records')} records"))
            for src in sm.get("sources") or []:
                L.append(f"- source {src.get('host')} ({src.get('origin')}): {src.get('records')} records"
                         + (f" [{src.get('status')}]" if src.get("status") else ""))
            if sm.get("error"):
                L.append(f"- error: {sm['error']}")
            L.append("")
        f = (disc.stats or {}).get("funnel", {})
        L += ["### Funnel", "",
              f"{f.get('mentions')} listing mentions → {f.get('units')} units after identity resolution "
              f"({f.get('buildings')} buildings) → {f.get('passed_filters')} pass → {f.get('filtered')} filtered → "
              f"{f.get('shortlisted')} deep research", "",
              "| region | units | pass | shortlisted | market median /month |", "|---|---|---|---|---|"]
        for name, st in (f.get("regions") or {}).items():
            mm = f"{int(st['market_monthly']):,} {st.get('currency')}" if st.get("market_monthly") else "—"
            L.append(f"| {name} | {st.get('units')} | {st.get('passed')} | {st.get('shortlisted')} | {mm} |")
        L += ["", "Rejection reasons: " + "; ".join(f"{k} ×{v}" for k, v in (f.get("rejection_reasons") or {}).items())]
    L += ["", "## Sources", "", "| host | kind | pages ok/fetched | records | blocked | robots-disallowed | status |",
          "|---|---|---|---|---|---|---|"]
    for src in s.scalars(select(AcqSource).order_by(AcqSource.fetches.desc())):
        L.append(f"| {src.host} | {src.kind} | {src.ok}/{src.fetches} | {src.records} | {src.blocked} | "
                 f"{src.disallowed} | {src.last_status} |")
    L += ["", "### Source registry (what Regent now knows how to use)", "",
          "| scope | host | origin | status | records | note |", "|---|---|---|---|---|---|"]
    for r in s.scalars(select(AcqSourceRecipe).order_by(AcqSourceRecipe.scope, AcqSourceRecipe.status)):
        note = ((r.evidence or {}).get("why") or (r.evidence or {}).get("last_note") or "")[:80].replace("|", "/")
        L.append(f"| {r.scope} | {r.host} | {r.origin} | {r.status} | {r.records} | {note} |")
    links = dict(s.execute(select(AcqLink.decision, func.count()).group_by(AcqLink.decision)).all())
    multi = [u for u in units if len(u.source_hosts or []) >= 2]
    conf = Counter(c.attribute for c in s.scalars(select(AcqConflict).where(AcqConflict.status == "open")))
    L += ["", "## World model", "",
          f"- documents: {s.scalar(select(func.count()).select_from(AcqDocument))}, "
          f"claims: {s.scalar(select(func.count()).select_from(AcqClaim))}, regions: {len(regions)}",
          f"- identity decisions: {links}",
          f"- units seen on ≥2 sites: {len(multi)}",
          f"- open conflicts (kept, not overwritten): {sum(conf.values())} {dict(conf)}",
          f"- enrichment jobs: {dict(Counter(f'{j.kind}:{j.status}' for j in s.scalars(select(AcqJob))))}"]
    short = sorted(s.scalars(select(AcqEntity).where(AcqEntity.stage == "shortlisted",
                                                     AcqEntity.entity_type == "unit")),
                   key=lambda e: ((e.region_id or ""), -e.score))
    rname = {r.id: r.label for r in regions}
    L += ["", "## Deep-research shortlist (per region)", ""]
    for i, e in enumerate(short, 1):
        p = s.get(AcqEntity, e.parent_id) if e.parent_id else None
        pb, b = (p.beliefs if p else {}) or {}, e.beliefs or {}
        name = (pb.get("name") or {}).get("value") or (pb.get("address") or {}).get("value") or \
            (b.get("title") or {}).get("value") or e.label
        cur = (b.get("currency") or {}).get("value") or "JPY"
        L.append(f"### {i}. [{rname.get(e.region_id, '?')}] {name} — {e.label} (score {e.score:.3f}; "
                 f"sources: {', '.join(e.source_hosts or [])})")
        L += ["", "| attribute | belief | confidence | sources | fresh | conflict |", "|---|---|---|---|---|---|"]
        for attr, bel in [(a, b.get(a)) for a in ("rent", "rent_period", "management_fee", "deposit", "key_money",
                                                   "bedrooms", "unit_kind", "availability", "move_in",
                                                   "info_updated_at", "earliest_move_in_est")] + \
                         [(a, pb.get(a)) for a in ("address", "postcode", "stations", "built_year", "coords",
                                                   "centre_km", "nearest_stations_public", "rail_distance_m",
                                                   "libraries_nearby", "hub_minutes_est")]:
            if not bel:
                continue
            hosts = sorted({h for hy in bel.get("hypotheses", []) for h in hy.get("hosts", [])})
            alt = ""
            if bel.get("conflict"):
                alt = " vs ".join(f"{_fmt(h['value'], cur, attr)} [{','.join(h['hosts'])}]" for h in bel["hypotheses"][:3])
            L.append(f"| {attr} | {_fmt(bel.get('value'), cur, attr)[:80]} | {bel.get('confidence', 0):.2f} | "
                     f"{', '.join(hosts)} | {'yes' if bel.get('fresh') else 'STALE'} | {alt[:160]} |")
        L.append("")
    if m is not None:
        L += ["## Strategies competing on this world", "", "| rank | score | route | status |", "|---|---|---|---|"]
        for r in sorted(routes, key=lambda r: r.rank or 99):
            L.append(f"| {r.rank} | {r.score:.3f} | {r.title} | {r.status} |")
        sel = s.get(Route, m.selected_route_id) if m.selected_route_id else None
        L += ["", f"Selected: **{sel.title if sel else 'none'}**"]
        if sel is not None:
            L += ["", f"> {sel.thesis}", ""]
            rationale = next((v.get("rationale") for v in (sel.estimate_sources or {}).values()
                              if isinstance(v, dict) and v.get("rationale")), {})
            for k, v in (rationale or {}).items():
                L.append(f"- {k}: {v}")
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
    ap.add_argument("--pages", type=int, default=300)
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
