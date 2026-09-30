"""Reading a live product from the outside: where it runs, what it talks to, what it promises.

Pure functions over what a fetch returned (URL, body, response headers). Nothing here knows
any particular product; the knowledge is about the web platform itself: which headers a
hosting platform sets, what an analytics snippet looks like, how a single-page app names its
API, how a site states its commitments.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urljoin, urlparse

# --------------------------------------------------------------- where could it live?

TLDS = ("com", "org", "net", "io", "app", "dev", "co", "ai", "me")
#: hosting platforms that give every project a predictable default hostname
PLATFORM_SUBDOMAINS = ("pages.dev", "vercel.app", "netlify.app", "web.app", "firebaseapp.com", "fly.dev",
                       "onrender.com", "herokuapp.com", "github.io", "glitch.me", "replit.app", "surge.sh")


def name_tokens(name: str) -> list[str]:
    n = unicodedata.normalize("NFKC", name).strip()
    n = re.sub(r"([a-z])([A-Z])", r"\1 \2", n)
    return [t for t in re.split(r"[^0-9A-Za-z]+", n.lower()) if t]


def slugs(name: str) -> list[str]:
    toks = name_tokens(name)
    if not toks:
        return []
    joined, hyph = "".join(toks), "-".join(toks)
    out = [joined, hyph, f"get{joined}", f"{joined}app"]
    return list(dict.fromkeys(s for s in out if 2 < len(s) <= 63))


def candidate_urls(name: str, *, country: str | None = None, accounts: list[str] | None = None) -> list[str]:
    """Hostnames a product called ``name`` would plausibly be served from, most likely first."""
    tlds = list(TLDS) + ([country.lower()] if country and country.lower() not in TLDS else [])
    out: list[str] = []
    for i, s in enumerate(slugs(name)):
        for t in (tlds if i < 2 else tlds[:3]):
            out.append(f"https://{s}.{t}/")
        if i < 2:
            out += [f"https://{s}.{p}/" for p in PLATFORM_SUBDOMAINS]
            for a in accounts or []:
                out.append(f"https://{a}.github.io/{s}/")
    return list(dict.fromkeys(out))


def normalized(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", unicodedata.normalize("NFKC", s).lower())


def names_subject(name: str, title: str, text: str) -> float:
    """How strongly a page presents itself as ``name`` (0..1)."""
    key = normalized(name)
    if not key:
        return 0.0
    if key in normalized(title):
        return 1.0
    head = normalized(text[:3000])
    if key in head:
        return 0.7
    return 0.3 if key in normalized(text) else 0.0


# ------------------------------------------------------------------- fingerprint

PLATFORM_HEADERS: list[tuple[str, str, str]] = [
    # (header, value-regex ('' = present), platform)
    ("server", r"cloudflare", "cloudflare"), ("cf-ray", "", "cloudflare"),
    ("x-vercel-id", "", "vercel"), ("server", r"vercel", "vercel"),
    ("x-nf-request-id", "", "netlify"), ("server", r"netlify", "netlify"),
    ("x-github-request-id", "", "github_pages"), ("server", r"github\.com", "github_pages"),
    ("fly-request-id", "", "fly"), ("x-render-origin-server", "", "render"),
    ("x-amz-cf-id", "", "cloudfront"), ("x-served-by", r"cache-", "fastly"), ("via", r"vegur", "heroku"),
    ("x-firebase-hosting", "", "firebase"), ("server", r"google frontend", "google_cloud"),
]

ANALYTICS_SIGNATURES: list[tuple[str, str]] = [
    (r"googletagmanager\.com|google-analytics\.com|\bgtag\(", "google_analytics"),
    (r"plausible\.io/js|data-domain=.{0,80}plausible", "plausible"),
    (r"static\.cloudflareinsights\.com|cloudflareinsights\.com/beacon", "cloudflare_web_analytics"),
    (r"umami\.(?:is|js)|data-website-id", "umami"), (r"posthog", "posthog"), (r"mixpanel", "mixpanel"),
    (r"cdn\.segment\.com|analytics\.js", "segment"), (r"matomo\.js|piwik\.js|_paq\.push", "matomo"),
    (r"usefathom\.com", "fathom"), (r"simpleanalyticscdn|scripts\.simpleanalytics", "simple_analytics"),
    (r"clarity\.ms", "microsoft_clarity"), (r"hotjar\.com", "hotjar"), (r"/_vercel/insights", "vercel_analytics"),
    (r"goatcounter\.com", "goatcounter"), (r"statcounter\.com", "statcounter"), (r"connect\.facebook\.net", "meta_pixel"),
]

PROMISE_PATTERNS = [
    r"no (?:ads|advertising|tracking|trackers|accounts?|sign[- ]?ups?|cookies|analytics|paywalls?)",
    r"(?:we|never) (?:do not|don't|never) (?:track|sell|collect)", r"without (?:tracking|ads|an account)",
    r"free forever", r"privacy[- ]first", r"not tracked",
    r"追跡(?:なし|しない|しません)", r"広告(?:なし|なし・|を表示しない)", r"アカウント(?:不要|なし)", r"トラッキング(?:なし|しない)",
    r"永久に無料|ずっと無料", r"kein(?:e)? (?:tracking|werbung)", r"sin (?:anuncios|rastreo)", r"sans (?:pub|pistage)",
]

_TAG_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.S | re.I)
_ATTR = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"|([\w:-]+)\s*=\s*\'([^\']*)\'')
_META = re.compile(r"<meta\b[^>]*>", re.I)
_LINK = re.compile(r"<link\b[^>]*>", re.I)
_HREF = re.compile(r"""(?:href|src|action)\s*=\s*["']([^"']+)["']""", re.I)
_ABS = re.compile(r"""https?://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s"'<>)\\]*)?""")
_API_PATH = re.compile(r"""["'`](/(?:api|v\d|graphql|rest)\b[A-Za-z0-9_./:-]*)""")
_BASE_VAR = re.compile(r"""\b([A-Z_][A-Z0-9_]{1,20}|api(?:Base|Url|URL|_url|_base)?)\s*=\s*["'](https?://[^"']+?)/?["']""")
_POST_CTX = re.compile(r"""["'`](/(?:api|v\d)[A-Za-z0-9_./:-]*)["'`]\s*,\s*\{\s*method\s*:\s*["'](POST|PUT|PATCH|DELETE)""",
                       re.I)


