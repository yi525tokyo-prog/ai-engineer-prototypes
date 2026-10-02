"""World acquisition for software needs: find the subject, read it, inventory what could answer.

``resolve``   -- from a subject's *name* to the live deployments that are it: probe the hostnames
                 such a product would plausibly use (name variants x common TLDs x hosting
                 platforms' default domains), keep the ones that present themselves under that
                 name, follow redirects to the canonical host, fingerprint each (platform,
                 analytics already present, API the app calls, public commitments), and read the
                 app's API endpoints with GET only. Every observation is stored as a claim with
                 the document it came from.
``inventory`` -- which sources could answer the need's questions: the product's own public
                 endpoints (field meanings proposed by the reasoning worker, each backed by a
                 verbatim quote Regent checks), platform connectors the fingerprint makes
                 applicable (and what access each needs), capabilities Regent already has for the
                 same need, and the commitments that constrain what may be built.
"""

from __future__ import annotations

from datetime import datetime

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.acquisition.claims import ClaimStore
from regent.acquisition.fetch import Fetcher
from regent.acquisition.tables import AcqEntity
from regent.acquisition.types import AttrSpec, ClaimIn, FreshnessPolicy
from regent.ids import new_id
from regent.software import connectors as C
from regent.software import principal, probe
from regent.software import semantics as SM
from regent.software.reasoner import Reasoner, ReasonerUnavailable, get_reasoner

DAY = 86400.0
ATTRS = {a.name: a for a in [
    AttrSpec("title", "deployment", "text", ttl_s=7 * DAY),
    AttrSpec("names_subject", "deployment", "number", ttl_s=7 * DAY, abs_tol=0.05),
    AttrSpec("platforms", "deployment", "json", ttl_s=7 * DAY),
    AttrSpec("analytics", "deployment", "json", ttl_s=DAY),
    AttrSpec("promises", "deployment", "json", ttl_s=7 * DAY),
    AttrSpec("csp_connect", "deployment", "json", ttl_s=7 * DAY),
    AttrSpec("owner_evidence", "deployment", "text", ttl_s=30 * DAY),
    AttrSpec("status", "endpoint", "number", ttl_s=DAY),
    AttrSpec("content_type", "endpoint", "text", ttl_s=DAY),
    AttrSpec("json_shape", "endpoint", "json", ttl_s=DAY),
    AttrSpec("sample", "endpoint", "json", ttl_s=3600, material=False),
    AttrSpec("used_by_code_with", "endpoint", "json", ttl_s=7 * DAY),
]}
POLICY = FreshnessPolicy(ATTRS)
MAX_ENDPOINTS = 24


def _entity(db: Session, etype: str, label: str, request_id: str, **features: Any) -> AcqEntity:
    e = db.scalar(select(AcqEntity).where(AcqEntity.domain == "software", AcqEntity.entity_type == etype,
                                          AcqEntity.block_key == label[:200]))
    if e is None:
        e = AcqEntity(id=new_id("swe"), domain="software", entity_type=etype, label=label[:300], block_key=label[:200],
                      features=features, beliefs={}, stage="discovered", request_id=request_id, source_hosts=[])
        db.add(e)
        db.flush()
    else:
        e.features = {**(e.features or {}), **features}
    return e


def _claim(store: ClaimStore, e: AcqEntity, attr: str, value: Any, doc, evidence: str = "", conf: float = 0.9) -> None:
    store.add(e.id, ClaimIn(attr, value, confidence=conf, evidence=evidence[:500], extractor="software.probe"),
              source_host=doc.host, source_kind="operator", url=doc.final_url, document_id=doc.id)


# ------------------------------------------------------------------ resolve

def resolve(db: Session, need: dict[str, Any], *, request_id: str, transport=None, log=None,
            max_probes: int = 90) -> dict[str, Any]:
    log = log or (lambda *a, **k: None)
    fetcher = Fetcher(db, request_id=request_id, transport=transport, allow_browser=False, min_interval_s=0.3)
    store = ClaimStore(db, POLICY)
    country = (need.get("principal") or {}).get("country")
    out: dict[str, Any] = {"subjects": [], "principal": principal.evidence()}
    try:
        for subj in need.get("subjects", []):
            if subj.get("kind") == "place":
                out["subjects"].append(_resolve_place(fetcher, subj, log))
                continue
            if subj.get("kind") not in ("product", "website", "organization", "dataset", "other"):
                out["subjects"].append({"name": subj["name"], "kind": subj.get("kind"), "skipped":
                                        "a person: nothing to look up on the public web"})
                continue
            if subj.get("kind") == "other" and (not probe.looks_like_name(subj["name"])
                                                or need.get("need_type") in ("tool", "action")
                                                or _TIME_WORDS.search(subj["name"])):
                out["subjects"].append({"name": subj["name"], "kind": "topic", "skipped":
                                        "a topic, not a named thing: it has no site of its own to find",
                                        "deployments": []})
                continue
            out["subjects"].append(_resolve_one(db, fetcher, store, subj, request_id, country, log, max_probes))
    finally:
        fetcher.close()
    db.flush()
    return out


