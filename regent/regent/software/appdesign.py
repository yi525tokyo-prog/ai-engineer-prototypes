"""Designing an application Regent will have built: the contract, before any code exists.

Regent does not prescribe a framework. It fixes what it must be able to rely on:

* the **interface** it will use the application through (JSON endpoints, each tied to the
  requirements it serves) -- this becomes Regent's tool for the capability;
* **UI hooks** (``data-testid`` names) so a real browser can exercise the flows a person uses;
* the **runtime contract** (``regent.json``: build / test / start commands, a health endpoint,
  all state under a data directory Regent owns, credentials provided by Regent);
* **acceptance scenarios** Regent will run itself -- API calls with expectations, browser steps,
  restarts that must preserve data, and negative checks (what must *not* be possible).

The reasoning worker proposes the design; Regent checks it: every core requirement is covered by
a scenario, every scenario only uses declared endpoints and hooks, privacy requirements have
negative checks, and the interface keeps every endpoint an earlier version had (so tools and
data that depend on it survive upgrades).
"""

from __future__ import annotations

import re
from typing import Any

from regent.software.reasoner import Reasoner, ReasonerUnavailable, get_reasoner

STEP = {"type": "object", "required": ["do"], "properties": {
    "do": {"type": "string", "enum": ["call", "goto", "fill", "click", "expect_text", "expect_no_text",
                                      "expect_visible", "expect_hidden", "reload", "restart_app", "login", "logout"]},
    "api": {"type": "string", "description": "for call: the endpoint id"},
    "path_params": {"type": "object"}, "query": {"type": ["object", "null"], "description": "for call: query "
                                                                                 "string parameters"},
    "body": {"type": ["object", "null"]},
    "auth": {"type": "boolean", "description": "for call: send the principal's credential (default true)"},
    "expect_status": {"type": "integer"},
    "expect_json_contains": {"type": ["object", "null"], "description": "key/values that must appear somewhere in "
                             "the JSON response (strings match case-insensitively as substrings)"},
    "save": {"type": ["object", "null"], "description": "var -> dotted path into the JSON response, e.g. "
             "{\"book_id\": \"id\"}; later steps may use {var} in path_params/body/value"},
    "path": {"type": "string", "description": "for goto: a URL path"},
    "target": {"type": "string", "description": "for fill/click/expect_visible/expect_hidden: a data-testid"},
    "value": {"type": "string"}, "text": {"type": "string"}}}

DESIGN_SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["name", "summary", "entities", "api", "ui", "scenarios", "external_hosts",
                                   "auth"],
    "properties": {
        "name": {"type": "string", "description": "kebab-case"},
        "summary": {"type": "string"},
        "entities": {"type": "array", "items": {"type": "object", "required": ["name", "fields"],
                     "properties": {"name": {"type": "string"},
                                    "fields": {"type": "array", "items": {"type": "string"}}}}},
        "auth": {"type": "object", "required": ["scheme", "why"],
                 "properties": {"scheme": {"type": "string", "enum": ["none", "passphrase"]},
                                "why": {"type": "string"}}},
        "api": {"type": "array", "items": {"type": "object", "required": ["id", "method", "path", "purpose",
                                                                          "requirements"],
                "properties": {"id": {"type": "string"}, "method": {"type": "string",
                                                                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                               "path": {"type": "string", "description": "starts with /api/, {param} for params"},
                               "purpose": {"type": "string"}, "request": {"type": ["object", "null"]},
                               "response": {"type": "string"},
                               "public": {"type": "boolean", "description": "reachable without the credential"},
                               "requirements": {"type": "array", "items": {"type": "string"}}}}},
        "ui": {"type": "array", "items": {"type": "object", "required": ["testid", "purpose"],
               "properties": {"testid": {"type": "string"}, "purpose": {"type": "string"},
                              "page": {"type": "string"}}}},
        "scenarios": {"type": "array", "items": {"type": "object", "required": ["id", "requirement", "kind", "steps"],
                      "properties": {"id": {"type": "string"},
                                     "requirement": {"type": "string", "description": "the id(s) of the "
                                                     "requirement(s) this scenario proves, exactly as given, "
                                                     "space-separated"},
                                     "kind": {"type": "string", "enum": ["api", "browser", "negative"]},
                                     "steps": {"type": "array", "items": STEP}}}},
        "external_hosts": {"type": "array", "items": {"type": "string"},
                           "description": "hosts the application may call (e.g. a public catalogue API)"},
        "migration": {"type": ["string", "null"], "description": "for a new version: how existing data is kept"},
    },
}

