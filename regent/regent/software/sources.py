"""Sources beyond the subject's own site: proposed by the reasoning worker, admitted only by use.

Not every need is about a product Regent can read from the outside. "Will it rain in Osaka?"
is answered by public data services and by existing services people already use. The
reasoning worker knows many of them; it is asked for exact URLs, and every proposal is then
*fetched* by Regent:

* a proposed API is admitted when it answers (robots.txt respected, GET only) with JSON that
  contains at least one of the fields it was proposed for;
* a proposed page of an existing service is admitted when it loads and the worker, shown the
  page's live text, gives extraction patterns that Regent can run against that same text and
  that return the example values the worker claims are on the page.

Decision rules ("good day to dry laundry = ...") are proposed the same way: as inspectable
expressions over admitted fields, each threshold with its reason. Regent parses them, checks
they only read admitted fields, and evaluates them on the live values before accepting them.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from regent.software import expr as X
from regent.software import probe
from regent.software.reasoner import Reasoner, ReasonerUnavailable

PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["apis", "existing_services"],
    "properties": {
        "apis": {"type": "array", "items": {"type": "object", "required": ["url", "why", "expected_fields", "question"],
                 "properties": {"url": {"type": "string", "description": "full GET URL, parameters filled in"},
                                "why": {"type": "string"},
                                "expected_fields": {"type": "array", "items": {"type": "string"},
                                                    "description": "dotted JSON paths, list indexes as numbers"},
                                "question": {"type": "string"}, "needs_key": {"type": "boolean"}}}},
        "existing_services": {"type": "array", "items": {"type": "object", "required": ["url", "name", "why", "question",
                                                                                        "link_texts"],
                              "properties": {"url": {"type": "string"}, "name": {"type": "string"},
                                             "why": {"type": "string"}, "question": {"type": "string"},
                                             "link_texts": {"type": "array", "items": {"type": "string"},
                                                            "description": "visible link texts a person would click, "
                                                                           "from the site's home page to this page, "
                                                                           "in the site's language"}}}},
    },
}

PROPOSAL_INSTRUCTIONS = """You advise an operational agent that must answer the principal's need with live data. Propose
(1) public data APIs that need no key and return JSON relevant to the questions -- give exact GET URLs with every
parameter filled in from the resolved subjects (coordinates, time zone, identifiers), and the JSON paths you expect
in the answer; and (2) pages of existing services people already use that answer the question directly (give the
exact page URL for this subject, plus the link texts a person would click from the site's home page to reach it,
in case the URL has moved). Each proposal is fetched and checked; propose only what you are confident exists.
Prefer few, reliable sources. If nothing beyond what was already observed is relevant, return empty lists."""

READING_SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["answers_question", "readings"],
    "properties": {
        "answers_question": {"type": "boolean"},
        "readings": {"type": "array", "items": {"type": "object", "required": [
            "name", "pattern", "example", "meaning", "question", "relation_to_need"],
            "properties": {
                "name": {"type": "string", "description": "snake_case field name"},
                "pattern": {"type": "string", "description": "Python regex with ONE capture group, matched against "
                            "the page text exactly as given"},
                "example": {"type": "string", "description": "the value the pattern captures in this text"},
                "meaning": {"type": "string"}, "question": {"type": "string"},
                "relation_to_need": {"type": "string", "enum": ["direct", "lower_bound", "upper_bound",
                                                                "activity_signal", "context", "unrelated"]},
                "counts": {"type": "string"}}}},
    },
}

READING_INSTRUCTIONS = """Below is the visible text of a page of an existing service, as fetched just now. Say whether it
answers the principal's question for this subject, and give regular expressions (each with exactly one capture group)
that pull the answering values out of this text, with the exact value each captures here. Values that change daily
must be captured by the pattern, not written into it. Anchor patterns on stable labels, not on today's values."""

RULE_SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["rules"],
    "properties": {"rules": {"type": "array", "items": {"type": "object", "required": [
        "id", "question", "expr", "yes_label", "no_label", "rationale", "thresholds"],
        "properties": {
            "id": {"type": "string"}, "question": {"type": "string"},
            "expr": {"type": "string", "description": "boolean expression using latest(\"ref\") over the given refs, "
                     "comparisons, and/or/not, numbers"},
            "yes_label": {"type": "string"}, "no_label": {"type": "string"}, "rationale": {"type": "string"},
            "thresholds": {"type": "array", "items": {"type": "object", "required": ["ref", "threshold", "why"],
                           "properties": {"ref": {"type": "string"}, "threshold": {"type": "string"},
                                          "why": {"type": "string"}}}}}}}},
}