GEOCODER = "https://geocoding-api.open-meteo.com/v1/search?count=5&format=json&language=en&name="


def _resolve_place(fetcher, subj, log) -> dict[str, Any]:
    """A place becomes coordinates, country and time zone (public geocoder, GeoNames data); the
    most populous match wins and the alternatives are kept."""
    from urllib.parse import quote

    data = fetcher.get_json(GEOCODER + quote(subj["name"])) or {}
    hits = sorted(data.get("results") or [], key=lambda h: -(h.get("population") or 0))
    if not hits:
        log("resolve", f"place '{subj['name']}' not found by the geocoder")
        return {"name": subj["name"], "kind": "place", "resolved": False, "deployments": []}
    h = hits[0]
    place = {k: h.get(k) for k in ("name", "latitude", "longitude", "country_code", "country", "admin1", "timezone",
                                   "population", "elevation")}
    log("resolve", f"place '{subj['name']}' -> {place['name']}, {place.get('admin1')}, {place.get('country_code')} "
                   f"({place['latitude']:.3f}, {place['longitude']:.3f}, {place.get('timezone')})")
    return {"name": subj["name"], "kind": "place", "resolved": True, "place": place, "deployments": [],
            "alternatives": [f"{x.get('name')}, {x.get('admin1')}, {x.get('country_code')}" for x in hits[1:4]],
            "source": GEOCODER + subj["name"]}


def _resolve_one(db, fetcher, store, subj, request_id, country, log, max_probes) -> dict[str, Any]:
    name = subj["name"]
    urls = probe.candidate_urls(name, country=country, accounts=principal.accounts())[:max_probes]
    log("resolve", f"probing {len(urls)} hostnames a product called '{name}' could live at")
    tried, matches = [], {}
    for u in urls:
        doc = fetcher.fetch(u, purpose="resolve", kind="operator", render="static")
        row = {"url": u, "status": doc.status, "final": doc.final_url,
               "outcome": "no such site" if doc.status == 0 else (f"HTTP {doc.status}" if not doc.ok else "")}
        if doc.ok:
            title = re.search(r"<title[^>]*>(.*?)</title>", doc.html, re.S | re.I)
            t = re.sub(r"\s+", " ", title.group(1)).strip() if title else ""
            score = probe.names_subject(name, t, doc.text)
            row.update(title=t[:120], names_subject=score)
            host = urlparse(doc.final_url).netloc
            if score >= 0.7:
                row["outcome"] = f"presents itself as {name}"
                prev = matches.get(host)
                if prev is None or score > prev["score"]:
                    matches[host] = {"doc": doc, "score": score, "via": [u] + (prev["via"] if prev else [])}
                else:
                    prev["via"].append(u)
            else:
                row["outcome"] = "live site, but not this subject"
        tried.append(row)
    deployments = []
    # the same site served under several hostnames (custom domain + platform default) is one
    # deployment with aliases: prefer the hostname that is not a platform default
    groups: dict[str, list[str]] = {}
    for host, m in matches.items():
        body = m["doc"].html
        for h in matches:
            body = body.replace(h, "")
        body = re.sub(r"\s+", " ", re.sub(r"<(?:link|meta)[^>]*(?:canonical|og:url)[^>]*>", "", body))
        groups.setdefault(body[:20000], []).append(host)
    for hosts in groups.values():
        hosts.sort(key=lambda h: (h.endswith(probe.PLATFORM_SUBDOMAINS), -matches[h]["score"], len(h)))
        main = matches[hosts[0]]
        main["aliases"] = hosts[1:]
        d = _read_deployment(db, fetcher, store, name, hosts[0], main, request_id, log)
        d["aliases"] = hosts[1:]
        deployments.append(d)
    product = _entity(db, "product", name, request_id, kind=subj.get("kind"))
    product.stage = "resolved" if deployments else "unresolved"
    for d in deployments:
        d["entity"].parent_id = product.id
    log("resolve", f"'{name}': {len(deployments)} deployment(s) found from {len(tried)} probes: "
                   + ", ".join(d["host"] for d in deployments))
    return {"name": name, "kind": subj.get("kind"), "product_entity": product.id, "probes": len(tried),
            "probe_log": tried, "deployments": [{k: v for k, v in d.items() if k != "entity"} for d in deployments]}