INSTRUCTIONS = """Design an application an operational agent will have a coding agent build, run and then use itself
through its JSON API. Do not choose a language or framework. Give: the data it keeps; whether it needs a credential
(a passphrase the agent provides) and why; the JSON API (every path under /api/, always including GET /api/health
that is public); data-testid hooks on the interface elements a person uses; and acceptance scenarios the agent will
run itself against a running instance -- API scenarios, browser scenarios (log in, fill, click, expect text, reload,
restart_app to prove data survives), and negative scenarios proving what must NOT be possible (for privacy: content
unreachable without the credential). Every core requirement needs at least one scenario. Use realistic data from the
supplied sources where the app integrates them. Keep it as small as the requirements allow.
Each scenario runs against a freshly started, EMPTY application (only the credential exists): it must create every
record it relies on, and may not rely on any other scenario. Scenarios are run literally: write {passphrase} wherever the credential goes (the agent substitutes the real one);
pass GET parameters in "query"; assert only text the scenario itself entered or the supplied data contains -- for a
message whose wording you cannot know (an error, an empty state) declare a data-testid and use expect_visible. Never
write placeholder values. State creation responses as you specify them in the endpoint (e.g. 201)."""

UPGRADE = """This is a new version of an application that is already in use. Keep every existing endpoint working
with the same paths and fields (the agent's tools and the principal's data depend on them); add what the new
requirements need; describe the migration that keeps all existing data; add scenarios for the new requirements,
including negative ones."""


def design(need: dict[str, Any], context: dict[str, Any], *, reasoner: Reasoner | None = None,
           previous: dict[str, Any] | None = None, mission_id: str | None = None) -> dict[str, Any]:
    r = reasoner or get_reasoner()
    payload = {"need": {k: need.get(k) for k in ("sentence", "requirements", "deliverable", "subjects")},
               "sources": context.get("sources", []), "previous_design": previous,
               "constraints": context.get("constraints", [])}
    try:
        ans = r.ask("app_design", INSTRUCTIONS + ("\n\n" + UPGRADE if previous else ""), payload, DESIGN_SCHEMA,
                    budget_usd=2.0, mission_id=mission_id)
    except ReasonerUnavailable as e:
        return {"ok": False, "error": str(e)[:300]}
    d = ans.output
    problems = check(d, need, previous)
    first = None
    if problems:      # rejected once, with Regent's reasons; the second answer is checked the same way
        first = {"design": d, "problems": problems}
        try:
            ans = r.ask("app_design", INSTRUCTIONS + ("\n\n" + UPGRADE if previous else ""),
                        {**payload, "rejected_design": d, "rejected_because": problems}, DESIGN_SCHEMA,
                        budget_usd=2.0, mission_id=mission_id)
            d = ans.output
            problems = check(d, need, previous)
        except ReasonerUnavailable:
            pass
    return {"ok": not problems, "design": d, "problems": problems, "first_rejected": first,
            "meta": {"by": ans.provider, "cost_usd": ans.cost_usd, "answer_key": ans.key}}


def scenario_requirements(sc: dict[str, Any], ids: set[str]) -> set[str]:
    """The requirement ids a scenario says it proves (designers write ``"r2 r3 browser flow"`` as
    readily as ``"r2"``)."""
    return {tok for tok in re.findall(r"[A-Za-z0-9_.-]+", str(sc.get("requirement") or "")) if tok in ids}