RULE_INSTRUCTIONS = """The principal wants a yes/no answer. Using ONLY the listed fields (refer to each as
latest("<ref>")), write a decision rule as a boolean expression with explicit thresholds, the conventional or
physical reason for each threshold, and short labels for yes and no. The rationale must hold on any day: do not
mention today's values in it. If an existing service already gives a
direct verdict or index, you may use it, but prefer rules that remain correct if one source is missing (use 'or' /
'and' deliberately). Do not invent fields. One or two rules at most."""


def propose(fetcher, need: dict[str, Any], resolved: dict[str, Any], reasoner: Reasoner, *,
            mission_id: str | None, log) -> dict[str, Any]:
    out: dict[str, Any] = {"apis": [], "pages": [], "rejected": [], "meta": {"by": "none"}}
    if not reasoner.available():
        out["meta"] = {"by": "none", "error": "no reasoning worker: only the subject's own sources"}
        return out
    payload = {
        "need": {"sentence": need.get("sentence"), "questions": need.get("questions"),
                 "deliverable": need.get("deliverable")},
        "principal": need.get("principal"),
        "subjects": [{k: v for k, v in s.items() if k in ("name", "kind", "place")}
                     | {"deployments": [d["host"] for d in s.get("deployments", [])]}
                     for s in resolved.get("subjects", [])],
    }
    out["meta"] = {"rounds": []}
    for round_ in range(2):
        if round_ and (out["apis"] or out["pages"]) and len(out["apis"]) + len(out["pages"]) >= 2:
            break
        if round_:
            # the first proposals did not survive contact with the web: say why and ask again
            payload = {**payload, "rejected_proposals": out["rejected"],
                       "instruction": "Every source above was rejected for the stated reason (robots.txt "
                                      "disallow is final: do not propose that host again). Propose different sources."}
        try:
            ans = reasoner.ask("source_proposals", PROPOSAL_INSTRUCTIONS, payload, PROPOSAL_SCHEMA, budget_usd=0.8,
                               mission_id=mission_id)
        except ReasonerUnavailable as e:
            out["meta"]["error"] = str(e)[:300]
            break
        out["meta"]["rounds"].append({"by": ans.provider, "model": ans.model, "cost_usd": ans.cost_usd,
                                      "answer_key": ans.key, "proposed": len(ans.output.get("apis", []))
                                      + len(ans.output.get("existing_services", []))})
        _admit(fetcher, need, reasoner, ans.output, out, mission_id, log)
    return out


