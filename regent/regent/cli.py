"""Headless operation: ``python -m regent.cli <command>``.

    init                 create schema (idempotent)
    seed [--api URL]     reset DB and load the case study
    run                  drive all active missions to quiescence
    status [MISSION]     print the cockpit essentials
    interrupts           list open human interrupts
    resolve ID [k=v...]  answer an interrupt (Regent resumes on next run)
    rebuild              rebuild world projections from the event log
"""

from __future__ import annotations

import json
import sys

from regent import db as dbm
from regent.config import settings


def _session():
    dbm.configure()
    return dbm.session()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "init":
        dbm.configure()
        dbm.init_db()
        print("schema ready on", dbm.engine().dialect.name)
    elif cmd == "seed":
        from regent.sim import scenario

        api = args[args.index("--api") + 1] if "--api" in args else settings.public_api_url
        dbm.configure()
        dbm.init_db(drop=True)
        s = dbm.session()
        print(json.dumps(scenario.seed(s, api), indent=2))
    elif cmd == "run":
        from regent.core.loop import RegentLoop

        s = _session()
        out = RegentLoop(s).run_all()
        for mid, reps in out.items():
            print(mid, " -> ".join(f"t{r.tick}:{r.status}" for r in reps))
    elif cmd == "status":
        from regent.api.views import cockpit, mission_list

        s = _session()
        mid = args[0] if args else next((m["id"] for m in mission_list(s) if not m["parent_id"]), None)
        if mid is None:
            print("no missions")
            return 1
        c = cockpit(s, mid)
        m, br = c["mission"], c["best_route"]
        print(f"{m['title']}  [{m['status']}]  tick {m['tick_count']}")
        for cr in m["criteria"]:
            print(f"  {'✓' if cr['met'] else '✗'} {cr['statement']}")
        if br:
            print(f"BEST  {br['title']}  score {br['score']:.3f}")
            for o in br["operations"]:
                print(f"   {o['status']:<14} {o['required_authority']:<8} {o['goal']}")
        for a in c["alternatives"]:
            print(f"ALT {a['rank']}  {a['score']:.3f}  {a['status']:<11} {a['title']}")
        for h in c["blocked_by_you"]:
            print(f"NEEDS YOU [{h['id']}] {h['required_action']} (~{h['estimated_time_seconds']}s)")
        for d in c["changes"][:5]:
            print(f"CHANGE {d['kind']}: {d['summary']}")
    elif cmd == "interrupts":
        from regent.core.human.interrupts import HumanInterruptManager

        for h in HumanInterruptManager(_session()).open():
            print(h.id, h.kind, h.required_action, json.dumps(h.resume_condition))
    elif cmd == "resolve":
        from regent.core.human.interrupts import HumanInterruptManager
        from regent.runtime import get_services

        s = _session()
        resp = {k: (v.lower() == "true" if v.lower() in ("true", "false") else v)
                for k, v in (a.split("=", 1) for a in args[1:])} or {"done": True}
        HumanInterruptManager(s, get_services()).resolve(args[0], resp)
        s.commit()
        print("resolved", args[0])
    elif cmd == "rebuild":
        from regent.core.world.projector import rebuild

        s = _session()
        print("replayed", rebuild(s), "events")
        s.commit()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