def check(d: dict[str, Any], need: dict[str, Any], previous: dict[str, Any] | None = None) -> list[str]:
    """Regent's checks on a proposed design (it is a claim like any other)."""
    problems = []
    api = {a["id"]: a for a in d.get("api", [])}
    testids = {u["testid"] for u in d.get("ui", [])}
    if not any(a["method"] == "GET" and a["path"].rstrip("/") == "/api/health" for a in api.values()):
        problems.append("no GET /api/health")
    for a in api.values():
        if not a["path"].startswith("/api/"):
            problems.append(f"endpoint {a['id']} is not under /api/")
    for sc in d.get("scenarios", []):
        for st in sc.get("steps", []):
            if st["do"] == "call" and st.get("api") not in api:
                problems.append(f"scenario {sc['id']} calls undeclared endpoint {st.get('api')}")
            if st["do"] in ("fill", "click") and st.get("target") not in testids:
                problems.append(f"scenario {sc['id']} uses undeclared hook {st.get('target')}")
    ids = {r["id"] for r in need.get("requirements", [])}
    covered = {x for sc in d.get("scenarios", []) for x in scenario_requirements(sc, ids)}
    for sc in d.get("scenarios", []):
        for st in sc.get("steps", []):
            for v in _strings(st):
                if PLACEHOLDER.search(v):
                    problems.append(f"scenario {sc['id']} uses a placeholder value {v!r}: it would be run literally")
            body = st.get("body") or {}
            if st.get("do") == "call" and isinstance(body, dict) and "passphrase" in body \
                    and body["passphrase"] != "{passphrase}" and sc.get("kind") != "negative":
                problems.append(f"scenario {sc['id']} logs in with {body['passphrase']!r} instead of {{passphrase}}")
            if st.get("do") in ("expect_visible", "expect_hidden") and st.get("target") not in testids:
                problems.append(f"scenario {sc['id']} uses undeclared hook {st.get('target')}")
    for r in need.get("requirements", []):
        if r.get("priority", "core") == "core" and r["id"] not in covered:
            problems.append(f"core requirement {r['id']} has no acceptance scenario")
    private = any(re.search(r"privat|only (?:i|me|the principal)|sign", (r.get("capability", "") + r.get("acceptance", "")),
                            re.I) for r in need.get("requirements", []))
    if private:
        if d.get("auth", {}).get("scheme") == "none":
            problems.append("a privacy requirement but no credential")
        if not any(sc.get("kind") == "negative" for sc in d.get("scenarios", [])):
            problems.append("a privacy requirement but no negative scenario")
    if not any(st["do"] == "restart_app" for sc in d.get("scenarios", []) for st in sc.get("steps", [])):
        problems.append("no scenario proves data survives a restart")
    if previous:
        old = {(a["method"], a["path"]) for a in previous.get("api", [])}
        new = {(a["method"], a["path"]) for a in d.get("api", [])}
        gone = sorted(old - new)
        if gone:
            problems.append(f"endpoints of the version in use were dropped: {gone}")
    return problems


PLACEHOLDER = re.compile(r"__[A-Z0-9_]+__|placeholder|\bTODO\b|<[a-z_]+>|\bXXX\b", re.I)


def _strings(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [s for k, x in v.items() if k not in ("do",) for s in _strings(x)]
    if isinstance(v, list):
        return [s for x in v for s in _strings(x)]
    return []


def requirement_coverage(d: dict[str, Any], need: dict[str, Any], results: dict[str, bool]) -> float:
    """Share of core requirements whose every scenario passed when Regent ran it."""
    core = [r["id"] for r in need.get("requirements", []) if r.get("priority", "core") == "core"]
    if not core:
        return 0.0
    ids = {r["id"] for r in need.get("requirements", [])}
    ok = 0
    for rid in core:
        scs = [sc["id"] for sc in d.get("scenarios", []) if rid in scenario_requirements(sc, ids)]
        if scs and all(results.get(s) for s in scs):
            ok += 1
    return round(ok / len(core), 3)
