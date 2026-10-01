"""Markdown report for scripts/app_benchmark.py (rendered from what Regent recorded)."""

from __future__ import annotations

import json
import sys
from typing import Any

MISSIONS = (("M1", "build"), ("M2", "use"), ("M2b", "use"), ("M3", "extend"), ("M4", "use v2"))


def _op(run: dict[str, Any], tool: str) -> dict[str, Any] | None:
    for o in run.get("operations") or []:
        if o["tool"] == tool and o["status"] in ("succeeded", "verified", "completed", "unverified", "failed"):
            return o
    return None


def _build_section(o: dict[str, Any]) -> list[str]:
    out = o.get("outputs") or {}
    lines = [f"- worker sessions: {out.get('rounds')} (initial + repairs); worker cost ${out.get('worker_cost_usd')}",
             f"- delivered: {json.dumps(out.get('inspect'))}",
             f"- accepted: {out.get('passed')}; requirement coverage {out.get('coverage')}; live at `{out.get('url')}`"]
    for i, fails in enumerate(out.get("failures_by_round") or []):
        if fails:
            lines.append(f"- round {i} failed Regent's checks ({len(fails)}):")
            lines += [f"    - {f}" for f in fails[:8]]
        else:
            lines.append(f"- round {i}: every check passed")
    promo = out.get("promote") or {}
    if promo:
        lines.append(f"- promotion: live={promo.get('live')} port={promo.get('port')} backup={promo.get('backup')} "
                     f"records carried over={promo.get('records_before')}")
    return lines


def _ops(run: dict[str, Any] | None, tool: str) -> list[dict[str, Any]]:
    return [o for o in (run or {}).get("operations") or [] if o["tool"] == tool]


def path_checks(out: dict[str, Any]) -> list[tuple[bool, str]]:
    """The bar: intent -> software is missing -> design -> delegate -> inspect -> test -> run -> use
    -> repair -> register -> reuse later (read from what Regent recorded, not from what it claimed)."""
    m1, m2, m3, m4 = (out.get(k) or {} for k in ("M1", "M2", "M3", "M4"))
    build = next((o["outputs"] for o in _ops(m1, "software.build_app") if o["status"] == "succeeded"), {}) or {}
    ext = next((o["outputs"] for o in _ops(m3, "software.extend_app") if o["status"] == "succeeded"), {}) or {}
    routes = {r["key"]: r for r in m1.get("routes") or []}
    alts = [r for k, r in routes.items() if k.startswith("software-existing-")]
    apps = ((out.get("K") or {}).get("after") or {}).get("applications") or []
    app = apps[0] if apps else {}
    ver = app.get("verification") or {}
    acc = ver.get("acceptance") or {}
    v2_design = next(((o.get("outputs") or {}).get("design") for o in _ops(m3, "software.design_app")
                      if o["status"] == "succeeded"), None) or m3.get("design") or {}
    regressions = sum(1 for s in v2_design.get("scenarios", []) if s.get("regression"))
    st = out.get("stranger") or {}
    uses = [o for k in ("M2", "M2b", "M4") for o in _ops(out.get(k), "software.use_app") if o["status"] == "succeeded"]
    return [
        (m1.get("selected") == "software-build-app" and bool(alts),
         f"software was found missing: {len(alts)} existing products competed and lost "
         f"({', '.join(r['title'] for r in alts)}), building was selected"),
        (bool(_ops(m1, "software.design_app")), "Regent designed the interface and its own acceptance scenarios before "
                                                "any code existed"),
        (bool(build.get("rounds")), f"construction was delegated to a coding worker: {(build.get('inspect') or {}).get('lines')} "
                                    f"lines in {(build.get('inspect') or {}).get('files')} files, worker cost "
                                    f"${build.get('worker_cost_usd')}"),
        (bool(build.get("rounds")) and len(build.get("failures_by_round") or []) > 1,
         f"Regent's checks failed the delivered software and it was repaired: {len(build.get('failures_by_round') or [])} "
         "rounds before acceptance"),
        (bool(build.get("passed")), "Regent inspected, ran the worker's tests, started it, and accepted it only after its own "
                                    "API, browser (phone-sized), restart and negative scenarios passed"),
        (bool(app.get("tool")), f"registered as a capability and a tool (`{app.get('tool')}`), live, the principal told "
                                "where it is and where the passphrase is kept"),
        (len(uses) >= 2, f"later missions used it through its API with read-back checks ({len(uses)} uses)"),
        (m3.get("selected", "").startswith("software-extend-") and bool(ext.get("passed")) and regressions > 0,
         f"a need it could not meet extended it: v{app.get('version')} built on a copy of the real data, held to "
         f"{regressions} regression scenarios "
         "of the version in use, promoted with a backup"),
        (not (ver.get("migration") or {}).get("lost") and (ver.get("migration") or {}).get("records_before") is not None,
         f"no record of the version in use was lost ({(ver.get('migration') or {}).get('records_before')} compared)"),
        (bool(st.get("shows_shared_note")) and not st.get("leaks_other_book"),
         "the shared link, opened by a stranger, shows that book's notes and nothing else"),
        ("restarted" in " ".join((out.get("K") or {}).get("maintenance") or []),
         "the live process was killed; Regent's maintenance pass brought it back on the same data"),
    ]