def _admit(fetcher, need, reasoner, proposal: dict[str, Any], out: dict[str, Any], mission_id, log) -> None:
    seen = {a["url"] for a in out["apis"]} | {p["url"] for p in out["pages"]} | {r["url"] for r in out["rejected"]}
    for a in proposal.get("apis", [])[:8]:
        if a.get("url") in seen:
            continue
        url = a.get("url", "")
        if not url.startswith("https://") or a.get("needs_key"):
            out["rejected"].append({"url": url, "why": "needs a key or not https"})
            continue
        doc = fetcher.fetch(url, purpose="proposal:api", kind="public_data", render="static")
        if not doc.ok:
            out["rejected"].append({"url": url, "why": f"did not answer: {doc.status} {doc.blocked or doc.error or ''}"})
            continue
        try:
            data = json.loads(doc.html)
        except ValueError:
            out["rejected"].append({"url": url, "why": "not JSON"})
            continue
        shape = probe.json_shape(data)
        present = [f for f in a.get("expected_fields", []) if shape_has(shape, f)]
        if not present:
            out["rejected"].append({"url": url, "why": f"none of the expected fields {a.get('expected_fields')}"})
            continue
        from regent.software.discover import _trim

        out["apis"].append({"url": url, "path": urlparse(url).path, "status": 200, "json": True, "shape": shape,
                            "sample": _trim(data), "context": [f"proposed: {a.get('why')}"], "writes_in_code": [],
                            "document_id": doc.id, "proposed_fields": present, "origin": "api"})
        log("inventory", f"admitted API {urlparse(url).netloc}{urlparse(url).path}: fields {present}")
    for p in proposal.get("existing_services", [])[:4]:
        url = p.get("url", "")
        if not url.startswith(("https://", "http://")) or url in seen:
            continue
        # pages built by JavaScript have no text until rendered: render when the static text is thin
        doc = fetcher.fetch(url, purpose="proposal:page", kind="portal", render="auto",
                            content_check=lambda text: len(text) >= 400)
        if not doc.ok and doc.status in (404, 410) and p.get("link_texts"):
            # the guessed address is wrong but the site is real: get there the way a person would
            found = _navigate(fetcher, url, p["link_texts"], log)
            if found is not None:
                log("inventory", f"{url} was {doc.status}; reached {found.final_url} by following "
                                 f"{p['link_texts']}")
                doc, url = found, found.final_url
        if doc.ok and doc.html.lstrip()[:1] in "[{":
            try:
                data = json.loads(doc.html)
            except ValueError:
                data = None
            if data is not None:            # a data endpoint, not a page: read it as data
                from regent.software.discover import _trim

                out["apis"].append({"url": url, "path": urlparse(url).path, "status": 200, "json": True,
                                    "shape": probe.json_shape(data), "sample": _trim(data),
                                    "context": [f"proposed as {p.get('name')}: {p.get('why')}"], "writes_in_code": [],
                                    "document_id": doc.id, "proposed_fields": [], "origin": "api"})
                log("inventory", f"admitted data endpoint {p.get('name')} ({urlparse(url).netloc})")
                continue
        if not doc.ok or len(doc.text) < 200:
            out["rejected"].append({"url": url, "why": f"page not readable: {doc.status} {doc.blocked or doc.error or ''}"})
            continue
        page = _read_page(doc, need, p, reasoner, mission_id)
        if not page["readings"]:
            out["rejected"].append({"url": url, "why": page.get("why") or "no verifiable reading"})
            continue
        out["pages"].append(page)
        log("inventory", f"admitted existing service {p.get('name')} ({urlparse(url).netloc}): "
                         + ", ".join(f"{r['name']}={r['value']!r}" for r in page["readings"]))


def _navigate(fetcher, url: str, terms: list[str], log, beam: int = 2):
    from regent.acquisition import navigate as nav

    u = urlparse(url)
    doc = fetcher.fetch(f"{u.scheme}://{u.netloc}/", purpose="proposal:navigate", kind="portal", render="static")
    if not doc.ok:
        return None
    for i, term in enumerate(terms[:4]):
        nxt = None
        for link in nav.find_links(doc.html, doc.final_url, term)[:beam]:
            d = fetcher.fetch(link, purpose="proposal:navigate", kind="portal", render="static")
            if d.ok:
                nxt = d
                break
        if nxt is None:
            log("inventory", f"navigation on {u.netloc}: no link '{term}'")
            return doc if i else None
        doc = nxt
    return doc


def _excerpt(text: str, need: dict[str, Any], n: int = 7000) -> str:
    if len(text) <= n:
        return text
    words = {w.lower() for s in need.get("subjects", []) for w in re.findall(r"\w+", s.get("name", ""))}
    for q in need.get("questions", []):
        words |= {w.lower() for w in re.findall(r"\w{4,}", q.get("question", ""))}
    best, best_i = -1, 0
    for i in range(0, max(1, len(text) - n), 500):
        chunk = text[i:i + n].lower()
        score = sum(chunk.count(w) for w in words)
        if score > best:
            best, best_i = score, i
    return text[best_i:best_i + n]