def _read_deployment(db, fetcher, store, name, host, m, request_id, log) -> dict[str, Any]:
    doc = m["doc"]
    fp = probe.fingerprint(doc.final_url, doc.html, doc.headers, doc.text)
    # the app's own scripts carry the API it talks to
    code = doc.html
    for src in [s for s in fp["scripts"] if urlparse(s).netloc == host][:5]:
        d2 = fetcher.fetch(src, purpose="resolve:script", kind="operator", render="static")
        if d2.ok:
            code += "\n" + d2.html
    extra = {}
    for path in ("/llms.txt", "/manifest.json", "/manifest.webmanifest"):
        d3 = fetcher.fetch(f"https://{host}{path}", purpose="resolve:meta", kind="operator", render="static")
        if d3.ok and not d3.html.lstrip().startswith("<"):
            extra[path] = d3.html[:4000]
    if fp["manifest"] and fp["manifest"] not in [f"https://{host}{p}" for p in extra]:
        d4 = fetcher.fetch(fp["manifest"], purpose="resolve:meta", kind="operator", render="static")
        if d4.ok:
            extra[urlparse(fp["manifest"]).path] = d4.html[:4000]
    fp["endpoints"] = probe.api_endpoints(doc.final_url, code, fp["csp"])
    fp["extra"] = extra
    e = _entity(db, "deployment", host, request_id, url=doc.final_url)
    e.stage = "resolved"
    _claim(store, e, "title", fp["title"], doc, fp["title"])
    _claim(store, e, "names_subject", m["score"], doc, fp["title"])
    _claim(store, e, "platforms", fp["platforms"], doc, "; ".join(v for k, v in fp["evidence"].items()
                                                                   if k.startswith("platform:")))
    _claim(store, e, "analytics", fp["analytics"], doc,
           "; ".join(v for k, v in fp["evidence"].items() if k.startswith("analytics:")) or
           "no known analytics signature in the page or its scripts")
    _claim(store, e, "promises", [p["phrase"] for p in fp["promises"]], doc,
           " | ".join(p["context"] for p in fp["promises"])[:500])
    _claim(store, e, "csp_connect", fp["csp_connect"], doc, "content-security-policy connect-src")
    owners = list(dict.fromkeys(o for o in (principal.owns_host(urlparse(b).netloc) for ep in fp["endpoints"]
                                            for b in ep["bases"]) if o))
    if principal.owns_host(host):
        owners.insert(0, principal.owns_host(host))
    if owners:
        _claim(store, e, "owner_evidence", owners[0], doc, owners[0], conf=0.6)
    endpoints = _read_endpoints(db, fetcher, store, e, fp, request_id, log)
    store.refresh(e)
    log("resolve", f"{host}: platforms {fp['platforms'] or 'unknown'}; analytics {fp['analytics'] or 'none'}; "
                   f"{len(fp['endpoints'])} API paths in code, {sum(1 for x in endpoints if x.get('json'))} readable")
    return {"host": host, "url": doc.final_url, "entity": e, "entity_id": e.id, "via": m["via"],
            "names_subject": m["score"], "document_id": doc.id,
            "fingerprint": {k: v for k, v in fp.items() if k not in ("endpoints", "evidence")},
            "fingerprint_evidence": fp["evidence"], "owner_evidence": owners[:3], "endpoints": endpoints}


