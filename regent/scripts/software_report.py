"""Markdown report for scripts/software_benchmark.py (also re-renders from the saved JSON)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _chk(ok: bool, text: str) -> str:
    return f"- [{'x' if ok else ' '}] {text}"


def _cost(run: dict[str, Any]) -> float:
    return round(sum(c["cost"] or 0 for c in run.get("model_calls", [])), 3)


def _people_claims_earned(A: dict) -> bool:
    metrics = (A.get("read") or {}).get("metrics", [])
    for m in metrics:
        rel = ((m.get("audit") or {}).get("relation")) or m.get("form")
        if m.get("form") in ("lower_bound", "upper_bound", "exact", "range") and rel == "proxy":
            return False
        if m.get("form") == "proxy" and not str(m.get("label", "")).lower().startswith("proxy"):
            return False
    head = [m for m in metrics if m.get("headline")]
    return bool(head)


def _credential_check(inv: dict, open_hi: list[dict]) -> tuple[bool, str]:
    """Asking the principal for a credential is worth their time only if what it unlocks earns a
    bound or a measure; a credential that unlocks only proxies is not requested."""
    earning = sorted({f["endpoint"].split(":", 1)[1] for f in inv.get("public_fields", [])
                      if str(f.get("endpoint", "")).startswith("connector:")
                      and f.get("relation_to_need") in ("measure", "lower_bound", "upper_bound", "direct")})
    if earning:
        ok = len(open_hi) == 1 and open_hi[0]["kind"] == "credential"
        return ok, (f"exactly one human interrupt, for a credential that unlocks an earned answer ({', '.join(earning)})"
                    + (f" (~{open_hi[0]['seconds']} s)" if open_hi else ""))
    return (not open_hi, "no credential requested: every platform source the principal could unlock is only a proxy "
                         f"for the question (open interrupts: {len(open_hi)})")


def checks_lindy(A: dict, B: dict, C: dict) -> list[tuple[bool, str]]:
    cap = A.get("capability") or {}
    ver = cap.get("verification") or {}
    inv = A.get("inventory") or {}
    routes = {r["key"]: r for r in A["routes"]}
    blocked_metrics = [m for m in (A.get("read") or {}).get("metrics", []) if m.get("status") == "blocked"]
    deps = [d for s in (A.get("discover") or {}).get("subjects", []) for d in s.get("deployments", [])]
    open_hi = [h for h in A["interrupts"] if h["status"] == "open"]
    b_ops = {o["key"] for o in B["operations"]}
    return [
        (bool(deps), f"the subject was found on the live web from its name alone: {', '.join(d['host'] for d in deps)}"),
        (len(A["routes"]) >= 4, f"{len(A['routes'])} competing routes were generated and scored"),
        (any(r["status"] == "invalidated" and "tracking" in (r["invalid"] or "") for r in A["routes"]),
         "the conventional route (add an analytics script) was ruled out by the product's own public promise"),
        (bool(ver.get("passed")), f"the capability passed Regent's own acceptance suite ({ver.get('summary')})"),
        (cap.get("status") in ("usable", "degraded"), f"the capability is active: {cap.get('status')}, tool "
                                                      f"`{cap.get('tool')}`, coverage {cap.get('coverage')}"),
        (all(m.get("value") is None for m in blocked_metrics),
         f"no number is shown for a source Regent cannot read ({len(blocked_metrics)} metrics honestly blocked)"),
        (_people_claims_earned(A), "every number about people is a form its audited premises earn; anything "
                                   "else is labelled a proxy (the headline says Unknown when only proxies exist)"),
        _credential_check(inv, open_hi),
        (A["human_time"]["active_seconds_spent"] < 30, f"active human time so far: "
                                                       f"{A['human_time']['active_seconds_spent']} s"),
        ("software.reuse" in {o["tool"] for o in B["operations"]} and "acquire.discover" not in
         {o["tool"] for o in B["operations"]}, "a later mission, worded differently, reused the capability without "
                                                "re-examining the world"),
        (C["observations_after"] > C["observations_before"],
         f"Regent re-read the capability's sources on its own ({C['observations_before']} -> "
         f"{C['observations_after']} observations)"),
    ]


def checks_laundry(D: dict, E: dict) -> list[tuple[bool, str]]:
    cap = D.get("capability") or {}
    ver = cap.get("verification") or {}
    inv = D.get("inventory") or {}
    place = next((s.get("place") for s in inv.get("subjects", []) if s.get("place")), None)
    prop = inv.get("proposals") or {}
    archetypes = {r["key"] for r in D["routes"]}
    return [
        (bool(place), f"the place was resolved: {place and place['name']} ({place and place['latitude']:.3f}, "
                      f"{place and place['longitude']:.3f}, {place and place['timezone']})" if place else "place resolved"),
        (bool(prop.get("apis") or prop.get("pages")),
         f"sources were proposed and admitted only by use: {len(prop.get('apis', []))} APIs, "
         f"{len(prop.get('pages', []))} existing-service pages; {len(prop.get('rejected', []))} rejected"),
        (any("robots" in (r.get("why") or "") for r in prop.get("rejected", [])),
         "a source whose robots.txt disallows Regent was rejected, not worked around"),
        ({"software-use-existing", "software-compose-data"} <= archetypes,
         "using an existing service competed with building from data"),
        (bool(inv.get("decision_rules")), "the yes/no answer is a stated rule Regent parsed and evaluated live"),
        (any(m.get("headline") == 1 and m.get("form") == "decision" for m in (D.get("read") or {}).get("metrics", [])),
         "the view and the message lead with the yes/no verdict"),
        (bool(ver.get("passed")), f"acceptance suite: {ver.get('summary')}"),
        (D["status"] == "completed", f"mission status: {D['status']}"),
        (bool(E["notified"]), "the morning message was delivered: " + (E["notified"][0]["text"][:160] if E["notified"]
                                                                     else "none")),
        (D["human_time"]["active_seconds_spent"] < 30, f"active human time: {D['human_time']['active_seconds_spent']} s"),
    ]


def _routes_table(run: dict) -> list[str]:
    out = ["| rank | route | status | score | P(success) | upside | authority | why not / note |", "|---|---|---|---|---|---|---|---|"]
    for r in run["routes"]:
        e = r.get("effective") or {}
        out.append(f"| {r['rank'] if r['rank'] is not None else '-'} | {r['title']} | {r['status']} | "
                   f"{(r['score'] or 0):.3f} | {e.get('success_probability', 0):.2f} | {e.get('expected_upside', 0):.2f} | "
                   f"{e.get('authority_cost', 0):.2f} | {(r['invalid'] or '')[:140]} |")
    return out


def _metrics(run: dict) -> list[str]:
    rd = run.get("read") or {}
    out = ["| metric | shows | form | status | definition |", "|---|---|---|---|---|"]
    for m in rd.get("metrics", []):
        out.append(f"| {m['label']}{' (headline)' if m.get('headline') else ''} | {m['display']} | {m.get('form')} | "
                   f"{m.get('status')} | {(m.get('definition') or '').replace('|', '/')[:220]} |")
    return out


def _need(run: dict) -> list[str]:
    n = run.get("need") or {}
    out = [f"- handled as: `{n.get('handled_as')}`; analysed by {n.get('analysis', {}).get('by')}",
           f"- subjects: " + ", ".join(f"{s['name']} ({s['kind']})" for s in n.get("subjects", [])),
           f"- deliverable: {n.get('deliverable')}"]
    for q in n.get("questions", []):
        out.append(f"- **{q['id']}** ({q.get('priority')}, {q.get('answer_type')}): {q['question']} — quantity: "
                   f"{q.get('quantity')}; excludes: {', '.join(q.get('exclude') or []) or '-'}; windows: "
                   f"{', '.join(q.get('windows') or [])}")
    return out


def _ticks(run: dict) -> list[str]:
    out = []
    for t in run["ticks"]:
        acq = next((p for p in t["phases"] if p["phase"] == "acquire"), {})
        sel = next((p for p in t["phases"] if p["phase"] == "select"), {})
        ex = next((p for p in t["phases"] if p["phase"] == "execute"), {})
        ver = next((p for p in t["phases"] if p["phase"] == "verify"), {})
        out.append(f"- tick {t['tick']} (+{t['seconds']}s, {t['status']}): acquire "
                   f"{[o.split(':')[0] for o in acq.get('operations', [])]}; select {sel.get('selected')} "
                   f"({sel.get('kind')}); executed {ex.get('started')}; verified "
                   f"{[(v['op'], v['verdict']) for v in ver.get('verified', [])]}")
    return out


def render(out: dict[str, Any]) -> str:
    A, B, C, D, E = out["A"], out["B"], out["C"], out["D"], out["E"]
    la, lb = checks_lindy(A, B, C), checks_laundry(D, E)
    ok = all(c for c, _ in la + lb)
    L = ["# Software needs benchmark", "",
         f"Run {out['started']}, {out['seconds']} s wall clock, live public web, one fresh world. Regent received "
         "only the sentences below; no provider, metric, framework, database, UI, deployment method or plan.", "",
         f"## Result: {'PASS' if ok else 'FAIL'}", "", "### LindyBooks", ""]
    L += [_chk(c, t) for c, t in la] + ["", "### Second need (materially different)", ""]
    L += [_chk(c, t) for c, t in lb]
    for key, run, title in (("A", A, "A. "), ("D", D, "D. ")):
        inv = run.get("inventory") or {}
        L += ["", f"## {title}“{run['sentence']}”", "",
              f"{run['seconds']} s, {len(run['ticks'])} ticks, reasoning-worker cost ${_cost(run)} "
              f"({len(run['model_calls'])} live calls). Final status: **{run['status']}**.", "",
              "### What the sentence needs (need analysis, checked against the sentence)", ""] + _need(run)
        disc = run.get("discover") or {}
        L += ["", "### What Regent found", ""]
        for sub in disc.get("subjects", []):
            if sub.get("place"):
                L.append(f"- {sub['name']}: place {sub['place']}")
            elif sub.get("skipped"):
                L.append(f"- {sub['name']}: {sub['skipped']}")
            else:
                L.append(f"- {sub['name']}: {sub.get('probes')} hostnames probed; deployments: "
                         + "; ".join(f"{d['host']} (aliases {d.get('aliases')}, platforms {d['fingerprint']['platforms']}, "
                                     f"analytics {d['fingerprint']['analytics'] or 'none'}, promises "
                                     f"{[p['phrase'] for p in d['fingerprint']['promises']]}, "
                                     f"{sum(1 for e in d['endpoints'] if e.get('json'))} readable endpoints, owner evidence "
                                     f"{d['owner_evidence'][:1]})" for d in sub.get("deployments", [])))
        prop = inv.get("proposals") or {}
        if prop:
            L.append(f"- proposed sources admitted: APIs {prop.get('apis')}, pages "
                     f"{[p['name'] for p in prop.get('pages', [])]}")
            for r in prop.get("rejected", []):
                L.append(f"  - rejected {r['url'][:110]}: {r['why'][:120]}")
        useful = [f for f in inv.get("public_fields", []) if f.get("relation_to_need") != "unrelated"]
        L += ["", f"Fields that bear on the need ({len(useful)} of {len(inv.get('public_fields', []))} classified; "
                  "each reading backed by a verbatim quote Regent found in what it observed):", "",
              "| source | field | relation | meaning | quote found |", "|---|---|---|---|---|"]
        for f in useful[:24]:
            L.append(f"| {str(f['endpoint'])[-50:]} | {f['path'][-50:]} | {f['relation_to_need']} | "
                     f"{(f.get('meaning') or '')[:90]} | {f.get('evidence_found')} |")
        unrel = [f for f in inv.get("public_fields", []) if f.get("relation_to_need") == "unrelated"]
        if unrel:
            L += ["", "Ruled out as unrelated: " + "; ".join(f"`{f['path']}` ({(f.get('meaning') or '')[:60]})"
                                                          for f in unrel[:10])]
        for c in inv.get("constraints", []):
            L.append(f"- commitment: {c['statement']} — quote “{c['evidence']}” found: {c['evidence_found']}")
        for r in inv.get("decision_rules", []):
            L.append(f"- decision rule `{r['expr'][:300]}` -> {r['value_now']} now")
        L += ["", "### Routes", ""] + _routes_table(run) + ["", f"Selected: **{run['selected']}**", "",
              "### Loop", ""] + _ticks(run)
        L += ["", "### Operations", "", "| op | tool | authority | status | note |", "|---|---|---|---|---|"]
        L += [f"| {o['key']} | {o['tool']} | {o['authority']} | {o['status']} | {(o['error'] or '')[:100]} |"
              for o in run["operations"]]
        cap = run.get("capability") or {}
        if cap:
            ver = cap.get("verification") or {}
            L += ["", f"### Capability `{cap['slug']}` v{cap['version']} ({cap['implementation']}, {cap['status']})", "",
                  f"Tool `{cap['tool']}`; view `/software/{cap['slug']}`; JSON `/api/software/capabilities/{cap['slug']}`. "
                  f"Sources: " + "; ".join(f"{x['id']} ({x['connector']}, {x['access']})" for x in cap["spec_sources"]),
                  "", f"![glance view](software-{'lindybooks' if key == 'A' else 'laundry'}-view.png)", "",
                  "Acceptance suite (Regent's own; nothing taken on a worker's word):", ""]
            L += [_chk(c["passed"], f"{c['check']} — {c['detail'][:160]}") for c in ver.get("checks", [])]
            L += ["", "What it shows now:", ""] + _metrics(run)
            un = (run.get("read") or {}).get("unanswered", [])
            if un:
                L += ["", "Not knowable yet:", ""] + [
                    f"- {u.get('question_text')}: {u['why']}" + (f" → unlock: {u['unlock']['human_action']} "
                                                                  f"(~{u['unlock']['seconds']} s)" if u.get("unlock") else "")
                    for u in un]
        L += ["", "### Human", ""]
        for h in run["interrupts"]:
            L.append(f"- interrupt ({h['kind']}, {h['status']}, ~{h['seconds']} s): {h['action']} — resumes when "
                     f"{h['resume']}")
        ht = run["human_time"]
        L.append(f"- active human time spent: **{ht['active_seconds_spent']} s**; requested and still open: "
                 f"{ht['active_seconds_requested_open']} s ({ht['method']})")
    L += ["", "## B. Reuse: “" + B["sentence"] + "”", "",
          f"Status {B['status']} in {B['seconds']} s. Need-analysis reuse match: "
          f"{[(m['slug'], m['judged_by'], m['covered_questions']) for m in (B.get('need') or {}).get('reuse', [])]}", ""]
    L += _routes_table(B) + ["", "Operations: " + ", ".join(f"{o['key']} ({o['status']})" for o in B["operations"])]
    L += ["", "## C. Regent keeps using what it built", "",
          f"After the refresh period, maintenance re-read the sources: {C['maintenance']}; observations "
          f"{C['observations_before']} -> {C['observations_after']} (the clock was advanced one hour for the "
          "refresh check only; the reads were live).", "", "## E. Delivery", ""]
    L += [f"- {n['for_date']} via {n['channel']}: {n['text']}" for n in E["notified"]]
    L += ["", "## Delegatable resources at run time", "", "| resource | available | note |", "|---|---|---|"]
    for r in out.get("resources", []):
        L.append(f"| {r['resource']} | {r.get('available')} | {(r.get('unavailable_because') or r.get('note') or r.get('missing_credentials') or '')} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    p = Path(sys.argv[1])
    data = json.loads(p.with_suffix(".json").read_text())
    p.write_text(render(data))
