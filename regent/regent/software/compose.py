"""Composing a capability from verified parts.

The composer turns the inventory's *field semantics* into a capability spec
deterministically: which sources to read, one metric per field that bears on the need (its
expression chosen from the field's time semantics, its honest form from its relation to the
need), a headline, and -- for every core question no metric answers well -- an explicit
"not knowable yet" entry naming the smallest action that would unlock it.

The reasoning worker never writes expressions or code here: it only said what fields mean,
and Regent checked those readings (quotes found, fields exist). Composition is Regent's.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from regent.software import capability as K
from regent.software import connectors as C
from regent.software import expr as X

RELATION_FORM = {"direct": "estimate", "measure": "estimate", "lower_bound": "lower_bound",
                 "at_least_one": "lower_bound", "upper_bound": "upper_bound", "proxy": "proxy",
                 "activity_signal": "proxy", "context": "context"}
FORM_RANK = {"exact": 5, "estimate": 4, "decision": 4, "range": 3, "lower_bound": 2, "upper_bound": 2, "activity": 1,
             "proxy": 1, "context": 0}
ORIGINS = ("product", "api", "page")


def _sid(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:64] or "src"


def source_id(endpoint: str) -> str:
    """Stable, readable id for a source: the connector, or host label + path ("lindy_api_fund")."""
    if endpoint.startswith("connector:"):
        return _sid(endpoint.split(":", 1)[1])
    u = urlparse(endpoint)
    host = u.netloc.split(":")[0]
    label = next((p for p in host.split(".") if p not in ("www", "api", "m")), host.split(".")[0])
    path = [p for p in u.path.split("/") if p and p not in ("api", "v1", "v2", "v3")]
    return _sid("_".join([label] + path[-2:]))[:40]


_source_id = source_id


def metrics_for(field: dict[str, Any], sid: str, windows: list[str], unit: str = "") -> list[dict[str, Any]]:
    """Metrics a field supports, chosen from how it behaves over time. ``unit`` is the unit of the
    question the field bears on ("people", "yes/no", ...)."""
    path, ts = field["path"], field.get("time_semantics")
    ref = f"{sid}:{path}"
    form = RELATION_FORM.get(field.get("relation_to_need"), "activity")
    noun = "people" if field.get("counts") == "people" or "people" in (unit or "").lower() else (unit or "")
    base = {"answers": field.get("question"), "form": form,
            "unit": "events" if form in ("activity", "proxy") else ("people" if field.get("counts") == "people"
                                                                     or field.get("audit") else unit),
            "caveats": list(dict.fromkeys(field.get("caveats") or [])),
            "source_field": {"source": sid, "path": path, "meaning": field.get("meaning"),
                             "confidence": field.get("confidence"), "evidence": field.get("evidence")}}
    tail = [p for p in path.split(".") if not p.isdigit() and p not in ("data", "details", "properties", "areas")]
    mid = _sid(f"{sid}_{'_'.join(tail[-2:]) or path}")
    out = []
    if ts == "event_list":
        key = field.get("timestamp_key")
        if not key:
            return [{**base, "id": f"{mid}_seen", "label": f"{field['meaning']}", "expr": f'distinct_items("{ref}")',
                     "window": "all_time", "definition": f"Distinct items observed in {path}: {field['meaning']}"}]
        for w in [w for w in windows if w not in ("all_time", "now")][:2] + ["all_time"]:
            wexpr = "all" if w == "all_time" else w
            out.append({**base, "id": f"{mid}_{w}", "label": ("Proxy: " if form == "proxy" else "")
                        + f"{_short(field['meaning'])} ({w.replace('_', ' ')})",
                        "expr": f'count_items("{ref}", "{key}", "{wexpr}")', "window": w,
                        "definition": f"Events listed in {path} with {key} inside the window: {field['meaning']}"})
        return out
    if ts == "resets_daily":
        return [{**base, "id": f"{mid}_24h", "label": ("Proxy: " if form == "proxy" else "")
                 + f"{_short(field['meaning'])} (last 24h)",
                 "expr": f'increase("{ref}", "24h")', "window": "24h",
                 "definition": f"Increase of the daily counter {path} over the last 24 hours (resets counted): "
                               f"{field['meaning']}"}]
    window = "all_time" if ts == "cumulative" else "now"
    audit = field.get("audit") or {}
    base["source_field"]["audit"] = {k: audit.get(k) for k in ("claimed", "relation", "missing")} if audit else None
    if field.get("relation_to_need") == "at_least_one":
        # someone in the population acted; how many is not established
        return [{**base, "id": f"{mid}_people", "label": f"{(noun or 'count').capitalize()}, at least",
                 "unit": noun or base["unit"], "expr": f'min(latest("{ref}"), 1)', "window": window,
                 "definition": f"At least one, because {path} counts {field.get('counts_unit') or 'units'} that only "
                               f"members of the population produce ({field['meaning']}); that they are distinct "
                               "people is not established.",
                 "caveats": base["caveats"]},
                {**base, "id": mid, "form": "proxy", "unit": field.get("counts_unit") or "units",
                 "label": f"Proxy: {_short(field['meaning'], 40)}", "expr": f'latest("{ref}")', "window": window,
                 "definition": field["meaning"]}]
    if form == "proxy":
        return [{**base, "id": mid, "unit": field.get("counts_unit") or "units",
                 "label": f"Proxy: {_short(field['meaning'], 40)}", "expr": f'latest("{ref}")', "window": window,
                 "definition": f"{field['meaning']}. A signal that may move with the answer; no number of "
                               f"{noun or 'the population'} follows from it."
                               + (f" Missing: {', '.join(audit.get('missing') or [])}." if audit.get('missing') else "")}]
    # cumulative totals and snapshots: the current value is the answer
    label = {"lower_bound": "People, at least", "upper_bound": "People, at most", "estimate": "People (estimate)"}.get(
        form) if field.get("counts") == "people" or audit else None
    if field.get("service") and form != "context":
        label = f"{_short(field['meaning'], 48)} ({field['service'].split()[0]})"
    return [{**base, "id": mid, "label": label or _short(field["meaning"]), "expr": f'latest("{ref}")',
             "window": window, "definition": field["meaning"]}]


def _short(s: str, n: int = 48) -> str:
    s = re.split(r"[;(]", s or "")[0].strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def compose(need: dict[str, Any], inventory: dict[str, Any], *, include: list[str] | None = None,
            title: str | None = None, use: tuple[str, ...] | list[str] = ORIGINS) -> dict[str, Any]:
    """Build a capability spec from the inventory. ``include`` lists platform connector ids to
    wire in (they stay blocked until their credential exists); ``use`` the kinds of public
    sources to read: the subject's own endpoints, public data APIs, existing services' pages."""
    include = include or []
    units = {q["id"]: q.get("unit") or "" for q in need.get("questions", [])}
    fields = [f for f in inventory.get("public_fields", []) if f.get("path_exists")
              and f.get("relation_to_need") != "unrelated" and float(f.get("confidence") or 0) >= 0.3
              and (str(f.get("endpoint", "")).startswith("connector:") or f.get("origin", "product") in use)]
    windows = sorted({w for q in need.get("questions", []) for w in q.get("windows", [])},
                     key=lambda w: (X.window_s(w) or 1e12) if re.fullmatch(r"\d+[smhdw]", w or "") else 1e13)
    sources: dict[str, dict[str, Any]] = {}
    metrics: list[dict[str, Any]] = []
    for f in fields:
        ep = f["endpoint"]
        if ep.startswith("connector:"):
            cid = ep.split(":", 1)[1]
            if cid not in include:
                continue
            sid = _sid(cid)
            if sid not in sources:
                ps = next((p for p in inventory.get("platform_sources", []) if p["id"] == cid), None)
                if ps is None:
                    continue
                sources[sid] = {"id": sid, "connector": cid, "title": ps["title"], "params": ps["params"],
                                "access": "credential", "credentials": ps["credentials"],
                                "footprint": ps["footprint"], "fields": []}
        elif f.get("connector") == "html_page":
            sid = _source_id(ep)
            sources.setdefault(sid, {"id": sid, "connector": "html_page", "title": f"{f.get('service') or 'page'}: {ep}",
                                     "params": {"url": ep, "patterns": {}}, "access": "public", "credentials": [],
                                     "footprint": C.HTML_PAGE.footprint, "origin": "page", "fields": []})
            sources[sid]["params"]["patterns"][f["path"]] = f["pattern"]
        else:
            sid = _source_id(ep)
            sources.setdefault(sid, {"id": sid, "connector": "http_json", "title": f"GET {ep}",
                                     "params": {"url": ep, "fields": []}, "access": "public", "credentials": [],
                                     "footprint": C.HTTP_JSON.footprint, "origin": f.get("origin", "product"),
                                     "fields": []})
            if f["path"] not in sources[sid]["params"]["fields"]:
                sources[sid]["params"]["fields"].append(f["path"])
        sources[sid]["fields"].append({"path": f["path"], "meaning": f.get("meaning"),
                                       "relation": f.get("relation_to_need"), "time_semantics": f.get("time_semantics")})
        metrics += metrics_for(f, sid, windows, units.get(f.get("question") or "", ""))
    # decision rules over the fields this capability reads
    readable = {f"{sid}:{fl['path']}" for sid, src in sources.items() for fl in src["fields"]}
    for rule in inventory.get("decision_rules", []):
        if not set(rule.get("refs") or []) <= readable:
            continue
        qtext = next((q.get("question") for q in need.get("questions", []) if q["id"] == rule["question"]), "")
        metrics.append({"id": _sid(f"rule_{rule['id']}"), "label": _short(qtext or "Verdict", 70),
                        "answers": rule["question"], "form": "decision", "unit": "yes/no", "expr": rule["expr"],
                        "yes": rule.get("yes_label"), "no": rule.get("no_label"), "window": "now",
                        "definition": rule.get("rationale", "") + " Rule: " + "; ".join(
                            f"{t['ref'].split(':', 1)[1].split('.')[-1]} {t['threshold']} ({t['why']})"
                            for t in rule.get("thresholds", [])),
                        "caveats": ["a stated rule, not a measurement; thresholds are conventions"],
                        "source_field": {"rule": rule["id"], "refs": rule.get("refs")}})
    # both ends known for a question: the honest headline is the range between them
    for q in need.get("questions", []):
        lows = [m for m in metrics if m.get("answers") == q["id"] and m["form"] == "lower_bound"]
        highs = [m for m in metrics if m.get("answers") == q["id"] and m["form"] == "upper_bound"
                 and m["expr"].startswith("latest(")]
        if lows and highs:
            lo = lows[0]["expr"] if len(lows) == 1 else "max(" + ", ".join(m["expr"] for m in lows) + ")"
            hi = highs[0]["expr"] if len(highs) == 1 else "min(" + ", ".join(m["expr"] for m in highs) + ")"
            metrics.append({
                "id": f"{_sid(q['id'])}_range", "label": "People, between",
                "answers": q["id"], "form": "range", "unit": "people", "expr": lo, "expr_hi": hi,
                "window": "now",
                "definition": "Lower end: " + "; ".join(m["definition"] for m in lows) + " Upper end: "
                              + "; ".join(m["definition"] for m in highs) + ". The true number is between them.",
                "caveats": sorted({c for m in lows + highs for c in m.get("caveats", [])})[:6],
                "source_field": {"combines": [m["id"] for m in lows + highs],
                                 "audit": {"relation": "range", "parts": [(m.get("source_field") or {}).get("audit")
                                                                          for m in lows + highs]}}})
    # one metric per id
    seen, uniq = set(), []
    for m in metrics:
        if m["id"] not in seen:
            seen.add(m["id"])
            uniq.append(m)
    metrics = uniq
    core = [q for q in need.get("questions", []) if q.get("priority", "core") == "core"] or need.get("questions", [])
    # headline: the most direct answer to the first core question, then its lower bounds
    # a count of people nobody measures is shown as unknown -- a proxy is never the headline number
    for q in core[:1]:
        best_form = max((FORM_RANK.get(m["form"], 0) for m in metrics if m.get("answers") == q["id"]), default=0)
        if q.get("answer_type", "count") == "count" and best_form < FORM_RANK["lower_bound"]:
            metrics.insert(0, {"id": f"{_sid(q['id'])}_answer", "label": _short(q.get("question") or "Answer", 70),
                               "answers": q["id"], "form": "context", "unit": q.get("unit") or "", "expr": "coalesce()",
                               "unknowable": True, "window": "now", "headline": 1,
                               "why": "nothing Regent can read counts these people: only proxies (below), each "
                                      "missing a premise that would turn it into a bound",
                               "definition": f"{q.get('quantity')}. Population: {q.get('population')}."})
    gated = {sid for sid, src in sources.items() if src["access"] == "credential"}

    def needs_credential(m: dict[str, Any]) -> bool:
        refs = X.references(m["expr"]) + (X.references(m["expr_hi"]) if m.get("expr_hi") else [])
        return any(r.split(":", 1)[0] in gated for r in refs)

    for q in core[:1]:
        prefer = "decision" if q.get("answer_type") == "yes_no" else None
        cands = sorted([m for m in metrics if m.get("answers") == q["id"]],
                       key=lambda m: (-FORM_RANK.get(m["form"], 0), m["form"] != prefer,
                                      m.get("window") != "all_time"))
        # the best answer, and the best answer readable today (so the view never opens on blanks); a
        # stated rule is shown next to the strongest independent answer to the same question
        if any(m.get("unknowable") and m.get("answers") == q["id"] for m in metrics):
            continue                       # the unknown is the headline; proxies stay below
        picks = cands[:1] + [m for m in cands if not needs_credential(m) and m is not cands[0]
                             and (m["form"] != cands[0]["form"] or not prefer)][:1]
        if cands and not picks[1:] and not needs_credential(cands[0]):
            picks = cands[:1]
        for i, m in enumerate(picks, 1):
            m["headline"] = i
    unanswered = []
    platform = {p["id"]: p for p in inventory.get("platform_sources", [])}
    for q in need.get("questions", []):
        now_forms = {m["form"] for m in metrics if m.get("answers") == q["id"] and not needs_credential(m)}
        best_now = _answer_weight(now_forms)
        if best_now >= K.FORM_WEIGHT["estimate"]:
            continue
        unlock = _unlock_for(q, inventory, platform, now_forms, include)
        why = ("no source Regent can read answers this" if not now_forms else
               "only proxies: signals that move with the answer but are not counts of the people asked about"
               if now_forms <= {"activity", "proxy", "context"} else
               "only a bound, not a count" if best_now <= K.FORM_WEIGHT["lower_bound"] else "only a partial answer")
        if unlock and unlock["connector"] in include:
            why += (f"; {unlock['title']} is wired in and turns this into "
                    f"{_FORM_PHRASE.get(unlock['gives'], unlock['gives'])} "
                    f"once {unlock['credential']} arrives")
        unanswered.append({"question": q["id"], "question_text": q.get("question"), "why": why, "unlock": unlock,
                           "priority": q.get("priority", "core")})
    definitions = [f"{m['label']}: {m['definition']}" + (f" (form: {m['form'].replace('_', ' ')})")
                   for m in metrics if m.get("headline")]
    definitions += [f"Not counted as people: {', '.join(core[0].get('exclude') or [])}"] if core else []
    definitions += [f"Unresolved from outside the product: {d}" for d in need.get("definitions_to_state", [])[:4]]
    subj = ", ".join(s["name"] for s in need.get("subjects", []))
    return {
        "title": title or f"{subj}: {core[0]['question'] if core else need.get('sentence')}"[:120],
        "slug": f"{subj}-{core[0]['id'] if core else 'need'}",
        "purpose": need.get("sentence"), "subjects": [s["name"] for s in need.get("subjects", [])],
        "sources": list(sources.values()), "metrics": metrics, "unanswered": unanswered,
        "definitions": definitions, "refresh_s": _refresh_s(need), "delivery": _delivery(need, inventory), "constraints": ["read_only", "aggregates_only", "no_client_code"]
        + [f"respects: {c['statement']}" for c in inventory.get("constraints", []) if c.get("evidence_found")],
        "composed_from": {"fields": len(fields), "include": include},
    }


_FORM_PHRASE = {"range": "a range", "activity": "an activity signal", "lower_bound": "a lower bound",
                "upper_bound": "an upper bound", "estimate": "an estimate", "exact": "an exact count"}


def _refresh_s(need: dict[str, Any]) -> int:
    refresh = (need.get("deliverable") or {}).get("refresh")
    return {"continuous": 900, "daily": 3 * 3600, "on_demand": 3600}.get(refresh, 86400)


def _delivery(need: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any] | None:
    """A deliverable the principal is *told* (an alert, a morning report) is pushed, not just viewable."""
    d = need.get("deliverable") or {}
    if d.get("form") not in ("alert", "report") and d.get("refresh") != "daily":
        return None
    tz = next((x["place"].get("timezone") for x in inventory.get("subjects", []) if x.get("place")), None)
    when = d.get("deliver_at_local") or "07:00"
    return {"schedule": "daily" if d.get("refresh") == "daily" else "on_change", "local_time": when,
            "timezone": tz or "UTC", "channel": "regent_inbox",
            "channels_needing_setup": ["email (SMTP credential)", "phone push (an app the principal installs)"]}


def _answer_weight(forms: set[str]) -> float:
    w = max((K.FORM_WEIGHT.get(f, 0.0) for f in forms), default=0.0)
    if {"lower_bound", "upper_bound"} <= forms:
        w = max(w, K.FORM_WEIGHT["range"])
    return w


def _unlock_for(q: dict[str, Any], inventory: dict[str, Any], platform: dict[str, Any], now_forms: set[str],
                include: list[str]) -> dict[str, Any] | None:
    """The single action that improves this answer most: a connector whose field, combined with
    what is readable today, gives the best honest form (a new upper bound next to a known lower
    bound makes a range; another lower bound adds nothing). Forbidden sources never qualify."""
    best = None
    base = _answer_weight(now_forms)
    for f in inventory.get("public_fields", []):
        ep = str(f.get("endpoint", ""))
        if not ep.startswith("connector:") or f.get("question") != q["id"] or not f.get("path_exists"):
            continue
        rel = f.get("relation_to_need")
        if rel in ("unrelated", "context"):
            continue
        p = platform.get(ep.split(":", 1)[1])
        if p is None or p.get("forbidden_by"):
            continue
        form = RELATION_FORM.get(rel, "activity")
        after = _answer_weight(now_forms | {form})
        if after <= base:
            continue
        key = (after, p["id"] in include, -p["human_seconds"])
        if best is None or key > best[0]:
            host = p.get("host") or ""
            gives = "range" if {"lower_bound", "upper_bound"} <= (now_forms | {form}) else form
            best = (key, {"connector": p["id"], "title": p["title"], "credential": (p["credentials"] or [None])[0],
                          "human_action": p["human_action"].format(zone=host, site=f"https://{host}/"),
                          "seconds": p["human_seconds"], "form": form, "gives": gives, "field": f["path"]})
    return best[1] if best else None


def coverage_estimate(need: dict[str, Any], inventory: dict[str, Any], include: list[str],
                      use: tuple[str, ...] = ORIGINS) -> float:
    """Coverage a composed capability would reach if every included source delivered."""
    spec = compose(need, inventory, include=include, use=use)
    if_delivered = [{**m, "status": "ok", "value": 1} for m in spec["metrics"]]
    return K.coverage(need, if_delivered)[0]