def _read_endpoints(db, fetcher, store, dep, fp, request_id, log) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for ep in fp["endpoints"]:
        for base in ep["bases"][:3]:
            url = urljoin(base + "/", ep["path"].lstrip("/"))
            if url in seen or len(out) >= MAX_ENDPOINTS:
                continue
            seen.add(url)
            doc = fetcher.fetch(url, purpose="resolve:endpoint", kind="operator", render="static")
            ct = (doc.headers or {}).get("content-type", "")
            row: dict[str, Any] = {"url": url, "path": ep["path"], "status": doc.status, "content_type": ct,
                                   "writes_in_code": ep["writes"], "context": ep["context"][:2],
                                   "document_id": doc.id}
            if doc.status == 0:
                continue
            if not ("json" in ct or doc.html.lstrip()[:1] in "{[") and urlparse(url).netloc == dep.label:
                continue          # a single-page app answering every path with its own HTML
            data = None
            if doc.html and ("json" in ct or doc.html.lstrip()[:1] in "{["):
                try:
                    data = json.loads(doc.html)
                except ValueError:
                    data = None
            e = _entity(db, "endpoint", url, request_id, path=ep["path"])
            e.parent_id = dep.id
            _claim(store, e, "status", doc.status, doc, f"GET {url} -> {doc.status}")
            _claim(store, e, "content_type", ct, doc, ct)
            _claim(store, e, "used_by_code_with", ep["writes"] or ["GET"], doc, (ep["context"] or [""])[0][:300])
            if data is not None:
                shape = probe.json_shape(data)
                row.update(json=True, shape=shape, sample=_trim(data))
                _claim(store, e, "json_shape", shape, doc, doc.html[:400])
                _claim(store, e, "sample", _trim(data), doc, doc.html[:400], conf=0.95)
                row["same_as_index"] = False
            store.refresh(e)
            row["entity_id"] = e.id
            out.append(row)
    # an API that answers every unknown path with the same index document is not publishing
    # those paths: mark duplicates so they are not mistaken for data sources
    bodies: dict[str, int] = {}
    for r in out:
        if r.get("json"):
            k = json.dumps(r["sample"], sort_keys=True)[:2000]
            bodies[k] = bodies.get(k, 0) + 1
    for r in out:
        if r.get("json") and bodies[json.dumps(r["sample"], sort_keys=True)[:2000]] >= 3:
            r["same_as_index"] = True
    return out


def _trim(data: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "…"
    if isinstance(data, dict):
        return {k: _trim(v, depth + 1) for k, v in list(data.items())[:40]}
    if isinstance(data, list):
        return [_trim(v, depth + 1) for v in data[:3]] + ([f"… {len(data) - 3} more"] if len(data) > 3 else [])
    if isinstance(data, str):
        return data[:200]
    return data


# ---------------------------------------------------------------- inventory

SEMANTICS_SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["fields", "constraints", "other_sources"],
    "properties": {
        "fields": {"type": "array", "items": {"type": "object", "required": [
            "endpoint", "path", "meaning", "relation_to_need", "question", "time_semantics", "evidence", "confidence"],
            "properties": {
                "endpoint": {"type": "string"}, "path": {"type": "string", "description": "dotted path in the JSON"},
                "meaning": {"type": "string"},
                "counts": {"type": "string", "enum": ["people", "actions", "items", "money", "time", "other"]},
                "relation_to_need": {"type": "string", "enum": ["measure", "lower_bound", "upper_bound", "proxy",
                                                                "direct", "context", "unrelated"]},
                "counts_unit": {"type": "string", "description": "the thing each unit of the number is (payment, "
                                "IP address, request, customer id, page view...)"},
                "premises": {"type": "object", "description": "only for fields bearing on a count of people: "
                             "for each premise, does it hold for this field, why, and a verbatim quote showing it",
                             "properties": {p: SM.PREMISE_SCHEMA for p in SM.PREMISES}},
                "question": {"type": ["string", "null"], "description": "id of the need question it bears on"},
                "time_semantics": {"type": "string", "enum": ["cumulative", "resets_daily", "snapshot",
                                                              "event_list", "unknown"]},
                "timestamp_key": {"type": ["string", "null"],
                                  "description": "for event lists: the item key holding the event time"},
                "caveats": {"type": "array", "items": {"type": "string"}},
                "evidence": {"type": "string", "description": "a verbatim quote (8-200 chars) from the endpoint's "
                             "code context or response that supports this reading"},
                "confidence": {"type": "number"}}}},
        "constraints": {"type": "array", "items": {"type": "object", "required": ["statement", "evidence", "forbids"],
                        "properties": {"statement": {"type": "string"},
                                       "evidence": {"type": "string", "description": "verbatim quote of the promise"},
                                       "forbids": {"type": "array", "items": {"type": "string", "enum": [
                                           "adds_client_code", "third_party_tracking", "writes_to_product",
                                           "stores_personal_data", "requires_accounts", "shows_ads"]}}}}},
        "other_sources": {"type": "array", "items": {"type": "object", "required": ["title", "access", "question"],
                          "properties": {"title": {"type": "string"}, "access": {"type": "string"},
                                         "question": {"type": "string"}, "why": {"type": "string"}}}},
    },
}