def _attrs(s: str) -> dict[str, str]:
    out = {}
    for m in _ATTR.finditer(s):
        k = (m.group(1) or m.group(3) or "").lower()
        out[k] = m.group(2) if m.group(1) else m.group(4)
    return out


def parse_csp(value: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in (value or "").split(";"):
        bits = part.strip().split()
        if bits:
            out[bits[0].lower()] = bits[1:]
    return out


def fingerprint(url: str, html: str, headers: dict[str, str], text: str = "") -> dict[str, Any]:
    """Everything observable about a deployment from one response."""
    host = urlparse(url).netloc
    h = {k.lower(): v for k, v in (headers or {}).items()}
    platforms: list[str] = []
    evidence: dict[str, str] = {}
    for name, rx, plat in PLATFORM_HEADERS:
        if name in h and (not rx or re.search(rx, h[name], re.I)) and plat not in platforms:
            platforms.append(plat)
            evidence[f"platform:{plat}"] = f"{name}: {h[name][:80]}"
    analytics = []
    for rx, name in ANALYTICS_SIGNATURES:
        m = re.search(rx, html, re.I)
        if m and name not in analytics:
            analytics.append(name)
            evidence[f"analytics:{name}"] = html[max(0, m.start() - 60):m.end() + 60]
    metas: dict[str, str] = {}
    for m in _META.finditer(html):
        a = _attrs(m.group(0))
        k = (a.get("name") or a.get("property") or "").lower()
        if k and a.get("content"):
            metas[k] = a["content"][:300]
    manifest = None
    for m in _LINK.finditer(html):
        a = _attrs(m.group(0))
        if "manifest" in (a.get("rel") or "").lower() and a.get("href"):
            manifest = urljoin(url, a["href"])
    csp = parse_csp(h.get("content-security-policy", ""))
    script_srcs = [urljoin(url, _attrs(m.group(1)).get("src", "")) for m in _TAG_SCRIPT.finditer(html)
                   if _attrs(m.group(1)).get("src")]
    outbound = sorted({u.rstrip(".,;") for u in _ABS.findall(html) if urlparse(u).netloc and urlparse(u).netloc != host})
    sw = None
    msw = re.search(r"serviceWorker\.register\(\s*[\"']([^\"']+)", html)
    if msw:
        sw = urljoin(url, msw.group(1))
    endpoints = api_endpoints(url, html, csp)
    stated = " ".join(v for k, v in metas.items() if "description" in k or k in ("keywords", "og:title"))
    promises = find_promises(stated + "\n" + (text or html))
    title = ""
    mt = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if mt:
        title = re.sub(r"\s+", " ", mt.group(1)).strip()[:200]
    return {
        "url": url, "host": host, "kind": "web", "title": title, "platforms": platforms, "analytics": analytics,
        "meta": metas, "manifest": manifest, "service_worker": sw, "csp": csp, "scripts": script_srcs[:30],
        "outbound": outbound[:120], "endpoints": endpoints, "promises": promises, "evidence": evidence,
        "tracking_free": not analytics,
        "csp_connect": [s for s in csp.get("connect-src", []) if s.startswith("http")],
    }


def api_endpoints(url: str, code: str, csp: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """API endpoints an app's code refers to, joined to the bases it uses; each marked with the
    HTTP methods the code uses it with (Regent only ever reads with GET)."""
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    bases: list[str] = []       # the API bases the code names first; same-origin last (SPAs answer anything)
    for m in _BASE_VAR.finditer(code):
        b = m.group(2).rstrip("/")
        if urlparse(b).netloc and b not in bases:
            bases.append(b)
    for s in (csp or {}).get("connect-src", []):
        if s.startswith("https://") and "*" not in s and s.rstrip("/") not in bases:
            bases.append(s.rstrip("/"))
    bases.append(origin)
    writes: dict[str, set[str]] = {}
    for m in _POST_CTX.finditer(code):
        writes.setdefault(m.group(1).split("?")[0], set()).add(m.group(2).upper())
    paths: dict[str, dict[str, Any]] = {}
    for m in _API_PATH.finditer(code):
        p = m.group(1).split("?")[0]
        if len(p) < 4 or p.endswith((".js", ".css", ".png", ".svg")):
            continue
        e = paths.setdefault(p, {"path": p, "mentions": 0, "context": []})
        e["mentions"] += 1
        if len(e["context"]) < 2:
            e["context"].append(re.sub(r"\s+", " ", code[max(0, m.start() - 160):m.end() + 160]))
    # absolute API URLs written out in full
    for u in _ABS.findall(code):
        pu = urlparse(u)
        if re.match(r"/(?:api|v\d|graphql)\b", pu.path or ""):
            base = f"{pu.scheme}://{pu.netloc}"
            if base not in bases:
                bases.append(base)
            e = paths.setdefault(pu.path, {"path": pu.path, "mentions": 0, "context": []})
            e["mentions"] += 1
            e.setdefault("absolute_base", base)
    out = []
    for p, e in paths.items():
        out.append({**e, "writes": sorted(writes.get(p, set())), "bases": [e["absolute_base"]] if e.get(
            "absolute_base") else bases, "parametric": p.endswith("/") or ":" in p})
    return sorted(out, key=lambda x: -x["mentions"])


def find_promises(text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen = set()
    for rx in PROMISE_PATTERNS:
        for m in re.finditer(rx, text, re.I):
            span = text[max(0, m.start() - 80):m.end() + 80]
            span = re.sub(r"<[^>]+>", " ", span)
            span = re.sub(r"\s+", " ", span).strip()
            k = m.group(0).lower()
            if k in seen:
                continue
            seen.add(k)
            out.append({"phrase": m.group(0), "context": span[:240]})
            if len(out) >= 12:
                return out
    return out


def json_shape(data: Any, prefix: str = "", depth: int = 0) -> dict[str, str]:
    """Dotted paths of a JSON document with their types (lists summarized by their first item)."""
    out: dict[str, str] = {}
    if depth > 4:
        return out
    if isinstance(data, dict):
        for k, v in list(data.items())[:60]:
            p = f"{prefix}.{k}" if prefix else str(k)
            out[p] = type(v).__name__ if not isinstance(v, list) else f"list[{len(v)}]"
            if isinstance(v, (dict, list)):
                out.update(json_shape(v, p, depth + 1))
    elif isinstance(data, list) and data:
        out.update(json_shape(data[0], f"{prefix}.0", depth + 1))
    return out
