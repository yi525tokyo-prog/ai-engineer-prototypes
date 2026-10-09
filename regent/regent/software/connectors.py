"""Data-source connectors a software capability can be composed from.

A connector is a *kind* of source: how to read it, what access it needs, what it can and
cannot measure, and what reading it costs the product (a server-side read adds nothing to
the product; a client-side script changes what the product does to its users).

``detect`` decides from a deployment fingerprint (see ``probe.fingerprint``) whether the
connector applies to a product at all -- nothing here names a particular product. Public
connectors are usable immediately; credential connectors are real clients that stay
``blocked: missing_credential`` until the principal provides the named credential, and are
never simulated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote, urlparse

import httpx

from regent.software import secrets

USER_AGENT = "RegentBot/0.1 (+https://github.com/yi525tokyo-prog/ai-engineer-prototypes; operational agent)"


class ConnectorBlocked(RuntimeError):
    def __init__(self, kind: str, detail: str, credential: str | None = None):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail, self.credential = kind, detail, credential


@dataclass
class Reading:
    fields: dict[str, Any]
    url: str
    status: int = 200
    raw_excerpt: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Connector:
    id: str
    title: str
    access: str                                  # public | credential
    credentials: list[str]
    measures: str                                # what the numbers are
    population: str                              # who/what is counted, including known contamination
    footprint: str                               # "read-only, server-side" / "adds client-side code" ...
    human_action: str = ""                       # smallest action that unlocks it (credential connectors)
    human_seconds: int = 0
    detect: Callable[[dict[str, Any]], list[dict[str, Any]]] = lambda fp: []
    collect: Callable[[dict[str, Any]], Reading] | None = None
    adds_client_code: bool = False
    fields: dict[str, str] = field(default_factory=dict)   # field -> meaning

    def status(self) -> dict[str, Any]:
        miss = secrets.missing(self.credentials)
        return {"id": self.id, "access": self.access, "missing_credentials": miss,
                "usable": not miss and self.collect is not None}

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "access": self.access, "credentials": self.credentials,
                "measures": self.measures, "population": self.population, "footprint": self.footprint,
                "adds_client_code": self.adds_client_code, "human_action": self.human_action,
                "human_seconds": self.human_seconds, "fields": self.fields, **self.status()}


def _client(**kw: Any) -> httpx.Client:
    return httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": USER_AGENT, **kw.pop("headers", {})},
                        **kw)


TRANSPORT: httpx.BaseTransport | None = None      # test hook (replay)


def _get(url: str, headers: dict[str, str] | None = None) -> httpx.Response:
    with _client(headers=headers or {}, transport=TRANSPORT) as c:
        return c.get(url)


def _post(url: str, body: Any, headers: dict[str, str]) -> httpx.Response:
    with _client(headers=headers, transport=TRANSPORT) as c:
        return c.post(url, json=body)


def _need(name: str) -> str:
    v = secrets.get(name)
    if not v:
        raise ConnectorBlocked("missing_credential", f"{name} is not set", credential=name)
    return v


def _apex(host: str) -> str:
    parts = host.split(".")
    if len(parts) > 2 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "ne", "or", "ac", "org", "net", "go"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# ------------------------------------------------------------------ public JSON

def collect_http_json(params: dict[str, Any]) -> Reading:
    """GET a public JSON endpoint and return the requested fields (dotted paths).
    Only GET: a discovered endpoint is never written to."""
    url = params["url"]
    r = _get(url, {"Accept": "application/json"})
    if r.status_code in (401, 403):
        raise ConnectorBlocked("access_denied", f"HTTP {r.status_code} from {url}")
    if r.status_code != 200:
        raise ConnectorBlocked("http_error", f"HTTP {r.status_code} from {url}")
    data = r.json()
    out: dict[str, Any] = {}
    for f in params.get("fields") or []:
        out[f] = json_path(data, f)
    return Reading(fields=out, url=url, status=r.status_code, raw_excerpt=r.text[:1500])


def json_path(data: Any, path: str) -> Any:
    cur = data
    for part in path.strip(".").split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        elif isinstance(cur, list) and part == "length":
            cur = len(cur)
        else:
            return None
    return cur


HTTP_JSON = Connector(
    id="http_json", title="Public JSON endpoint of the product", access="public", credentials=[],
    measures="whatever the endpoint publishes; semantics are established per field",
    population="depends on the field", footprint="read-only GET of a public endpoint the product already serves",
    collect=collect_http_json)


# ------------------------------------------------------------------ an existing service's page

def page_text(html_: str) -> str:
    from regent.acquisition.fetch import html_to_text

    return html_to_text(html_)[1]


def extract_patterns(text: str, patterns: dict[str, str]) -> dict[str, Any]:
    import re

    out: dict[str, Any] = {}
    for name, rx in (patterns or {}).items():
        m = re.search(rx, text, re.S)
        if not m:
            out[name] = None
            continue
        v = (m.group(1) if m.groups() else m.group(0)).strip()
        try:
            out[name] = float(v.replace(",", "")) if re.fullmatch(r"-?[\d,]+(?:\.\d+)?", v) else v
            if isinstance(out[name], float) and out[name].is_integer():
                out[name] = int(out[name])
        except ValueError:
            out[name] = v
    return out


def collect_html(params: dict[str, Any]) -> Reading:
    """Read what an existing service already shows people: GET the page, pull named values out of
    its visible text with the patterns Regent verified against the live page."""
    url = params["url"]
    r = _get(url, {"Accept": "text/html"})
    if r.status_code in (401, 403, 429):
        raise ConnectorBlocked("access_denied", f"HTTP {r.status_code} from {url}")
    if r.status_code != 200:
        raise ConnectorBlocked("http_error", f"HTTP {r.status_code} from {url}")
    text = page_text(r.text)
    fields = extract_patterns(text, params.get("patterns") or {})
    missing = [k for k, v in fields.items() if v is None]
    if missing and len(missing) == len(fields):
        raise ConnectorBlocked("layout_changed", f"none of {missing} found on {url}: the page changed")
    return Reading(fields=fields, url=url, status=r.status_code, raw_excerpt=text[:1500])


HTML_PAGE = Connector(
    id="html_page", title="A page of an existing service", access="public", credentials=[],
    measures="what the service already shows people", population="as defined by that service",
    footprint="read-only GET of a public page, like a person visiting it", collect=collect_html)


# ------------------------------------------------------------------ Cloudflare

CF_API = "https://api.cloudflare.com/client/v4"


def collect_cloudflare(params: dict[str, Any]) -> Reading:
    token = _need("CLOUDFLARE_API_TOKEN")
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    host = params["host"]
    zr = _get(f"{CF_API}/zones?name={quote(_apex(host))}", h)
    if zr.status_code in (401, 403):
        raise ConnectorBlocked("access_denied", f"Cloudflare rejected the token for zone lookup (HTTP {zr.status_code})",
                               credential="CLOUDFLARE_API_TOKEN")
    zones = (zr.json() or {}).get("result") or []
    if not zones:
        raise ConnectorBlocked("not_found", f"no Cloudflare zone {_apex(host)} visible to this token",
                               credential="CLOUDFLARE_API_TOKEN")
    zone = zones[0]["id"]
    until = datetime.now(timezone.utc).date()
    since = until - timedelta(days=int(params.get("days", 30)))
    q = """query($zone: String!, $since: Date!, $until: Date!) { viewer { zones(filter: {zoneTag: $zone}) {
      httpRequests1dGroups(limit: 100, filter: {date_geq: $since, date_leq: $until}, orderBy: [date_ASC]) {
        dimensions { date } sum { requests pageViews } uniq { uniques } } } } }"""
    gr = _post(f"{CF_API}/graphql", {"query": q, "variables": {"zone": zone, "since": since.isoformat(),
                                                                  "until": until.isoformat()}}, h)
    body = gr.json()
    if body.get("errors"):
        raise ConnectorBlocked("api_error", json.dumps(body["errors"])[:300], credential="CLOUDFLARE_API_TOKEN")
    groups = ((((body.get("data") or {}).get("viewer") or {}).get("zones") or [{}])[0]
              .get("httpRequests1dGroups") or [])
    daily = [{"date": g["dimensions"]["date"], "requests": g["sum"]["requests"], "page_views": g["sum"]["pageViews"],
              "uniques": g["uniq"]["uniques"]} for g in groups]
    done = [d for d in daily if d["date"] < until.isoformat()]      # today's row is incomplete
    last = done[-1] if done else {}
    fields = {"daily": daily, "uniques_last_full_day": last.get("uniques"),
              "page_views_last_full_day": last.get("page_views"),
              "uniques_7d_daily_max": max((d["uniques"] for d in done[-7:]), default=None),
              "page_views_7d": sum(d["page_views"] for d in done[-7:]) if done else None}
    return Reading(fields=fields, url=f"{CF_API}/graphql", raw_excerpt=json.dumps(daily[-3:]))


def detect_cloudflare(fp: dict[str, Any]) -> list[dict[str, Any]]:
    if "cloudflare" in fp.get("platforms", []):
        return [{"host": fp["host"]}]
    return []


CLOUDFLARE = Connector(
    id="cloudflare_analytics", title="Cloudflare zone analytics (GraphQL Analytics API)", access="credential",
    credentials=["CLOUDFLARE_API_TOKEN"],
    measures="edge requests, page views and daily unique visitor IPs for the product's domain",
    population="every client that reached the edge: people, crawlers and bots alike; 'uniques' are distinct IP "
               "addresses per day (a household behind one IP counts once, a crawler farm counts many)",
    footprint="read-only, server-side: Cloudflare already records this as the product's host; nothing changes "
              "for the product or its readers",
    human_action="Create a Cloudflare API token (dash.cloudflare.com → My Profile → API Tokens → Create Token → "
                 "'Read analytics and logs' template, zone {zone}) and paste it here",
    human_seconds=180, detect=detect_cloudflare, collect=collect_cloudflare,
    fields={"uniques_last_full_day": "distinct client IPs on the last complete UTC day",
            "page_views_last_full_day": "HTML page views on the last complete UTC day",
            "uniques_7d_daily_max": "highest daily distinct-IP count in the last 7 complete days",
            "page_views_7d": "HTML page views over the last 7 complete days", "daily": "per-day rows"})


# ------------------------------------------------------------------ Stripe

def collect_stripe(params: dict[str, Any]) -> Reading:
    key = _need("STRIPE_RESTRICTED_KEY")
    h = {"Authorization": f"Bearer {key}"}
    payers: set[str] = set()
    n, after = 0, None
    for _ in range(20):
        url = "https://api.stripe.com/v1/charges?limit=100" + (f"&starting_after={after}" if after else "")
        r = _get(url, h)
        if r.status_code in (401, 403):
            raise ConnectorBlocked("access_denied", f"Stripe rejected the key (HTTP {r.status_code})",
                                   credential="STRIPE_RESTRICTED_KEY")
        body = r.json()
        for c in body.get("data") or []:
            if c.get("livemode") is False:
                continue                         # test-mode charges are not people
            if c.get("paid") and not c.get("refunded"):
                n += 1
                who = c.get("customer") or ((c.get("billing_details") or {}).get("email") or "").lower() or c["id"]
                payers.add(str(hash(who)))           # identities are counted, never stored
        if not body.get("has_more"):
            break
        after = body["data"][-1]["id"]
    return Reading(fields={"paying_people": len(payers), "successful_charges": n},
                   url="https://api.stripe.com/v1/charges", raw_excerpt=f"{n} charges")


def detect_stripe(fp: dict[str, Any]) -> list[dict[str, Any]]:
    links = [u for u in fp.get("outbound", []) if urlparse(u).netloc.endswith("stripe.com")]
    return [{"links": links[:4]}] if links else []


STRIPE = Connector(
    id="stripe_payments", title="Stripe payments (restricted read key)", access="credential",
    credentials=["STRIPE_RESTRICTED_KEY"],
    measures="distinct paying customers (by Stripe customer, else billing email) and successful charges; "
             "live-mode charges only, test-mode charges excluded",
    population="paying customers only; a payment the operator makes to their own live account is not "
               "distinguishable from a reader's",
    footprint="read-only, server-side; identities are hashed in memory and never stored",
    human_action="Create a Stripe restricted key with read access to Charges (dashboard.stripe.com → Developers → "
                 "API keys → Create restricted key) and paste it here", human_seconds=150,
    detect=detect_stripe, collect=collect_stripe,
    fields={"paying_people": "distinct paying customers, all time", "successful_charges": "paid, unrefunded charges"})


# ------------------------------------------------------------------ Google Search Console

def collect_search_console(params: dict[str, Any]) -> Reading:
    token = _need("GOOGLE_SEARCH_CONSOLE_TOKEN")
    site = params["site"]
    end = datetime.now(timezone.utc).date() - timedelta(days=3)     # GSC data lags ~2-3 days
    start = end - timedelta(days=27)
    r = _post(f"https://www.googleapis.com/webmasters/v3/sites/{quote(site, safe='')}/searchAnalytics/query",
              {"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": ["date"]},
              {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    if r.status_code in (401, 403):
        raise ConnectorBlocked("access_denied", f"Search Console rejected the token (HTTP {r.status_code})",
                               credential="GOOGLE_SEARCH_CONSOLE_TOKEN")
    rows = r.json().get("rows") or []
    return Reading(fields={"clicks_28d": sum(x.get("clicks", 0) for x in rows),
                           "impressions_28d": sum(x.get("impressions", 0) for x in rows)},
                   url="https://www.googleapis.com/webmasters/v3/searchAnalytics/query")


def detect_search_console(fp: dict[str, Any]) -> list[dict[str, Any]]:
    if (fp.get("meta") or {}).get("google-site-verification"):
        return [{"site": f"https://{fp['host']}/"}]
    return []


SEARCH_CONSOLE = Connector(
    id="google_search_console", title="Google Search Console (site verified)", access="credential",
    credentials=["GOOGLE_SEARCH_CONSOLE_TOKEN"],
    measures="clicks from Google search results to the product (each click is a person choosing it)",
    population="people arriving from Google search only; excludes direct visits and returning readers",
    footprint="read-only; the site is already verified with Google",
    human_action="Authorize read-only Search Console access for {site} (OAuth consent as the site owner)",
    human_seconds=240, detect=detect_search_console, collect=collect_search_console,
    fields={"clicks_28d": "search clicks over 28 days", "impressions_28d": "search impressions over 28 days"})


# ------------------------------------------------------------------ analytics already in the product

def collect_plausible(params: dict[str, Any]) -> Reading:
    key = _need("PLAUSIBLE_API_KEY")
    r = _get(f"https://plausible.io/api/v1/stats/aggregate?site_id={quote(params['site_id'])}&period=30d"
             "&metrics=visitors,visits,pageviews", {"Authorization": f"Bearer {key}"})
    if r.status_code in (401, 403):
        raise ConnectorBlocked("access_denied", f"Plausible rejected the key (HTTP {r.status_code})",
                               credential="PLAUSIBLE_API_KEY")
    res = r.json().get("results") or {}
    return Reading(fields={k: (v or {}).get("value") for k, v in res.items()},
                   url="https://plausible.io/api/v1/stats/aggregate")


def detect_plausible(fp: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"site_id": fp["host"]}] if "plausible" in fp.get("analytics", []) else []


PLAUSIBLE = Connector(
    id="plausible", title="Plausible Analytics (already installed)", access="credential",
    credentials=["PLAUSIBLE_API_KEY"], measures="unique visitors, visits, page views",
    population="browsers that ran the script; bots mostly excluded", footprint="read-only; script already present",
    human_action="Create a Plausible API key (plausible.io → Settings → API Keys) and paste it here",
    human_seconds=90, detect=detect_plausible, collect=collect_plausible)


def detect_client_analytics(fp: dict[str, Any]) -> list[dict[str, Any]]:
    # Offered for every web product: *adding* a client-side analytics script. Whether that is
    # acceptable is not the connector's call -- the product's public promises decide.
    return [{"host": fp["host"]}] if fp.get("kind") == "web" else []


CLIENT_ANALYTICS = Connector(
    id="client_side_analytics", title="Add a client-side analytics script to the product", access="credential",
    credentials=["ANALYTICS_SITE_KEY"],
    measures="unique visitors and sessions as seen by a script running in each reader's browser",
    population="browsers that run the script; ad-blockers and no-JS readers are missed",
    footprint="adds third-party code to every page the product serves and sends each visit to a third party",
    human_action="Sign up for an analytics provider, add its script to the product and deploy", human_seconds=1200,
    detect=detect_client_analytics, adds_client_code=True)


# ------------------------------------------------------------------ delegated readers

def collect_script(params: dict[str, Any]) -> Reading:
    """Run a reader a worker wrote (``python <path> <source>``) in a subprocess with a timeout.
    Its output is data to be verified, like any other source."""
    import subprocess
    import sys
    from pathlib import Path

    path = Path(params["path"])
    proc = subprocess.run([sys.executable, str(path), params.get("source", "")], cwd=path.parent,
                          capture_output=True, text=True, timeout=int(params.get("timeout_s", 60)))
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    except ValueError:
        out = {}
    if proc.returncode != 0 or "fields" not in out:
        raise ConnectorBlocked("reader_failed", (out.get("error") or proc.stderr or proc.stdout or "no output")[:300])
    return Reading(fields=out["fields"], url=str(out.get("url", "")), raw_excerpt=proc.stdout[:1500])


SCRIPT = Connector(
    id="script", title="Reader written by a worker (run by Regent)", access="public", credentials=[],
    measures="whatever the reader returns; verified against Regent's own reads",
    population="depends on the field", footprint="agent-written read-only reader, run in a subprocess with a timeout",
    collect=collect_script)


CONNECTORS: dict[str, Connector] = {c.id: c for c in (HTTP_JSON, HTML_PAGE, CLOUDFLARE, STRIPE, SEARCH_CONSOLE,
                                                      PLAUSIBLE, CLIENT_ANALYTICS, SCRIPT)}


def applicable(fp: dict[str, Any]) -> list[tuple[Connector, dict[str, Any]]]:
    out = []
    for c in CONNECTORS.values():
        if c.id in ("http_json", "html_page", "script"):
            continue
        for params in c.detect(fp):
            out.append((c, params))
    return out


def collect(connector_id: str, params: dict[str, Any]) -> Reading:
    c = CONNECTORS[connector_id]
    if c.collect is None:
        raise ConnectorBlocked("not_implemented", f"{c.title} has no reader: it is an action, not a source")
    miss = secrets.missing(c.credentials)
    if miss:
        raise ConnectorBlocked("missing_credential", f"{', '.join(miss)} not provided", credential=miss[0])
    return c.collect(params)