SEMANTICS_INSTRUCTIONS = """You read a live product from the outside for an operational agent. Given the principal's
need (questions with precise definitions) and what was observed (public JSON endpoints the product's own code calls,
with the code around each call and a sample response; the product's stated commitments; platform sources that exist
but need the principal's access, described by their documented fields), say for each numeric or event-list field
that could bear on the questions what it measures, what one unit of it is, and how it relates to the need. For a
platform source use its "endpoint" value (connector:<id>) and the field name as path, and quote its documentation as
evidence. For a value inside a JSON array give the element's path with its index (e.g. "daily.temperature_2m_max.0").
For questions that are not counts of people use "direct" for a value that answers as it stands and "context" for
inputs to a decision.

For fields bearing on a count of people, the relation must be earned. Answer each premise for the field:
membership (every counted unit was produced by a member of the question's population -- real, external, not bots,
crawlers, the operator, staff or tests), distinctness (no member counted twice), window (units fall in the question's
window), coverage (every member produced at least one unit), no_merging (no unit stands for two or more members).
Say "unknown" when nothing observed settles it, and "no" when you know it fails (e.g. one IP address can be shared by
a whole household or office, so IP counts merge people; crawlers have IPs too). Claim "measure" / "lower_bound" /
"upper_bound" only if you believe the corresponding premises hold, otherwise "proxy". Regent applies the inference
rules itself and only accepts premises whose evidence it finds verbatim in the input. A number counting something
other than people using this product (upstream catalogue counts, sizes, budgets, caps) is "unrelated". Also list the
product's commitments that constrain how usage may be measured, and other sources the principal likely holds."""


