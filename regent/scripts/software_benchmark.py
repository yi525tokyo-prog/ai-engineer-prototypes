"""Software-need benchmark: one sentence in, a verified capability Regent keeps using out.

A fresh world receives only a sentence. Nothing names a provider, metric, framework, database,
UI or deployment. Regent must work out what information is needed, look at the world, choose
between existing software, integration and building, execute, verify on its own terms, and
leave a capability it uses again.

    python scripts/software_benchmark.py --out docs/benchmarks/software-needs.md

Phases (one world, in order):
  A  "I want to know, at a glance, how many real people are actually using LindyBooks."
  B  a later, differently worded mission about the same thing (must reuse, not rebuild)
  C  time passes: Regent re-reads its capabilities on its own
  D  a materially different need: "Every morning, tell me whether it's a good day to dry laundry
     outside in Osaka." (place, third-party data, decision rule, existing services compete)
  E  the morning delivery

Uses REGENT_DATABASE_URL (dedicated database, reset) and the live public web. The reasoning
worker is the Claude Code CLI (tool-less); its answers are recorded under the workspace.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LINDY = "I want to know, at a glance, how many real people are actually using LindyBooks."
LINDY_AGAIN = "How many people actually read LindyBooks these days?"
LAUNDRY = "Every morning, tell me whether it's a good day to dry laundry outside in Osaka."


def run_mission(s, sentence: str, max_ticks: int = 10) -> dict:
    from regent.core.goals.missions import MissionGraph
    from regent.core.loop import RegentLoop

    m = MissionGraph(s).create(title=sentence, objective=sentence)
    s.commit()
    ticks, t0 = [], time.time()
    for i in range(max_ticks):
        r = RegentLoop(s).tick(m.id, force=(i == 0))
        ticks.append({"tick": r.tick, "status": r.status, "progress": r.progress, "idle": r.idle,
                      "seconds": round(time.time() - t0, 1), "phases": r.phases})
        print(f"  tick {r.tick} +{time.time() - t0:.0f}s {r.status} progress={r.progress}", flush=True)
        if r.idle:
            break
    return {"mission_id": m.id, "sentence": sentence, "ticks": ticks, "seconds": round(time.time() - t0, 1)}


def screenshot(html: str, path: Path) -> None:
    from playwright.sync_api import sync_playwright

    from regent.browser.driver import _chromium_executable

    tmp = path.with_suffix(".html")
    tmp.write_text(html)
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception:
            b = pw.chromium.launch(executable_path=_chromium_executable())
        pg = b.new_page(viewport={"width": 1000, "height": 700})
        pg.goto(tmp.resolve().as_uri())
        pg.screenshot(path=str(path), full_page=True)
        b.close()
    tmp.unlink()


def collect(s, run: dict) -> dict:
    """Everything the report needs about one mission, read back from Regent's own records."""
    from sqlalchemy import select

    from regent.db import Decision, HumanInterrupt, Mission, ModelCall, Operation, Route
    from regent.software import capability as K
    from regent.software.domain import latest_plan
    from regent.software.humantime import ledger
    from regent.software.tables import SwCapability

    mid = run["mission_id"]
    s.expire_all()
    m = s.get(Mission, mid)
    caps = list(s.scalars(select(SwCapability).where(SwCapability.mission_id == mid)))
    used = (m.attrs or {}).get("need", {}).get("reuse") or []
    if not caps and used:
        caps = [K.get(s, used[0]["id"])]
    cap = caps[0] if caps else None
    reads = K.read(s, cap, count_use=False) if cap else None
    return {
        **run, "status": m.status, "need": (m.attrs or {}).get("need"),
        "discover": latest_plan(s, mid, "discover"), "inventory": latest_plan(s, mid, "inventory"),
        "routes": [{"key": r.key, "title": r.title, "status": r.status, "score": r.score, "rank": r.rank,
                    "effective": r.effective, "invalid": r.invalidated_reason, "thesis": r.thesis,
                    "breakdown": r.score_breakdown}
                   for r in sorted(s.scalars(select(Route).where(Route.mission_id == mid)),
                                   key=lambda r: (r.rank or 99))],
        "selected": (s.get(Route, m.selected_route_id).key if m.selected_route_id else None),
        "operations": [{"key": o.key, "tool": f"{o.tool}.{o.action}", "status": o.status, "error": o.error,
                        "authority": o.required_authority}
                       for o in s.scalars(select(Operation).where(Operation.mission_id == mid)
                                          .order_by(Operation.created_at))],
        "interrupts": [{"kind": h.kind, "status": h.status, "action": h.required_action,
                        "seconds": h.estimated_time_seconds, "resume": h.resume_condition, "reason": h.reason}
                       for h in s.scalars(select(HumanInterrupt).where(HumanInterrupt.mission_id == mid))],
        "decisions": [{"kind": d.kind, "summary": d.summary} for d in
                      s.scalars(select(Decision).where(Decision.mission_id == mid).order_by(Decision.created_at))],
        "model_calls": [{"task": c.task, "cost": c.cost_usd, "ms": c.latency_ms}
                        for c in s.scalars(select(ModelCall).where(ModelCall.mission_id == mid))],
        "capability": None if cap is None else {
            "slug": cap.slug, "status": cap.status, "version": cap.version, "implementation": cap.implementation,
            "tool": cap.tool_name, "coverage": cap.coverage, "verification": cap.verification,
            "spec_sources": [{k: x.get(k) for k in ("id", "connector", "title", "access")} for x in cap.spec["sources"]],
            "uses": cap.uses, "provenance": cap.provenance},
        "read": reads, "human_time": ledger(s, mid),
        "view_html": K.render_html(reads) if reads else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/benchmarks/software-needs.md")
    a = ap.parse_args()
    from regent import db as dbm
    from regent.software import capability as K

    dbm.configure()
    dbm.init_db(drop=True)
    s = dbm.session()
    from regent.runtime import get_services

    K.load_all(s, get_services())
    out: dict = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    t0 = time.time()

    print("A. LindyBooks", flush=True)
    out["A"] = collect(s, run_mission(s, LINDY))
    print("B. the same need, worded differently", flush=True)
    out["B"] = collect(s, run_mission(s, LINDY_AGAIN))

    print("C. time passes; Regent keeps its capabilities current", flush=True)
    from regent.core.loop import RegentLoop
    from regent.software.tables import SwCapability, SwObservation

    cap = s.query(SwCapability).filter(SwCapability.slug == out["A"]["capability"]["slug"]).one()
    before = s.query(SwObservation).filter(SwObservation.capability_id == cap.id).count()
    from regent.ids import utcnow

    K.CLOCK = lambda: utcnow() + timedelta(hours=1)    # the refresh period elapses; the reads are live
    maint = RegentLoop(s).maintain()
    K.CLOCK = None
    s.expire_all()
    after = s.query(SwObservation).filter(SwObservation.capability_id == cap.id).count()
    out["C"] = {"maintenance": maint, "observations_before": before, "observations_after": after}

    print("D. a different need", flush=True)
    out["D"] = collect(s, run_mission(s, LAUNDRY))
    print("E. the morning delivery", flush=True)
    from sqlalchemy import select

    from regent.db import Event

    out["E"] = {"maintenance": RegentLoop(s).maintain(),
                "notified": [e.payload for e in s.scalars(select(Event).where(Event.type == "principal_notified"))]}
    from regent.software.resources import inventory

    out["resources"] = inventory()
    out["seconds"] = round(time.time() - t0)
    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    for key, name in (("A", "software-lindybooks-view.png"), ("D", "software-laundry-view.png")):
        if out[key].get("view_html"):
            screenshot(out[key]["view_html"], outp.parent / name)
    (outp.with_suffix(".json")).write_text(json.dumps(
        {k: ({kk: vv for kk, vv in v.items() if kk != "view_html"} if isinstance(v, dict) else v)
         for k, v in out.items()}, ensure_ascii=False, indent=1, default=str))
    from software_report import render  # noqa: E402  (sibling module)

    outp.write_text(render(out))
    print(f"wrote {outp}", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