def _read_page(doc, need, proposal, reasoner: Reasoner, mission_id) -> dict[str, Any]:
    from regent.software.connectors import extract_patterns

    text = doc.text
    excerpt = _excerpt(text, need)
    payload = {"question": [q.get("question") for q in need.get("questions", [])], "subject":
               [s.get("name") for s in need.get("subjects", [])], "service": proposal.get("name"),
               "url": doc.final_url, "page_text": excerpt}
    try:
        ans = reasoner.ask("page_reading", READING_INSTRUCTIONS, payload, READING_SCHEMA, budget_usd=0.8,
                           mission_id=mission_id)
    except ReasonerUnavailable as e:
        return {"url": doc.final_url, "readings": [], "why": str(e)[:200]}
    readings = []
    for r in ans.output.get("readings", []):
        try:
            rx = re.compile(r["pattern"], re.S)
        except re.error:
            continue
        if rx.groups != 1:
            continue
        got = extract_patterns(text, {r["name"]: r["pattern"]}).get(r["name"])
        if got is None or _loose(str(got)) != _loose(str(r.get("example", ""))):
            continue                        # the worker's claim about the page did not hold
        m = rx.search(text)
        readings.append({**r, "value": got, "evidence": text[max(0, m.start() - 40):m.end() + 40].strip()})
    return {"url": doc.final_url, "name": proposal.get("name"), "why": proposal.get("why"),
            "answers_question": bool(ans.output.get("answers_question")), "readings": readings,
            "document_id": doc.id, "meta": {"by": ans.provider, "cost_usd": ans.cost_usd, "answer_key": ans.key},
            "origin": "page"}


def decision_rules(need: dict[str, Any], fields: list[dict[str, Any]], refs: dict[str, Any], reasoner: Reasoner, *,
                   mission_id: str | None) -> dict[str, Any]:
    """Rules for yes/no questions over admitted fields. ``refs`` maps "source:path" -> live value."""
    yes_no = [q for q in need.get("questions", []) if q.get("answer_type") == "yes_no"]
    if not yes_no or not refs or not reasoner.available():
        return {"rules": [], "rejected": []}
    payload = {"questions": yes_no, "fields": [
        {"ref": ref, "value_now": v, "meaning": next((f.get("meaning") for f in fields if f.get("_ref") == ref), "")}
        for ref, v in refs.items()]}
    ok: list[dict[str, Any]] = []
    bad: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"rounds": []}
    for round_ in range(2):
        if ok:
            break
        if round_:
            # the first rules failed Regent's checks: say exactly why and ask once more
            payload = {**payload, "rejected_rules": bad, "instruction": "Each rule above was rejected for the "
                       "stated reason. Values are compared as numbers only when they are numbers (a unit suffix "
                       "like '%' is stripped); compare text values with ==. Write a rule that evaluates now."}
        try:
            ans = reasoner.ask("decision_rules", RULE_INSTRUCTIONS, payload, RULE_SCHEMA, budget_usd=0.8,
                               mission_id=mission_id)
        except ReasonerUnavailable as e:
            bad.append({"why": str(e)[:200]})
            break
        meta["rounds"].append({"by": ans.provider, "cost_usd": ans.cost_usd, "answer_key": ans.key})
        _check_rules(need, refs, ans.output.get("rules", [])[:2], ok, bad)
    return {"rules": ok, "rejected": bad, "meta": meta}


def _check_rules(need, refs, rules, ok, bad) -> None:
    from datetime import datetime, timezone

    for rule in rules:
        try:
            used = X.references(rule["expr"])
        except X.ExprError as e:
            bad.append({"rule": rule.get("id"), "why": f"does not parse: {e}"})
            continue
        unknown = [u for u in used if u not in refs]
        if unknown:
            bad.append({"rule": rule.get("id"), "why": f"reads fields that were not admitted: {unknown}"})
            continue
        now = datetime.now(timezone.utc)
        ctx = X.EvalContext(series=lambda r: [(now, refs[r])] if refs.get(r) is not None else [], now=now)
        try:
            v = X.evaluate(rule["expr"], ctx)
        except (X.ExprError, TypeError, ValueError) as e:
            bad.append({"rule": rule.get("id"), "why": f"does not evaluate: {e}"})
            continue
        if not isinstance(v, bool):
            bad.append({"rule": rule.get("id"), "why": f"evaluates to {v!r}, not yes/no"})
            continue
        from regent.software.need import question_id

        qid = question_id(need, rule.get("question"))
        if qid is None:
            bad.append({"rule": rule.get("id"), "why": f"answers no known question: {rule.get('question')!r}"})
            continue
        ok.append({**rule, "question": qid, "value_now": v, "refs": used})


def shape_has(shape: dict[str, str], path: str) -> bool:
    if path in shape or any(k.startswith(path + ".") for k in shape):
        return True
    base = re.sub(r"(\.\d+)+$", "", path)          # "daily.temperature_2m_max.0" -> the list itself
    return base != path and base in shape


def _loose(s: str) -> str:
    return re.sub(r"[\s,]+", "", s).strip().lower()