def inventory(db: Session, need: dict[str, Any], resolved: dict[str, Any], *, reasoner: Reasoner | None = None,
              mission_id: str | None = None, log=None, request_id: str | None = None,
              transport=None) -> dict[str, Any]:
    from regent.software import sources as S
    from regent.software.compose import source_id

    log = log or (lambda *a, **k: None)
    r = reasoner or get_reasoner()
    deployments = [d for s in resolved.get("subjects", []) for d in s.get("deployments", [])]
    endpoints = [{**ep, "origin": "product"} for d in deployments for ep in d["endpoints"]
                 if ep.get("json") and ep.get("status") == 200 and not ep.get("same_as_index")]
    fetcher = Fetcher(db, request_id=request_id, transport=transport, allow_browser=transport is None,
                      min_interval_s=0.5)
    try:
        proposals = S.propose(fetcher, need, resolved, r, mission_id=mission_id, log=log)
    finally:
        fetcher.close()
    endpoints += proposals["apis"]
    promises = [p for d in deployments for p in d["fingerprint"].get("promises", [])]
    payload = {
        "need": {"sentence": need.get("sentence"), "questions": need.get("questions"),
                 "definitions_to_state": need.get("definitions_to_state")},
        "endpoints": [{"url": ep["url"], "code_context": ep["context"], "used_with": ep["writes_in_code"] or ["GET"],
                       "fields": ep["shape"], "sample": ep["sample"]} for ep in endpoints],
        "commitments": promises,
        "platform": [{"host": d["host"], "platforms": d["fingerprint"]["platforms"],
                      "analytics_found": d["fingerprint"]["analytics"]} for d in deployments],
        # sources that exist for this product's platform but need the principal's access
        "platform_sources": [{"endpoint": f"connector:{conn.id}", "title": conn.title, "measures": conn.measures,
                              "population": conn.population, "fields": conn.fields}
                             for conn in {c.id: c for d in deployments
                                          for c, _ in C.applicable({**d["fingerprint"], "host": d["host"]})}.values()
                             if conn.collect is not None],
    }
    sem: dict[str, Any] = {"fields": [], "constraints": [], "other_sources": []}
    meta: dict[str, Any] = {"by": "none"}
    if endpoints or promises:
        if r.available():
            try:
                ans = r.ask("field_semantics", SEMANTICS_INSTRUCTIONS, payload, SEMANTICS_SCHEMA, budget_usd=1.5,
                            mission_id=mission_id)
                sem = ans.output
                meta = {"by": ans.provider, "model": ans.model, "cost_usd": ans.cost_usd, "answer_key": ans.key}
            except ReasonerUnavailable as e:
                meta = {"by": "none", "error": str(e)[:300]}
        else:
            meta = {"by": "none", "error": "no reasoning worker: field meanings unknown"}
    haystack = json.dumps(payload, ensure_ascii=False)
    fields = []
    for f in sem.get("fields", []):
        ev = (f.get("evidence") or "").strip()
        supported = len(ev) >= 6 and (ev in haystack or _loose(ev) in _loose(haystack) or _json_quote_in(ev, payload))
        f = {**f, "evidence_found": supported}
        if not supported:
            f["confidence"] = min(float(f.get("confidence") or 0), 0.2)
            f.setdefault("caveats", []).append("evidence quote not found in what was observed: unverified reading")
        ep = next((e for e in endpoints if e["url"] == f.get("endpoint")), None)
        if str(f.get("endpoint", "")).startswith("connector:"):
            conn = C.CONNECTORS.get(f["endpoint"].split(":", 1)[1])
            f["path_exists"] = bool(conn and f.get("path") in conn.fields)
        else:
            f["path_exists"] = bool(ep and S.shape_has(ep["shape"], f.get("path", "")))
            f["origin"] = ep.get("origin", "product") if ep else "product"
        if not f["path_exists"]:
            f["relation_to_need"] = "unrelated"
            f.setdefault("caveats", []).append("no such field in the observed response")
        fields.append(f)
    # what each field may honestly be used as: Regent's inference rules over evidenced premises
    from regent.software.need import question_id as _qid

    observed = re.sub(r"\s+", " ", haystack.replace('\\"', '"')).lower()
    counts = {q["id"] for q in need.get("questions", []) if q.get("answer_type", "count") == "count"}
    for f in fields:
        qid = _qid(need, f.get("question"))
        if qid in counts and f.get("relation_to_need") not in ("unrelated",):
            if f.get("relation_to_need") in ("direct", "activity_signal"):
                f["relation_to_need"] = {"direct": "measure", "activity_signal": "proxy"}[f["relation_to_need"]]
            a = SM.audit(f, observed)
            f["audit"] = a
            f["claimed_relation"] = a["claimed"]
            f["relation_to_need"] = a["relation"]
            f.setdefault("caveats", []).extend(a["notes"])
    # readings of existing services' pages: already verified against the live page text
    for page in proposals["pages"]:
        for rd in page["readings"]:
            if rd.get("relation_to_need") == "unrelated":
                continue
            fields.append({"endpoint": page["url"], "connector": "html_page", "path": rd["name"],
                           "pattern": rd["pattern"], "meaning": rd["meaning"], "question": rd.get("question"),
                           "relation_to_need": rd.get("relation_to_need", "context"), "counts": rd.get("counts"),
                           "time_semantics": "snapshot", "evidence": rd["evidence"], "evidence_found": True,
                           "path_exists": True, "confidence": 0.7, "origin": "page", "service": page.get("name"),
                           "caveats": [f"read from {page.get('name')}'s page; breaks if its layout changes"]})
    # questions referred to by text become ids; values' actual types decide how they behave over time
    from regent.software.need import question_id

    for f in fields:
        f["question"] = question_id(need, f.get("question"))
        if f.get("origin") in ("product", "api") and not str(f.get("endpoint", "")).startswith("connector:"):
            ep = next((e for e in endpoints if e["url"] == f["endpoint"]), None)
            v = C.json_path(ep["sample"], f["path"]) if ep else None
            f["value_type"] = "list" if isinstance(v, list) else type(v).__name__
            if f.get("time_semantics") == "event_list" and not isinstance(v, list):
                f["time_semantics"] = "snapshot"
    # live values of every admitted field, for decision rules
    refs: dict[str, Any] = {}
    for f in fields:
        if f.get("relation_to_need") == "unrelated" or not f.get("path_exists") or \
                str(f.get("endpoint", "")).startswith("connector:") or float(f.get("confidence") or 0) < 0.3:
            continue          # rules may only read fields the composer will accept
        ref = f"{source_id(f['endpoint'])}:{f['path']}"
        f["_ref"] = ref
        if f.get("origin") == "page":
            page = next(p for p in proposals["pages"] if p["url"] == f["endpoint"])
            refs[ref] = next(rd["value"] for rd in page["readings"] if rd["name"] == f["path"])
        else:
            ep = next((e for e in endpoints if e["url"] == f["endpoint"]), None)
            v = C.json_path(ep["sample"], f["path"]) if ep else None
            if isinstance(v, (int, float, str, bool)):
                refs[ref] = v
    rules = S.decision_rules(need, fields, refs, r, mission_id=mission_id)
    constraints = []
    for c in sem.get("constraints", []):
        ev = (c.get("evidence") or "").strip()
        ok = bool(ev) and (_loose(ev) in _loose(haystack) or any(_loose(ev) in _loose(p["context"]) for p in promises))
        constraints.append({**c, "evidence_found": ok})
    if not sem.get("constraints") and meta.get("by") == "none":
        # the worker could not interpret the commitments, but they were seen in public: they still bind
        constraints += _literal_commitments(promises)
    # platform connectors the fingerprints make applicable
    platform_sources = []
    for d in deployments:
        fp = {**d["fingerprint"], "host": d["host"]}
        for conn, params in C.applicable(fp):
            forbidden = [c["statement"] for c in constraints if c["evidence_found"] and (
                (conn.adds_client_code and {"adds_client_code", "third_party_tracking"} & set(c["forbids"])))]
            platform_sources.append({**conn.describe(), "params": params, "host": d["host"],
                                     "forbidden_by": forbidden})
    existing = need.get("reuse") or []
    inv = {"public_fields": fields, "public_endpoints": [{k: ep[k] for k in ("url", "status", "shape")}
                                                        for ep in endpoints],
           "platform_sources": platform_sources, "constraints": constraints,
           "other_sources": sem.get("other_sources", []), "existing_capabilities": existing, "semantics": meta,
           "proposals": {"apis": [a["url"] for a in proposals["apis"]],
                         "pages": [{k: p[k] for k in ("url", "name", "answers_question")} for p in proposals["pages"]],
                         "rejected": proposals["rejected"], "meta": proposals["meta"]},
           "decision_rules": rules.get("rules", []), "rejected_rules": rules.get("rejected", []),
           "subjects": [{k: v for k, v in x.items() if k in ("name", "kind", "place", "resolved")}
                        for x in resolved.get("subjects", [])],
           "deployments": [{"host": d["host"], "url": d["url"], "platforms": d["fingerprint"]["platforms"],
                            "analytics": d["fingerprint"]["analytics"], "owner_evidence": d["owner_evidence"]}
                           for d in deployments]}
    useful = [f for f in fields if f["relation_to_need"] != "unrelated" and f["path_exists"]]
    log("inventory", f"{len(endpoints)} readable endpoints, {len(useful)} fields bear on the need, "
                     f"{len(platform_sources)} platform sources, {len(constraints)} commitments, "
                     f"{len(existing)} existing capabilities")
    return inv


