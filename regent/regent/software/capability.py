"""Software capabilities: things Regent builds once and then keeps using.

A capability is not a script. It is a versioned, verified unit of software with

* a **need** it answers (the analyzed intent and its need signatures, so a later mission
  asking the same thing finds it instead of rebuilding);
* a **spec**: sources (connector + parameters + what each field means), metrics (safe
  expressions over observed series, each with a definition, an honest form -- exact, lower
  bound, activity signal... -- and caveats), questions it cannot answer yet and what would
  unlock them, refresh period and constraints;
* an **implementation**: ``composed`` (Regent's vetted runtime executes the spec),
  ``external`` (an existing service answers the need; Regent reads it), or ``delegated``
  (code a worker wrote in a workspace, run behind the same interface);
* an **interface**: tool actions ``read`` / ``collect`` registered in Regent's tool registry, a
  JSON API, a glance view (HTML) and world facts ``software.<slug>.<metric>``;
* a **verification record** produced by Regent's own acceptance suite (``verify.py``); a
  capability is *usable* only when that suite passes -- never because a worker said so.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.ids import new_id, utcnow
from regent.software import connectors as C
from regent.software import expr as X
from regent.software.tables import SwCapability, SwCapabilityVersion, SwObservation

CLOCK: Any = None            # test hook
FORMS = ("exact", "estimate", "range", "lower_bound", "upper_bound", "activity")
FORM_WEIGHT = {"exact": 1.0, "estimate": 0.9, "range": 0.8, "upper_bound": 0.4, "lower_bound": 0.5, "activity": 0.25}


def now() -> datetime:
    return CLOCK() if CLOCK else utcnow()


def _aware(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def slugify(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:60] or "capability"


# ----------------------------------------------------------------- registry

def find(db: Session, signature: list[str], *, usable_only: bool = True) -> list[SwCapability]:
    want = set(signature)
    out = []
    for c in db.scalars(select(SwCapability).where(SwCapability.status != "retired")):
        if usable_only and c.status not in ("usable", "degraded"):
            continue
        if want & set(c.signature or []):
            out.append(c)
    return out


def get(db: Session, ref: str) -> SwCapability | None:
    c = db.get(SwCapability, ref)
    return c or db.scalar(select(SwCapability).where(SwCapability.slug == ref))


def save_version(db: Session, *, mission_id: str | None, need: dict[str, Any], signature: list[str],
                 spec: dict[str, Any], implementation: str, provenance: dict[str, Any], reason: str,
                 capability_id: str | None = None) -> SwCapability:
    cap = db.get(SwCapability, capability_id) if capability_id else None
    if cap is None:
        base = slugify(spec.get("slug") or spec.get("title") or "capability")
        slug, i = base, 2
        while db.scalar(select(SwCapability.id).where(SwCapability.slug == slug)):
            slug, i = f"{base}-{i}", i + 1
        cap = SwCapability(id=new_id("cap"), slug=slug, mission_id=mission_id, version=0, spec={}, verification={},
                           provenance={}, signature=[], need={})
        db.add(cap)
    cap.version = (cap.version or 0) + 1
    cap.title = spec.get("title") or cap.title
    cap.need, cap.signature, cap.spec = need, sorted(set(signature)), spec
    cap.implementation, cap.status = implementation, "built"
    cap.provenance = {**(cap.provenance or {}), f"v{cap.version}": provenance}
    cap.tool_name = f"cap_{cap.slug.replace('-', '_')}"[:80]
    cap.updated_at = now()
    db.add(SwCapabilityVersion(id=new_id("capv"), capability_id=cap.id, version=cap.version, spec=spec,
                               verification={}, status="built", reason=reason))
    db.flush()
    return cap


# ------------------------------------------------------------------ runtime

def collect(db: Session, cap: SwCapability, *, sources: list[str] | None = None) -> list[SwObservation]:
    """Read every source of the capability once (credential sources report blocked, never fake)."""
    out = []
    for s in (cap.spec or {}).get("sources", []):
        if sources and s["id"] not in sources:
            continue
        t = now()
        try:
            rd = C.collect(s["connector"], s.get("params") or {})
            ob = SwObservation(id=new_id("obs"), capability_id=cap.id, source_id=s["id"], status="ok",
                               fields=rd.fields, url=rd.url, excerpt=rd.raw_excerpt[:1500], observed_at=t)
        except C.ConnectorBlocked as e:
            ob = SwObservation(id=new_id("obs"), capability_id=cap.id, source_id=s["id"], status="blocked",
                               fields={}, url=(s.get("params") or {}).get("url", ""), error=str(e),
                               blocker={"kind": e.kind, "detail": e.detail, "credential": e.credential},
                               observed_at=t)
        except Exception as e:           # a source failing must not take the capability down
            ob = SwObservation(id=new_id("obs"), capability_id=cap.id, source_id=s["id"], status="error",
                               fields={}, url=(s.get("params") or {}).get("url", ""),
                               error=f"{type(e).__name__}: {e}"[:400], observed_at=t)
        db.add(ob)
        out.append(ob)
    cap.last_collect_at = now()
    db.flush()
    return out


def _series_fn(db: Session, cap: SwCapability):
    cache: dict[str, list[SwObservation]] = {}

    def series(ref: str) -> X.Series:
        src, fld = ref.split(":", 1)
        if src not in cache:
            cache[src] = list(db.scalars(select(SwObservation).where(
                SwObservation.capability_id == cap.id, SwObservation.source_id == src,
                SwObservation.status == "ok").order_by(SwObservation.observed_at)))
        return [(_aware(o.observed_at), (o.fields or {}).get(fld)) for o in cache[src]
                if (o.fields or {}).get(fld) is not None]
    return series


def source_status(db: Session, cap: SwCapability) -> dict[str, dict[str, Any]]:
    out = {}
    for s in (cap.spec or {}).get("sources", []):
        last = db.scalar(select(SwObservation).where(SwObservation.capability_id == cap.id,
                                                     SwObservation.source_id == s["id"])
                         .order_by(SwObservation.observed_at.desc()).limit(1))
        last_ok = db.scalar(select(SwObservation.observed_at).where(
            SwObservation.capability_id == cap.id, SwObservation.source_id == s["id"], SwObservation.status == "ok")
            .order_by(SwObservation.observed_at.desc()).limit(1))
        conn = C.CONNECTORS.get(s["connector"])
        out[s["id"]] = {"id": s["id"], "connector": s["connector"], "title": s.get("title") or (conn.title if conn else ""),
                        "status": last.status if last else "never_read", "last_ok": _iso(last_ok),
                        "last_error": last.error if last and last.status != "ok" else None,
                        "blocker": last.blocker if last and last.status == "blocked" else None,
                        "url": (s.get("params") or {}).get("url") or (last.url if last else ""),
                        "observations": db.query(SwObservation).filter(SwObservation.capability_id == cap.id,
                                                                       SwObservation.source_id == s["id"],
                                                                       SwObservation.status == "ok").count()}
    return out


def _iso(d: datetime | None) -> str | None:
    d = _aware(d)
    return d.isoformat(timespec="seconds") if d else None


def compute(db: Session, cap: SwCapability) -> list[dict[str, Any]]:
    series = _series_fn(db, cap)
    srcs = source_status(db, cap)
    out = []
    for m in (cap.spec or {}).get("metrics", []):
        ctx = X.EvalContext(series=series, now=now())
        res: dict[str, Any] = {k: m.get(k) for k in ("id", "label", "unit", "form", "answers", "definition",
                                                      "caveats", "window", "headline")}
        try:
            v = X.evaluate(m["expr"], ctx)
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            if m.get("expr_hi"):
                hi = X.evaluate(m["expr_hi"], ctx)
                if isinstance(hi, float) and hi.is_integer():
                    hi = int(hi)
                res["value_hi"] = hi
                if hi is None:
                    v = None            # a range needs both ends
            res["value"] = v
            needed = {r.split(":", 1)[0] for r in ctx.refs}
            blocked = [s for s in needed if srcs.get(s, {}).get("status") == "blocked"]
            if v is None:
                res["status"] = "blocked" if blocked else "no_data"
                res["why"] = (f"source {', '.join(blocked)} is blocked: "
                              + "; ".join(str((srcs[b].get("blocker") or {}).get("detail")) for b in blocked)) \
                    if blocked else "no observation yet"
            else:
                res["status"] = "partial" if ctx.partial else "ok"
            res["notes"] = ctx.notes
            res["refs"] = sorted(ctx.refs)
        except (X.ExprError, TypeError, ValueError, KeyError) as e:
            res.update(value=None, status="error", why=f"{type(e).__name__}: {e}"[:200], notes=[])
        res["display"] = display(res)
        out.append(res)
    return out


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        v = round(v, 1) if abs(v) < 100 else round(v)
    return f"{v:,}" if isinstance(v, (int, float)) else str(v)


def display(m: dict[str, Any]) -> str:
    v = m.get("value")
    if v is None:
        return "—"
    s = _fmt(v)
    form = m.get("form")
    if form == "range" and m.get("value_hi") is not None:
        return f"{s} – {_fmt(m['value_hi'])}"
    if form == "lower_bound":
        return f"≥ {s}"
    if form == "upper_bound":
        return f"≤ {s}"
    return s


def coverage(need: dict[str, Any], metrics: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    """How much of the need the capability answers *now*: per core question, the best honest
    form it has a live value for (an exact count counts fully, a lower bound half...)."""
    core = [q for q in need.get("questions", []) if q.get("priority", "core") == "core"] or need.get("questions", [])
    if not core:
        return 0.0, {}
    per = {}
    for q in core:
        best = 0.0
        forms = set()
        for m in metrics:
            if m.get("answers") == q["id"] and m.get("status") in ("ok", "partial") and m.get("value") is not None:
                w = FORM_WEIGHT.get(m.get("form") or "activity", 0.25)
                best = max(best, w * (0.85 if m.get("status") == "partial" else 1.0))
                forms.add(m.get("form"))
        if {"lower_bound", "upper_bound"} <= forms:        # both ends known: the answer is a range
            best = max(best, FORM_WEIGHT["range"])
        per[q["id"]] = round(best, 3)
    return round(sum(per.values()) / len(per), 3), per


def read(db: Session, cap: SwCapability, *, count_use: bool = True) -> dict[str, Any]:
    metrics = compute(db, cap)
    cov, per = coverage(cap.need or {}, metrics)
    if count_use:
        cap.uses = (cap.uses or 0) + 1
    srcs = source_status(db, cap)
    last = max((s["last_ok"] for s in srcs.values() if s["last_ok"]), default=None)
    return {
        "capability": {"id": cap.id, "slug": cap.slug, "title": cap.title, "version": cap.version,
                       "status": cap.status, "implementation": cap.implementation, "tool": cap.tool_name},
        "need": {"sentence": (cap.need or {}).get("sentence"), "definitions": (cap.spec or {}).get("definitions", []),
                 "questions": [{"id": q["id"], "question": q["question"]} for q in (cap.need or {}).get("questions", [])]},
        "as_of": last, "generated_at": _iso(now()), "coverage": cov, "coverage_by_question": per,
        "metrics": metrics, "unanswered": (cap.spec or {}).get("unanswered", []), "sources": list(srcs.values()),
        "verification": {k: (cap.verification or {}).get(k) for k in ("passed", "at", "summary", "version")},
    }


def facts(cap: SwCapability, r: dict[str, Any]) -> list[dict[str, Any]]:
    base = f"software.{cap.slug}"
    fs = [{"key": f"{base}.status", "value": cap.status, "confidence": 1.0, "source": f"capability:{cap.slug}"},
          {"key": f"{base}.coverage", "value": r["coverage"], "confidence": 1.0, "source": f"capability:{cap.slug}"}]
    for m in r["metrics"]:
        if m.get("value") is not None:
            fs.append({"key": f"{base}.{m['id']}", "value": m["value"], "source": f"capability:{cap.slug}",
                       "confidence": 0.95 if m.get("status") == "ok" else 0.7})
    return fs


# --------------------------------------------------------------------- view

def render_html(r: dict[str, Any], *, refresh_s: int = 120) -> str:
    """The glance view. Top: the answer in its honest form, readable in seconds. Next: what is not
    knowable yet and the single action that would change that. Below, folded: every supporting
    number with its definition, the definitions, the sources. Server-rendered, no scripts."""
    e = html.escape
    c = r["capability"]
    heads = [m for m in r["metrics"] if m.get("headline")] or r["metrics"][:1]
    rest = [m for m in r["metrics"] if m not in heads]

    def card(m: dict[str, Any], big: bool) -> str:
        status = m.get("status")
        why = m.get("why") or ""
        note = "; ".join(m.get("notes") or [])
        caveats = "".join(f"<li>{e(x)}</li>" for x in (m.get("caveats") or [])[:4])
        blocked = status in ("blocked", "no_data", "error")
        return (f'<section class="card{" big" if big else ""}{" off" if blocked else ""}" data-metric="{e(m["id"])}" '
                f'data-status="{e(str(status))}"><div class="label">{e(m.get("label") or m["id"])}</div>'
                f'<div class="value" data-value="{e("" if m.get("value") is None else str(m["value"]))}">'
                f'{e(m["display"])}</div>'
                f'<div class="form">{e(_form_text(m))}{" · " + e(_window(m.get("window"))) if m.get("window") else ""}'
                f'{" · " + ("not connected" if status == "blocked" else e(str(status))) if status != "ok" else ""}</div>'
                f'<p class="def">{e(m.get("definition") or "")}</p>'
                + (f'<p class="why">{e(why)}</p>' if why and big else "") + (f'<p class="note">{e(note)}</p>' if note else "")
                + (f'<ul class="caveats">{caveats}</ul>' if caveats and not big else "") + "</section>")

    core_unans = [u for u in r.get("unanswered", []) if u.get("priority", "core") == "core"]
    other_unans = [u for u in r.get("unanswered", []) if u not in core_unans]

    def unans_li(u: dict[str, Any]) -> str:
        un = u.get("unlock") or {}
        return (f'<li data-question="{e(u.get("question", ""))}"><b>{e(u.get("question_text") or u.get("question", ""))}</b>'
                f'<br>{e(u.get("why", ""))}'
                + (f'<br><span class="unlock">→ {e(un.get("human_action", ""))} '
                   f'(~{max(1, int(un.get("seconds", 0)) // 60)} min of your time)</span>' if un else "") + "</li>")

    srcs = "".join(
        f'<tr><td>{e(s["id"])}</td><td>{e(s["title"])}</td><td class="st-{e(s["status"])}">{e(s["status"])}</td>'
        f'<td>{e(s["last_ok"] or "never")}</td><td>{s["observations"]}</td>'
        f'<td>{e((s.get("blocker") or {}).get("detail") or s.get("last_error") or "")}</td></tr>'
        for s in r["sources"])
    defs = "".join(f"<li>{e(d)}</li>" for d in r["need"].get("definitions") or [])
    v = r.get("verification") or {}
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="{refresh_s}">
<title>{e(c["title"])}</title><style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6a66;--card:#fff;--line:#e5e2da;--acc:#0b6e4f;--warn:#9a5b00;--off:#b9b6ad}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ecebe6;--mut:#a3a19a;--card:#1f1f1d;--line:#34332f;--acc:#5fd3a8;--warn:#f0b35a;--off:#5c5a55}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}}
main{{max-width:900px;margin:0 auto;padding:20px 16px 40px}} h1{{font-size:18px;margin:0 0 4px}}
.sub{{color:var(--mut);margin:0 0 16px;font-size:13px}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}
.big .value{{font-size:52px;line-height:1.1}} .value{{font-size:26px;font-weight:650;color:var(--acc);font-variant-numeric:tabular-nums}}
.off .value{{color:var(--off)}} .label{{font-weight:600}} .form{{color:var(--mut);font-size:13px}} .def{{font-size:13px;margin:.5em 0 0}}
.why,.note{{font-size:12px;color:var(--warn);margin:.3em 0 0}} .caveats{{font-size:12px;color:var(--mut);padding-left:18px;margin:.4em 0 0}}
h2{{font-size:15px;margin:22px 0 8px}} .next{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--warn);border-radius:10px;padding:10px 14px 10px 30px;margin:14px 0 0}}
.next li{{margin:4px 0}} table{{width:100%;border-collapse:collapse;font-size:12px}} td,th{{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}}
.st-blocked{{color:var(--warn)}} .st-ok{{color:var(--acc)}} .unlock{{color:var(--warn)}} ul{{margin:0}} .tbl{{overflow-x:auto}}
details{{margin-top:18px}} summary{{cursor:pointer;font-weight:600}}
</style></head><body><main>
<h1>{e(r["need"].get("sentence") or c["title"])}</h1>
<p class="sub">as of {e(r.get("as_of") or "no data yet")} · answers <b data-coverage="{r["coverage"]}">{round(r["coverage"] * 100)}%</b>
of the question · v{c["version"]} {e(c["status"])} · checked by Regent {e(str(v.get("at") or "never"))}</p>
<div class="grid">{"".join(card(m, True) for m in heads)}</div>
{f'<ul class="next">{"".join(unans_li(u) for u in core_unans)}</ul>' if core_unans else ""}
<details><summary>Supporting numbers ({len(rest)})</summary><div class="grid" style="margin-top:10px">{"".join(card(m, False) for m in rest)}</div></details>
<details><summary>Also asked, not knowable yet ({len(other_unans)})</summary><ul>{"".join(unans_li(u) for u in other_unans)}</ul></details>
<details><summary>Definitions</summary><ul>{defs}</ul></details>
<details><summary>Sources</summary><div class="tbl"><table><tr><th>id</th><th>source</th><th>status</th><th>last read</th><th>readings</th><th>note</th></tr>{srcs}</table></div></details>
</main></body></html>"""


