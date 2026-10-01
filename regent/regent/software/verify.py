"""Regent's acceptance suite for a capability.

Nothing is accepted on a worker's word: not the reasoning worker's reading of a field, not a
coding agent's "done", not the capability's own report. The suite

1. checks the spec is well-formed (every metric parses, references declared fields, has a
   definition and an honest form; the headline exists);
2. checks every core question is either answered or explicitly marked unknowable-yet;
3. reads every public source *through the capability*, then again *independently* (a separate
   plain HTTP read that shares no code with the connector) and compares the values;
4. checks honesty: a metric whose source is blocked shows no number (never 0), bounds are
   consistent, counts are non-negative, nothing identifying is stored;
5. checks constraints: sources are read-only and add nothing to the product;
6. exercises the interface: the registered tool's ``read`` returns what ``compute`` says;
7. renders the glance view in a real browser and compares every number a person would see
   with the computed values.

A capability is *usable* when every critical check passes.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.software import capability as K
from regent.software import connectors as C
from regent.software import expr as X
from regent.software.tables import SwCapability, SwCapabilityVersion, SwObservation

TRANSPORT: httpx.BaseTransport | None = None     # test hook for the independent read
BROWSER = True                                    # tests may disable the real browser check


def _check(name: str, passed: bool, detail: str = "", critical: bool = True, **data: Any) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "critical": critical, "detail": detail[:600], **data}


def run(db: Session, cap: SwCapability, *, services: Any = None, collect: bool = True) -> dict[str, Any]:
    spec = cap.spec or {}
    checks: list[dict[str, Any]] = []
    checks += _spec_checks(spec, cap.need or {})
    if collect:
        K.collect(db, cap)
    checks += _source_checks(db, cap)
    metrics = K.compute(db, cap)
    checks += _honesty_checks(db, cap, metrics)
    checks += _constraint_checks(spec)
    if services is not None:
        checks.append(_interface_check(db, services, cap, metrics))
    checks.append(_view_check(db, cap))
    cov, per = K.coverage(cap.need or {}, metrics)
    critical_failed = [c for c in checks if c["critical"] and not c["passed"]]
    res = {"passed": not critical_failed, "version": cap.version, "at": K.now().isoformat(timespec="seconds"),
           "coverage": cov, "coverage_by_question": per, "checks": checks,
           "summary": (f"{sum(c['passed'] for c in checks)}/{len(checks)} checks passed"
                       + (f"; failed: {', '.join(c['check'] for c in critical_failed)}" if critical_failed else ""))}
    cap.verification = res
    cap.coverage = cov
    v = db.scalar(select(SwCapabilityVersion).where(SwCapabilityVersion.capability_id == cap.id,
                                                    SwCapabilityVersion.version == cap.version))
    if v is not None:
        v.verification, v.status = res, "verified" if res["passed"] else "rejected"
    db.flush()
    return res


# ------------------------------------------------------------------ checks

def _spec_checks(spec: dict[str, Any], need: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    declared = {f"{s['id']}:{p}" for s in spec.get("sources", []) for p in
                ((s.get("params") or {}).get("fields") or list(((s.get("params") or {}).get("patterns") or {}))
                 or [f["path"] for f in s.get("fields", [])])}
    bad = []
    for m in spec.get("metrics", []):
        try:
            refs = X.references(m["expr"]) + (X.references(m["expr_hi"]) if m.get("expr_hi") else [])
        except X.ExprError as e:
            bad.append(f"{m['id']}: {e}")
            continue
        undeclared = [r for r in refs if r not in declared]
        if undeclared:
            bad.append(f"{m['id']} reads undeclared {undeclared}")
        if m.get("form") not in K.FORMS or not m.get("definition"):
            bad.append(f"{m['id']} lacks an honest form or a definition")
    out.append(_check("metrics are well-formed", not bad, "; ".join(bad) or f"{len(spec.get('metrics', []))} metrics"))
    out.append(_check("a headline exists", any(m.get("headline") for m in spec.get("metrics", [])) or
                      not spec.get("metrics"), critical=bool(spec.get("metrics"))))
    core = [q for q in need.get("questions", []) if q.get("priority", "core") == "core"] or need.get("questions", [])
    answered = {m.get("answers") for m in spec.get("metrics", [])} | {u["question"] for u in spec.get("unanswered", [])}
    missing = [q["id"] for q in core if q["id"] not in answered]
    out.append(_check("every core question is answered or marked unknowable-yet", not missing,
                      f"unaccounted: {missing}" if missing else f"{len(core)} core question(s) accounted for"))
    out.append(_check("the capability answers at least one question with a number",
                      any(m.get("answers") for m in spec.get("metrics", [])), critical=False))
    return out


def _latest(db: Session, cap: SwCapability, sid: str) -> SwObservation | None:
    return db.scalar(select(SwObservation).where(SwObservation.capability_id == cap.id, SwObservation.source_id == sid)
                     .order_by(SwObservation.observed_at.desc()).limit(1))


def _source_checks(db: Session, cap: SwCapability) -> list[dict[str, Any]]:
    out = []
    for s in (cap.spec or {}).get("sources", []):
        ob = _latest(db, cap, s["id"])
        if s.get("access") == "credential":
            ok = ob is not None and (ob.status == "ok" or (ob.status == "blocked" and (ob.blocker or {}).get("kind")
                                                           == "missing_credential"))
            out.append(_check(f"source {s['id']} is live or honestly blocked", ok,
                              f"{ob.status if ob else 'never read'}: {ob.error if ob and ob.error else ''}",
                              source=s["id"], status=ob.status if ob else None))
            continue
        if ob is None or ob.status != "ok":
            out.append(_check(f"source {s['id']} answers", False, ob.error if ob else "never read", source=s["id"]))
            continue
        fields = (s.get("params") or {}).get("fields") or list(((s.get("params") or {}).get("patterns") or {}).keys())
        missing = [f for f in fields if (ob.fields or {}).get(f) is None]
        out.append(_check(f"source {s['id']} answers with every declared field", not missing,
                          f"missing {missing}" if missing else f"{len(fields)} fields", source=s["id"]))
        if s["connector"] in ("http_json", "html_page"):
            out.append(_independent_read(s, ob))
        else:
            out.append(_rerun_agrees(s, ob))
    return out


def _rerun_agrees(s: dict[str, Any], ob: SwObservation) -> dict[str, Any]:
    """A reader Regent did not write must give the same answer when Regent runs it again."""
    try:
        again = C.collect(s["connector"], s.get("params") or {}).fields
    except Exception as e:
        return _check(f"re-running reader {s['id']} agrees", False, f"{type(e).__name__}: {e}", source=s["id"])
    diffs = [f for f in (ob.fields or {}) if isinstance(ob.fields[f], (int, float)) and isinstance(again.get(f), (int, float))
             and abs(ob.fields[f] - again[f]) > max(1.0, 0.05 * abs(again[f]))]
    diffs += [f for f in (ob.fields or {}) if f not in again]
    return _check(f"re-running reader {s['id']} agrees", not diffs, f"unstable: {diffs}" if diffs else "stable",
                  source=s["id"])


def _independent_read(s: dict[str, Any], ob: SwObservation) -> dict[str, Any]:
    """Regent re-reads the source itself with a plain HTTP client and compares."""
    url = (s.get("params") or {}).get("url")
    html_page = s["connector"] == "html_page"
    try:
        with httpx.Client(timeout=20, follow_redirects=True, transport=TRANSPORT,
                          headers={"User-Agent": C.USER_AGENT}) as c:
            resp = c.get(url)
            data = C.extract_patterns(C.page_text(resp.text), (s.get("params") or {}).get("patterns") or {}) \
                if html_page else resp.json()
    except Exception as e:
        return _check(f"independent read of {s['id']} agrees", False, f"{type(e).__name__}: {e}", source=s["id"])
    diffs = []
    names = list(((s.get("params") or {}).get("patterns") or {}).keys()) if html_page else \
        (s.get("params") or {}).get("fields") or []
    for f in names:
        a, b = (ob.fields or {}).get(f), (data.get(f) if html_page else C.json_path(data, f))
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(a - b) > max(1.0, 0.05 * abs(b)):
                diffs.append(f"{f}: capability {a} vs direct {b}")
        elif isinstance(a, list) and isinstance(b, list):
            if a and b and json.dumps(a[0], sort_keys=True) != json.dumps(b[0], sort_keys=True) and len(a) == len(b):
                diffs.append(f"{f}: first item differs")
        elif isinstance(a, str) and isinstance(b, str) and _close_times(a, b):
            continue                       # a timestamp that moved between two reads moments apart
        elif a != b and not (a is None and b is None):
            diffs.append(f"{f}: {str(a)[:40]} vs {str(b)[:40]}")
    return _check(f"independent read of {s['id']} agrees", not diffs,
                  "; ".join(diffs) or "every field matches a direct read", source=s["id"])


def _close_times(a: str, b: str, tolerance_s: float = 3600) -> bool:
    from datetime import datetime

    try:
        ta = datetime.fromisoformat(a.replace("Z", "+00:00"))
        tb = datetime.fromisoformat(b.replace("Z", "+00:00"))
    except ValueError:
        return False
    return abs((ta - tb).total_seconds()) <= tolerance_s


IDENTIFYING = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\b(?:\d{1,3}\.){3}\d{1,3}\b|\bcus_[A-Za-z0-9]{8,}")


def _honesty_checks(db: Session, cap: SwCapability, metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    srcs = K.source_status(db, cap)
    lied = [m["id"] for m in metrics if m.get("value") is not None and any(
        srcs.get(r.split(":", 1)[0], {}).get("status") == "blocked" for r in m.get("refs", []))]
    out.append(_check("no number is shown for a blocked source", not lied, f"shown anyway: {lied}" if lied else
                      f"{sum(1 for m in metrics if m.get('status') == 'blocked')} metric(s) honestly blocked"))
    neg = [m["id"] for m in metrics if isinstance(m.get("value"), (int, float)) and m["value"] < 0
           and m.get("unit") in ("people", "events")]
    out.append(_check("counts are non-negative", not neg, f"negative: {neg}" if neg else ""))
    by_q: dict[str, dict[str, list[float]]] = {}
    for m in metrics:
        if isinstance(m.get("value"), (int, float)) and m.get("answers") and m.get("unit") == "people":
            by_q.setdefault(m["answers"], {}).setdefault(m.get("form"), []).append(m["value"])
    incons = [q for q, f in by_q.items() if f.get("lower_bound") and f.get("upper_bound")
              and max(f["lower_bound"]) > min(f["upper_bound"])]
    out.append(_check("lower bounds do not exceed upper bounds", not incons, f"inconsistent: {incons}" if incons else ""))
    leaked = []
    for ob in db.scalars(select(SwObservation).where(SwObservation.capability_id == cap.id,
                                                     SwObservation.status == "ok")):
        if IDENTIFYING.search(json.dumps(ob.fields or {}, default=str)):
            leaked.append(ob.source_id)
    out.append(_check("stored observations contain no identifying data", not leaked,
                      f"identifying values in {sorted(set(leaked))}" if leaked else "aggregates only"))
    return out


def _constraint_checks(spec: dict[str, Any]) -> list[dict[str, Any]]:
    bad = []
    for s in spec.get("sources", []):
        conn = C.CONNECTORS.get(s["connector"])
        if conn is None:
            bad.append(f"{s['id']}: unknown connector {s['connector']}")
        elif conn.adds_client_code or conn.collect is None:
            bad.append(f"{s['id']}: {conn.footprint}")
    return [_check("sources are read-only and add nothing to the product", not bad, "; ".join(bad) or
                   "every source is a read of something that already exists")]


def _interface_check(db: Session, services: Any, cap: SwCapability, metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Call the capability through the tool registry, exactly as a route or a later mission will."""
    from regent.tools.base import ToolContext

    K.register_tool(services, cap)
    db.commit()                      # the tool reads the capability in its own session
    tool = services.tools.get(cap.tool_name)
    res = tool.invoke("read", {"for_verification": True},
                      ToolContext(mission_id=cap.mission_id or "", operation_id="verify", workspace=None))
    if res.status != "ok":
        return _check("the registered tool returns the computed answer", False, res.error or "")
    got = res.outputs.get("metrics", {})
    diffs = [m["id"] for m in metrics if (got.get(m["id"]) or {}).get("value") != m.get("value")]
    return _check("the registered tool returns the computed answer", not diffs,
                  f"differs: {diffs}" if diffs else f"{len(got)} metrics via {cap.tool_name}.read")


