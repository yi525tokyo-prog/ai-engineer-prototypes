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


def render(out: dict[str, Any]) -> str:
    L = ["# Application capability benchmark (coding worker run for real)", "",
         f"Started {out.get('started')}; {out.get('seconds')} s wall clock. Workspace `{out.get('workspace')}`.", "",
         "Only sentences were given. Regent analysed each one, looked at the world, chose a route, and for the "
         "application routes delegated code to a file-only coding worker. Regent then built, tested, ran, "
         "browser-accepted, repaired, promoted and registered what came back, and later missions used that "
         "application. Every figure below was read back from Regent's own records (`app-benchmark.json`).", ""]
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
    L += ["## Totals", "", f"- principal's active time across missions: {total_h:.0f} s",
          f"- coding-worker spend: ${total_cost:.2f}", ""]
    return "\n".join(L)


if __name__ == "__main__":
    print(render(json.load(open(sys.argv[1]))))
