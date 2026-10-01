"""Application benchmark: a need no data, rule or single connector can satisfy.

A fresh world receives only sentences. Nothing names a framework, database, language, UI or
deployment. The first sentence can only be met by software that does not exist yet and that keeps
the principal's own data: Regent must find that out, design it, have a coding agent build it,
inspect/build/test/run/browser-accept/repair it itself, keep it running and register it. Later
sentences must be met with *that* application -- used, then extended to a new version on the
real data with backup and rollback, then used again.

    REGENT_DATABASE_URL=postgresql+psycopg://regent:regent@localhost/regent_appbench \\
    REGENT_WORKSPACE=var/appbench REGENT_CODING_AGENT=claude-code \\
    python scripts/app_benchmark.py --out docs/benchmarks/application-capability.md

Missions (one world, in order):
  M1  build      the reading place (private, persistent, any browser)
  M2  use        put a book on it, with progress and a note
  M2b use        a second book (so isolation can be tested later)
  M3  extend     share one book's notes with a friend, nothing else (forces v2: access control,
                 migration of real data, promotion with backup/rollback)
  M4  use v2     make that link; Regent opens it as a stranger and checks what it shows
  K   lifecycle  the live process is killed; Regent's maintenance pass must bring it back

The principal approves the coding agent when Regent asks (an authorization interrupt; COMMIT
authority), which the benchmark does on their behalf and counts as their time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

M1 = ("I read old books on LindyBooks on my phone and keep losing track of where I stopped and what I thought about "
      "them. I want a private place, usable from any browser, to keep my reading and my notes.")
M2 = "Put The Odyssey on my reading list - I'm on book 3 - and note that the Butler translation reads well."
M2B = "Add Moby-Dick too: I'm at chapter 12, and the whale-anatomy chapters drag."
M3 = "I want to send a friend a link to my notes on one book, without them seeing anything else."
M4 = "Make me a link I can send my friend to my notes on The Odyssey."

APPROVE = {"software.build_app", "software.extend_app"}


def run_mission(s, sentence: str, max_ticks: int = 14) -> dict:
    from sqlalchemy import select

    from regent.core.goals.missions import MissionGraph
    from regent.core.human.interrupts import HumanInterruptManager
    from regent.core.loop import RegentLoop
    from regent.db import HumanInterrupt, Operation

    m = MissionGraph(s).create(title=sentence, objective=sentence)
    s.commit()
    ticks, approvals, t0 = [], [], time.time()
    for i in range(max_ticks):
        r = RegentLoop(s).tick(m.id, force=(i == 0))
        ticks.append({"tick": r.tick, "status": r.status, "progress": r.progress, "idle": r.idle,
                      "seconds": round(time.time() - t0, 1)})
        print(f"  tick {r.tick} +{time.time() - t0:.0f}s {r.status} progress={r.progress}", flush=True)
        s.expire_all()
        opened = list(s.scalars(select(HumanInterrupt).where(HumanInterrupt.mission_id == m.id,
                                                             HumanInterrupt.status == "open")))
        acted = False
        for hi in opened:
            op = s.get(Operation, hi.operation_id) if hi.operation_id else None
            what = f"{op.tool}.{op.action}" if op else None
            if hi.kind == "authorization" and what in APPROVE:
                # the principal's explicit instruction in this session: run the coding worker for real
                HumanInterruptManager(s).resolve(hi.id, {"approve": True, "active_seconds": 20})
                s.commit()
                approvals.append({"interrupt": hi.id, "operation": what, "reason": hi.reason,
                                  "action": hi.required_action})
                print(f"  principal approved {what}", flush=True)
                acted = True
        if r.idle and not acted:
            break
    return {"mission_id": m.id, "sentence": sentence, "ticks": ticks, "approvals": approvals,
            "seconds": round(time.time() - t0, 1)}


def collect(s, run: dict) -> dict:
    from sqlalchemy import select

    from regent.db import Decision, Event, HumanInterrupt, Mission, ModelCall, Operation, Route
    from regent.software.domain import latest_plan
    from regent.software.humantime import ledger

    mid = run["mission_id"]
    s.expire_all()
    m = s.get(Mission, mid)
    need = (m.attrs or {}).get("need") or {}
    return {
        **run, "status": m.status, "need": need, "design": (m.attrs or {}).get("app_design"),
        "inventory": latest_plan(s, mid, "inventory"), "discover": latest_plan(s, mid, "discover"),
        "routes": [{"key": r.key, "title": r.title, "status": r.status, "score": r.score, "rank": r.rank,
                    "invalid": r.invalidated_reason, "thesis": r.thesis}
                   for r in sorted(s.scalars(select(Route).where(Route.mission_id == mid)), key=lambda r: (r.rank or 99))],
        "selected": (s.get(Route, m.selected_route_id).key if m.selected_route_id else None),
        "operations": [{"key": o.key, "tool": f"{o.tool}.{o.action}", "status": o.status, "error": o.error,
                        "authority": o.required_authority, "outputs": o.outputs}
                       for o in s.scalars(select(Operation).where(Operation.mission_id == mid)
                                          .order_by(Operation.created_at))],
        "interrupts": [{"kind": h.kind, "status": h.status, "action": h.required_action,
                        "seconds": h.estimated_time_seconds, "reason": h.reason}
                       for h in s.scalars(select(HumanInterrupt).where(HumanInterrupt.mission_id == mid))],
        "decisions": [{"kind": d.kind, "summary": d.summary} for d in
                      s.scalars(select(Decision).where(Decision.mission_id == mid).order_by(Decision.created_at))],
        "model_calls": [{"task": c.task, "cost": c.cost_usd} for c in
                        s.scalars(select(ModelCall).where(ModelCall.mission_id == mid))],
        "notified": [e.payload for e in s.scalars(select(Event).where(Event.type == "principal_notified",
                                                                      Event.mission_id == mid))],
        "human_time": ledger(s, mid),
    }


def app_state(s) -> dict:
    from sqlalchemy import select

    from regent.software import appaccept as A
    from regent.software import appcap
    from regent.software.tables import SwCapability

    s.expire_all()
    caps = list(s.scalars(select(SwCapability).where(SwCapability.implementation == "application")))
    out = []
    for c in caps:
        r = appcap.runner(c)
        snap = A.snapshot(r.svc, c.spec["design"], r.passphrase)
        out.append({"id": c.id, "slug": c.slug, "version": c.version, "status": c.status, "coverage": c.coverage,
                    "uses": c.uses, "tool": c.tool_name, "app": {k: v for k, v in c.spec["app"].items()},
                    "api": [{k: a.get(k) for k in ("id", "method", "path", "public")} for a in c.spec["design"]["api"]],
                    "verification": c.verification, "data": snap,
                    "versions": c.provenance.get("versions") if c.provenance else None})
    return {"applications": out}


def screenshot_app(s, path: Path, *, login: bool = True, url_path: str = "/") -> str:
    """What the principal sees in a phone-sized browser (Regent's own look, not the worker's)."""
    from playwright.sync_api import sync_playwright
    from sqlalchemy import select

    from regent.browser.driver import _chromium_executable
    from regent.software import appcap, secrets
    from regent.software.tables import SwCapability

    c = s.scalars(select(SwCapability).where(SwCapability.implementation == "application")).first()
    svc = appcap.live(c)
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception:
            b = pw.chromium.launch(executable_path=_chromium_executable())
        pg = b.new_page(viewport={"width": 390, "height": 844})
        pg.goto(svc.url + url_path)
        pg.wait_for_load_state("networkidle")
        if login and c.spec["app"].get("credential"):
            try:
                pg.fill('[data-testid="login-passphrase"]', secrets.get(c.spec["app"]["credential"]), timeout=5000)
                pg.click('[data-testid="login-submit"]')
                pg.wait_for_load_state("networkidle")
                time.sleep(1)
            except Exception:
                pass
        pg.screenshot(path=str(path), full_page=True)
        text = pg.inner_text("body")
        b.close()
    return text


def stranger_view(url: str) -> str:
    from playwright.sync_api import sync_playwright

    from regent.browser.driver import _chromium_executable

    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception:
            b = pw.chromium.launch(executable_path=_chromium_executable())
        pg = b.new_context().new_page()
        pg.goto(url)
        pg.wait_for_load_state("networkidle")
        time.sleep(1)
        text = pg.inner_text("body")
        b.close()
    return text


def find_share_url(m4: dict, base: str) -> str | None:
    import re

    blob = json.dumps(m4.get("operations"), default=str)
    for u in re.findall(r"https?://127\.0\.0\.1:\d+/[^\s\"'\\]+", blob):
        if "/api/" not in u or "share" in u:
            return u
    for p in re.findall(r"\"(/(?:s|share|shared|public|view)[^\s\"'\\]+)\"", blob):
        return base + p
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/benchmarks/application-capability.md")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    from regent import db as dbm
    from regent.runtime import get_services
    from regent.software import appservice as S
    from regent.software import capability as K

    if os.environ.get("REGENT_CODING_AGENT") != "claude-code":
        sys.exit("set REGENT_CODING_AGENT=claude-code (the principal's opt-in)")
    dbm.configure()
    dbm.init_db(drop=True)
    s = dbm.session()
    K.load_all(s, get_services())
    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    out: dict = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "workspace": str(S.apps_root().parent)}
    t0 = time.time()

    def save():
        out["seconds"] = round(time.time() - t0)
        outp.with_suffix(".json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))

    try:
        for key, sentence in (("M1", M1), ("M2", M2), ("M2b", M2B), ("M3", M3), ("M4", M4)):
            print(f"{key}. {sentence}", flush=True)
            out[key] = collect(s, run_mission(s, sentence))
            try:
                out[key]["after"] = app_state(s)
            except Exception as e:      # noqa: BLE001 -- report it; the next mission shows the consequence
                out[key]["after"] = {"error": f"{type(e).__name__}: {e}"}
            save()
            if key == "M1" and not (out[key]["after"].get("applications")):
                print("no application after M1; stopping", flush=True)
                break
            if key == "M2":
                out["screens"] = {"after_M2": screenshot_app(s, outp.parent / "app-after-m2.png")}
        if out.get("M4"):
            apps = (out["M4"].get("after") or {}).get("applications") or []
            base = apps[0]["app"].get("url") if apps else ""
            share = find_share_url(out["M4"], base or "")
            out["stranger"] = {"url": share}
            if share:
                text = stranger_view(share)
                out["stranger"].update({
                    "text": text[:3000], "shows_shared_note": "butler" in text.lower(),
                    "leaks_other_book": any(w in text.lower() for w in ("moby", "whale")),
                    "leaks_login_or_list": "login-passphrase" in text})
            save()
        print("K. the live process dies; maintenance must restore it", flush=True)
        from regent.core.loop import RegentLoop

        killed = []
        for svc in list(S._RUNNING.values()):
            if svc.role == "live" and svc.proc:
                os.killpg(svc.proc.pid, 9)
                svc.proc.wait()
                killed.append(svc.slug)
        maint = RegentLoop(s).maintain()
        out["K"] = {"killed": killed, "maintenance": maint, "after": app_state(s)}
        save()
    finally:
        out["seconds"] = round(time.time() - t0)
        save()
        S.stop_all()
    from app_report import render  # noqa: E402  (sibling module)

    outp.write_text(render(out))
    print(f"wrote {outp}", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