def _window(w: str | None) -> str:
    return {"all_time": "all time", "now": "latest reading"}.get(w or "", w or "")


def _form_text(m: dict[str, Any]) -> str:
    return {"lower_bound": "at least (lower bound)", "upper_bound": "at most (upper bound)", "exact": "exact",
            "estimate": "estimate", "range": "range", "activity": "activity signal, not a count of people"}.get(
        m.get("form") or "", m.get("form") or "")


# --------------------------------------------------------------- interface

def register_tool(services: Any, cap: SwCapability) -> None:
    """Expose the capability as a Regent tool so routes (and later missions) can use it."""
    from regent import db as dbm
    from regent.schemas import ToolResult
    from regent.tools.base import ActionSpec, Tool

    cap_id = cap.id
    caps = [f"need:{s}" for s in cap.signature or []]

    def handler(action: str, inputs: dict[str, Any], ctx) -> ToolResult:
        s = dbm.session()
        try:
            c = s.get(SwCapability, cap_id)
            allowed = ("usable", "degraded") + (("built",) if inputs.get("for_verification") else ())
            if c is None or c.status not in allowed:
                return ToolResult(status="failed", error=f"capability {cap_id} is not usable ({c.status if c else 'gone'})")
            if action == "collect":
                obs = collect(s, c)
            r = read(s, c, count_use=not inputs.get("for_verification"))
            s.commit()
            out = {"capability": r["capability"], "coverage": r["coverage"], "as_of": r["as_of"],
                   "metrics": {m["id"]: {"value": m["value"], "display": m["display"], "form": m.get("form"),
                                         "status": m.get("status")} for m in r["metrics"]},
                   "unanswered": [u.get("question") for u in r["unanswered"]]}
            if action == "collect":
                out["observations"] = {o.source_id: o.status for o in obs}
            return ToolResult(status="ok", outputs=out, facts=facts(c, r))
        finally:
            s.close()

    services.tools.register(Tool(
        name=cap.tool_name, executor="api", description=f"{cap.title} (Regent capability v{cap.version})",
        actions={"read": ActionSpec("read", "Current answer with definitions and honest forms", "AUTO",
                                    capabilities=caps, latency_s=0.2),
                 "collect": ActionSpec("collect", "Read every source now, then answer", "AUTO",
                                       capabilities=caps, latency_s=5)},
        handler=handler, backend="live", built_by_regent=True))


def load_all(db: Session, services: Any) -> int:
    n = 0
    for c in db.scalars(select(SwCapability).where(SwCapability.status.in_(("usable", "degraded")))):
        register_tool(services, c)
        n += 1
    return n


def due(cap: SwCapability) -> bool:
    period = int((cap.spec or {}).get("refresh_s") or 900)
    last = _aware(cap.last_collect_at)
    return last is None or now() - last >= timedelta(seconds=period)