def render(out: dict[str, Any]) -> str:
    L = ["# Application capability benchmark (coding worker run for real)", "",
         f"Started {out.get('started')}; {out.get('seconds')} s wall clock. Workspace `{out.get('workspace')}`.", "",
         "Only sentences were given. Regent analysed each one, looked at the world, chose a route, and for the "
         "application routes delegated code to a file-only coding worker. Regent then built, tested, ran, "
         "browser-accepted, repaired, promoted and registered what came back, and later missions used that "
         "application. Every figure below was read back from Regent's own records ([`application-capability.json`](application-capability.json)); the code the worker delivered, with Regent's briefs and repair rounds, is in [`application-capability-app/`](application-capability-app).", ""]
    L += ["## Result: " + ("PASS" if all(ok for ok, _ in path_checks(out)) else "FAIL"), ""]
    L += [f"- [{'x' if ok else ' '}] {text}" for ok, text in path_checks(out)] + [""]
    total_h = 0.0
    total_cost = 0.0
    for key, label in MISSIONS:
        run = out.get(key)
        if not run:
            continue
        need = run.get("need") or {}
        L += [f"## {key} ({label}): “{run['sentence']}”", "",
              f"- status **{run.get('status')}** after {len(run.get('ticks') or [])} ticks, {run.get('seconds')} s",
              f"- need: {need.get('handled_as')} / {need.get('need_type')}; requirements: "
              + ", ".join(f"`{r['id']}`({r.get('priority')}{'' if r.get('stated', True) else ', implied'})"
                          for r in need.get("requirements") or [])]
        for x in need.get("reuse") or []:
            L.append(f"- existing capability judged **{x.get('relation')}**: {x['slug']} (gaps: "
                     f"{'; '.join(x.get('gaps') or []) or 'none'})")
        L += ["", "| rank | route | status | score |", "|---|---|---|---|"]
        for r in run.get("routes") or []:
            L.append(f"| {r.get('rank')} | {r['title']} | {r['status']}{' (' + r['invalid'] + ')' if r.get('invalid') else ''}"
                     f" | {r.get('score') if r.get('score') is None else round(r['score'], 3)} |")
        L += ["", "Operations: " + ", ".join(f"`{o['tool']}` {o['status']}" for o in run.get("operations") or []), ""]
        for tool in ("software.build_app", "software.extend_app"):
            o = _op(run, tool)
            if o:
                L += [f"**{tool}**", ""] + _build_section(o) + [""]
                total_cost += float((o.get("outputs") or {}).get("worker_cost_usd") or 0)
        o = _op(run, "software.use_app")
        if o:
            oo = o.get("outputs") or {}
            L += [f"**software.use_app**: {oo.get('calls')} API calls, {oo.get('verified_by_read_back')} read-back "
                  f"checks, passed={oo.get('passed')}" + (f"; error: {oo.get('error')}" if oo.get("error") else ""),
                  "", "```", *(oo.get("log") or []), "```", ""]
        for a in run.get("approvals") or []:
            L.append(f"- principal approved `{a['operation']}` (authorization interrupt: {a['action'][:120]})")
        for i in run.get("interrupts") or []:
            L.append(f"- interrupt [{i['kind']}, {i['status']}]: {i['action'][:160]}")
        ht = run.get("human_time") or {}
        total_h += float(ht.get("active_seconds_spent") or 0)
        L += [f"- principal's active time: {ht.get('active_seconds_spent')} s "
              f"(open requests: {ht.get('active_seconds_requested_open')} s)"]
        for n in run.get("notified") or []:
            L.append(f"- told the principal: {n.get('text')}")
        apps = (run.get("after") or {}).get("applications") or []
        for a in apps:
            recs = sum(len(v.get("books", v) if isinstance(v, dict) else v) if isinstance(v, (dict, list)) else 0
                       for v in (a.get("data") or {}).values())
            L.append(f"- afterwards: `{a['slug']}` v{a['version']} {a['status']}, coverage {a['coverage']}, "
                     f"{len(a['api'])} endpoints, uses {a['uses']}, data endpoints returned {recs} item(s)")
        L.append("")
    st = out.get("stranger")
    if st:
        L += ["## The shared link, opened by a stranger", "",
              f"- link: `{st.get('url')}`",
              f"- shows the shared notes (Butler): **{st.get('shows_shared_note')}**",
              f"- leaks the other book (Moby-Dick): **{st.get('leaks_other_book')}**", ""]
    k = out.get("K")
    if k:
        L += ["## Lifecycle: the live process is killed", "",
              f"- killed: {k.get('killed')}; maintenance pass: {k.get('maintenance')}",
              f"- afterwards running: " + ", ".join(f"{a['slug']} v{a['version']}" for a in
                                                     (k.get('after') or {}).get('applications') or []), ""]
    L += ["## Totals", "", f"- principal's active time across missions: {total_h:.0f} s (typing five sentences at "
          "40 wpm, plus two 20-second approvals of the coding agent)",
          f"- coding-worker spend: ${total_cost:.2f}", "",
          "## What this does not show", "",
          "- **Hosting.** \"Usable from any browser\" on the principal's phone needs public hosting: a domain, "
          "TLS and a host account. That is an identity and payment decision, and it was not taken. The application "
          "runs on this machine, and Regent told the principal so.",
          "- **One browser engine.** Browser acceptance ran in Chromium at a phone-sized viewport only. The "
          "requirement coverage figure counts the scenarios that ran; it does not count engines that were never "
          "tried.",
          "- **Edition choice.** For \"the Butler translation\", Regent chose the catalogue entry titled exactly "
          "\"The Odyssey\" and said it could not confirm the translator. It offered to swap in entry 1727, which "
          "is in fact Butler's. It was honest about the uncertainty, but it did not resolve it.",
          "- **Approvals are simulated.** The benchmark resolves the two authorization interrupts on the "
          "principal's behalf, following the instruction to run the coding worker for real. They are counted at 20 "
          "seconds each.",
          ""]
    return "\n".join(L)


if __name__ == "__main__":
    print(render(json.load(open(sys.argv[1]))))