def _view_check(db: Session, cap: SwCapability) -> dict[str, Any]:
    r = K.read(db, cap, count_use=False)
    page = K.render_html(r)
    expected = {m["id"]: m["display"] for m in r["metrics"]}
    if not BROWSER:
        seen = dict(re.findall(r'data-metric="([^"]+)".*?<div class="value"[^>]*>([^<]*)</div>', page, re.S))
        how = "parsed HTML (browser check disabled)"
    else:
        try:
            seen = _browser_read(page)
            how = "rendered in headless Chromium"
        except Exception as e:
            return _check("the glance view shows exactly the computed numbers", False,
                          f"browser failed: {type(e).__name__}: {str(e)[:200]}", critical=False)
    seen = {k: v.replace(" ", " ").strip() for k, v in seen.items()}
    diffs = [f"{k}: shows {seen.get(k)!r}, computed {v!r}" for k, v in expected.items() if seen.get(k) != v]
    return _check("the glance view shows exactly the computed numbers", not diffs,
                  "; ".join(diffs) or f"{len(expected)} numbers match ({how})", rendered_with=how)


def _browser_read(page: str) -> dict[str, str]:
    from playwright.sync_api import sync_playwright

    from regent.browser.driver import _chromium_executable

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "view.html"
        p.write_text(page)
        with sync_playwright() as pw:
            try:
                b = pw.chromium.launch(headless=True)
            except Exception:
                b = pw.chromium.launch(headless=True, executable_path=_chromium_executable())
            try:
                pg = b.new_page()
                pg.goto(p.as_uri())
                # a person can unfold every section: read what they would see then
                pg.eval_on_selector_all("details", "els => els.forEach(d => d.open = true)")
                vals = pg.eval_on_selector_all(
                    "section[data-metric]", "els => els.map(e => [e.dataset.metric, "
                    "e.querySelector('.value').innerText])")
            finally:
                b.close()
    return {k: v for k, v in vals}