_COMMITMENT_WORDS = (
    (r"\bno (?:third[- ]party )?(?:tracking|trackers?|analytics|cookies)\b|\bdon'?t track\b|\bnot tracked\b",
     ["third_party_tracking", "adds_client_code"]),
    (r"\bno ads\b|\bad[- ]free\b", ["shows_ads"]),
    (r"\bno (?:accounts?|sign[- ]?ups?|log[- ]?ins?)\b", ["requires_accounts"]),
)


def _literal_commitments(promises: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Commitments read literally from the promise phrases the fingerprint found (used when no
    reasoning worker could interpret them): conservative, quoted, and marked as such."""
    out = []
    for p in promises:
        text = f"{p.get('phrase', '')} {p.get('context', '')}"
        forbids = sorted({f for rx, fs in _COMMITMENT_WORDS if re.search(rx, text, re.I) for f in fs})
        if forbids:
            out.append({"statement": p.get("phrase") or p.get("context", "")[:120], "evidence": p.get("phrase", ""),
                        "forbids": forbids, "evidence_found": True, "interpreted_by": "literal reading"})
    return out


# days, months and other time words are capitalised in English but are not products to look up
_TIME_WORDS = re.compile(r"^(?:mon|tues|wednes|thurs|fri|satur|sun)day$|^(?:january|february|march|april|may|june|"
                         r"july|august|september|october|november|december|today|tomorrow|tonight|weekend)$|"
                         r"[月火水木金土日]曜", re.I)


def _json_quote_in(ev: str, observed: Any) -> bool:
    """A quote of JSON (``"paid": {"jpy": 1000, "count": 2}``) is found if the same keys and values
    appear somewhere in what was observed, in any key order: the same response can serialize its
    keys differently from one read to the next."""
    try:
        quoted = json.loads("{" + ev.strip().strip(",") + "}")
    except ValueError:
        try:
            quoted = json.loads(ev)
        except ValueError:
            return False
    if not isinstance(quoted, (dict, list)) or not quoted:
        return False

    def contains(actual: Any, want: Any) -> bool:
        if isinstance(want, dict):
            return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in want.items())
        if isinstance(want, list):
            return isinstance(actual, list) and all(any(contains(a, w) for a in actual) for w in want)
        return actual == want

    def anywhere(node: Any) -> bool:
        if contains(node, quoted):
            return True
        if isinstance(node, dict):
            return any(anywhere(v) for v in node.values())
        if isinstance(node, list):
            return any(anywhere(v) for v in node)
        if isinstance(node, str) and node[:1] in "{[":
            try:
                return anywhere(json.loads(node))
            except ValueError:
                return False
        return False

    return anywhere(observed)


def _loose(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace('\\"', '"')).strip().lower()


# ------------------------------------------------------------ tool needs

ALT_SCHEMA: dict[str, Any] = {"type": "object", "required": ["alternatives"], "properties": {"alternatives": {
    "type": "array", "items": {"type": "object", "required": ["name", "url", "meets", "account_needed", "privacy"],
                               "properties": {"name": {"type": "string"}, "url": {"type": "string"},
                                              "meets": {"type": "array", "items": {"type": "string"},
                                                        "description": "requirement ids it meets as it is"},
                                              "account_needed": {"type": "boolean"},
                                              "privacy": {"type": "string"}, "why_not": {"type": "string"}}}}}}

ALT_INSTRUCTIONS = """The principal wants an ability (requirements below). List existing software they could use
instead of having something built (at most 4, real products with their home page URL), and for each the requirement
ids it meets as it is today, whether an account is needed, and what it means for their privacy. Be strict: a
requirement that needs integration with the named service is met only if the product actually integrates it."""


def inventory_tool(db: Session, need: dict[str, Any], resolved: dict[str, Any], *, reasoner: Reasoner | None = None,
                   mission_id: str | None = None, log=None, request_id: str | None = None,
                   transport=None) -> dict[str, Any]:
    """For an ability the principal wants to keep using: what the software could build on (the
    subject's live API), what already exists instead (checked to be reachable), and what Regent
    already has."""
    log = log or (lambda *a, **k: None)
    r = reasoner or get_reasoner()
    deployments = [d for s in resolved.get("subjects", []) for d in s.get("deployments", [])]
    app_sources = [{"url": ep["url"], "fields": ep["shape"], "sample": ep["sample"], "used_by_product_code_like":
                    (ep.get("context") or [""])[0][:300]}
                   for d in deployments for ep in d["endpoints"]
                   if ep.get("json") and ep.get("status") == 200 and not ep.get("same_as_index")]
    alternatives, meta = [], {"by": "none"}
    if r.available():
        try:
            ans = r.ask("app_alternatives", ALT_INSTRUCTIONS, {"need": {k: need.get(k) for k in (
                "sentence", "requirements", "subjects")}}, ALT_SCHEMA, budget_usd=0.6, mission_id=mission_id)
            meta = {"by": ans.provider, "cost_usd": ans.cost_usd, "answer_key": ans.key}
            fetcher = Fetcher(db, request_id=request_id, transport=transport, allow_browser=False, min_interval_s=0.3)
            try:
                for a in ans.output.get("alternatives", [])[:4]:
                    doc = fetcher.fetch(a["url"], purpose="alternative", kind="portal", render="static")
                    alternatives.append({**a, "reachable": doc.ok, "status": doc.status,
                                         "blocked": (doc.blocked or {}).get("type")})
            finally:
                fetcher.close()
        except ReasonerUnavailable as e:
            meta = {"by": "none", "error": str(e)[:300]}
    remind = None
    if r.available() and need.get("need_type") == "action":
        from zoneinfo import ZoneInfo

        from regent.reminders import REMIND_INSTRUCTIONS, REMIND_SCHEMA

        tz = (need.get("principal") or {}).get("timezone") or "UTC"
        try:
            now_local = datetime.now(ZoneInfo(tz)).strftime("%A %Y-%m-%d %H:%M")
        except Exception:
            tz, now_local = "UTC", datetime.utcnow().strftime("%A %Y-%m-%d %H:%M")
        try:
            remind = r.ask("regent_message", REMIND_INSTRUCTIONS,
                           {"request": need.get("sentence"), "requirements": need.get("requirements"),
                            "now_local": now_local, "time_zone": tz}, REMIND_SCHEMA, budget_usd=0.3,
                           mission_id=mission_id).output
            remind["time_zone"] = tz
        except ReasonerUnavailable:
            remind = None
    inv = {"kind": "tool", "app_sources": app_sources, "alternatives": alternatives, "alternatives_meta": meta,
           "remind": remind,
           "existing_capabilities": need.get("reuse") or [], "constraints": [],
           "deployments": [{"host": d["host"], "url": d["url"]} for d in deployments],
           "subjects": [{k: v for k, v in x.items() if k in ("name", "kind", "place", "resolved")}
                        for x in resolved.get("subjects", [])]}
    log("inventory", f"tool need: {len(app_sources)} live endpoints to build on; {len(alternatives)} existing products "
                     f"({sum(1 for a in alternatives if a['reachable'])} reachable)")
    return inv
