"""Source discovery and the learned source registry.

Regent does not need a hardcoded portal for every place it might look. For a (domain,
country, city) it assembles candidate sources from several channels, verifies each one by
*using* it, and remembers what worked:

* ``registry``  -- sources verified earlier (and country-pack seeds) are reused first;
* ``search``    -- a web-search API when configured (``REGENT_SEARCH_API_KEY``);
* ``authority`` -- official guidance pages for the country, whose outbound links often
  name the local sources;
* ``snowball``  -- outbound links from housing sources already verified in that country
  (portals link to partner portals and aggregators);
* ``probe``     -- domains formed from the local language's words for the domain and the
  country's top-level domain (e.g. "pisos" + ".com", "realestate" + ".co.nz"), each checked.

A candidate becomes ``verified`` only when navigation from its entry page reaches pages
the domain extractor turns into records for the requested city; otherwise it is kept as
``rejected``/``blocked`` with the reason -- so the next run does not waste pages on it.
Blocks (robots, 403/405/429, CAPTCHA, bot challenges) are recorded, never bypassed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy import select

from regent.acquisition.navigate import anchors, find_links, next_page
from regent.acquisition.tables import AcqSourceRecipe
from regent.acquisition.types import SourceSpec
from regent.ids import utcnow

COUNTRY_TLDS = {"GB": [".co.uk", ".com"], "US": [".com"], "DE": [".de"], "ES": [".es", ".com"], "NZ": [".co.nz"],
                "AU": [".com.au"], "IE": [".ie"], "NL": [".nl"], "FR": [".fr", ".com"], "IT": [".it"],
                "PT": [".pt"], "CA": [".ca"], "AT": [".at"], "CH": [".ch"], "BE": [".be"], "JP": [".jp", ".co.jp"],
                "SG": [".sg", ".com.sg"], "SE": [".se"], "DK": [".dk"], "NO": [".no"], "PL": [".pl"], "CZ": [".cz"]}
IGNORED_HOSTS = re.compile(r"(facebook|twitter|instagram|youtube|linkedin|google|apple|tiktok|pinterest|x\.com|"
                           r"doubleclick|onelink|app\.link|go\.link|cookie|consent|gov\.|\.gov|wikipedia|"
                           r"maps\.|apps\.|play\.|itunes|signin|login|help\.|faq\.|blog\.|careers|jobs\.)", re.I)


def recipe_id(domain: str, scope: str, host: str) -> str:
    return f"{domain}:{scope}:{host}"[:160]


@dataclass
class DomainVocabulary:
    """What a domain looks like on the web, per language (supplied by the domain adapter)."""

    nav_words: dict[str, list[str]]            # language -> anchor words leading to listings ("to rent", "mieten")
    avoid_words: list[str]                     # URL fragments to avoid ("for-sale", "kaufen")
    stems: dict[str, list[str]]                # language -> words people build site names from ("rent", "pisos")
    verify: Callable[[Any, str, str], int]     # (doc, country, city) -> number of domain records on the page
    kind: str = "portal"
    city_share: Callable[[Any, str], float] | None = None   # (doc, city) -> share of records located in city


@dataclass
class Candidate:
    host: str
    entry_url: str
    channel: str
    evidence: dict[str, Any] = field(default_factory=dict)


class SourceRegistry:
    def __init__(self, db, domain: str):
        self.db = db
        self.domain = domain

    def seed(self, scope: str, specs: list[SourceSpec], origin: str) -> None:
        for s in specs:
            rid = recipe_id(self.domain, scope, s.host)
            if self.db.get(AcqSourceRecipe, rid) is None:
                self.db.add(AcqSourceRecipe(id=rid, domain=self.domain, host=s.host, scope=scope, kind=s.kind,
                                            entry_url=s.entry_url, nav=list(s.nav), render=s.render, origin=origin,
                                            status="candidate", hints={}, evidence={"purpose": s.purpose}))
        self.db.flush()

    def usable(self, scope: str) -> list[AcqSourceRecipe]:
        rows = self.db.scalars(select(AcqSourceRecipe).where(
            AcqSourceRecipe.domain == self.domain, AcqSourceRecipe.scope == scope,
            AcqSourceRecipe.status.in_(("candidate", "verified"))).order_by(AcqSourceRecipe.created_at, AcqSourceRecipe.id))
        return sorted(rows, key=lambda r: (r.status != "verified", -(r.records or 0), r.failures or 0))

    def known_hosts(self) -> set[str]:
        return {r.host for r in self.db.scalars(select(AcqSourceRecipe).where(AcqSourceRecipe.domain == self.domain))}

    def record(self, rid: str, *, ok: bool, records: int = 0, city: str | None = None, trail: list[str] | None = None,
               status: str | None = None, note: str | None = None) -> None:
        r = self.db.get(AcqSourceRecipe, rid)
        if r is None:
            return
        r.uses = (r.uses or 0) + 1
        r.last_used_at = utcnow()
        if ok:
            r.records = (r.records or 0) + records
            r.status = "verified"
            if city and trail:
                r.evidence = {**(r.evidence or {}), "trails": {**(r.evidence or {}).get("trails", {}), city: trail}}
        else:
            r.failures = (r.failures or 0) + 1
            if status:
                r.status = status
        if note:
            r.evidence = {**(r.evidence or {}), "last_note": note[:300]}
        self.db.flush()


class SourceDiscovery:
    def __init__(self, engine, domain: str, vocab: DomainVocabulary):
        self.engine = engine
        self.registry = SourceRegistry(engine.db, domain)
        self.domain = domain
        self.vocab = vocab

    # ----------------------------------------------------------- channels

    def candidates(self, country: str, city: str, languages: list[str], authority_urls: list[str],
                   max_probe: int = 10) -> list[Candidate]:
        known = self.registry.known_hosts()
        seen: set[str] = set()
        out: list[Candidate] = []

        def add(c: Candidate) -> None:
            if c.host in known or c.host in seen or IGNORED_HOSTS.search(c.host):
                return
            seen.add(c.host)
            out.append(c)

        for c in self._search(country, city, languages):
            add(c)
        for c in self._outbound(authority_urls, "authority"):
            add(c)
        verified = [r.entry_url for r in self.registry.usable(country) if r.status == "verified"]
        for c in self._outbound(verified[:3], "snowball"):
            add(c)
        for c in self._probe(country, languages)[:max_probe]:
            add(c)
        return out

    def _search(self, country: str, city: str, languages: list[str]) -> list[Candidate]:
        from regent.config import settings

        if not settings.search_api_key:
            self.engine.log("discovery", "search channel unavailable: REGENT_SEARCH_API_KEY not set")
            return []
        from regent.connectors.services import BraveSearch

        out = []
        for lang in (languages or ["en"])[:2]:
            words = self.vocab.nav_words.get(lang) or self.vocab.nav_words.get("en", [])
            q = f"{words[0] if words else ''} {city}".strip()
            try:
                for h in BraveSearch().search(q, 10):
                    host = urlparse(h["url"]).netloc
                    out.append(Candidate(host, f"https://{host}/", "search", {"query": q, "title": h.get("title")}))
            except Exception as e:
                self.engine.log("discovery", f"search failed: {type(e).__name__}: {e}"[:200])
        return out

    def _outbound(self, urls: list[str], channel: str) -> list[Candidate]:
        out = []
        for u in urls:
            if not self.engine.budget_left():
                break
            doc = self.engine.fetch(u, purpose="discovery", kind="public_data" if channel == "authority" else "portal",
                                    render="static")
            if doc is None or not doc.ok:
                continue
            base = urlparse(doc.final_url).netloc
            for text, link in anchors(doc.html, doc.final_url):
                host = urlparse(link).netloc
                if host and host != base and not host.endswith("." + base.removeprefix("www.")):
                    out.append(Candidate(host, f"https://{host}/", channel, {"linked_from": doc.final_url,
                                                                           "anchor": text[:60]}))
        return out

    def _probe(self, country: str, languages: list[str]) -> list[Candidate]:
        tlds = COUNTRY_TLDS.get(country, [f".{country.lower()}", ".com"])
        out = []
        for lang in languages or ["en"]:
            for stem in self.vocab.stems.get(lang, []):
                for tld in tlds:
                    host = f"www.{stem}{tld}"
                    out.append(Candidate(host, f"https://{host}/", "probe", {"stem": stem, "tld": tld, "lang": lang}))
        return out

    # ---------------------------------------------------------- verification

    def verify(self, c: Candidate, country: str, city: str, languages: list[str], render: str = "auto") -> dict[str, Any]:
        """Use the candidate: reach city listings by navigation and count extracted records."""
        e = self.engine
        doc = e.fetch(c.entry_url, purpose="discovery", kind=self.vocab.kind, render="static")
        if doc is None:
            return {"host": c.host, "status": "skipped", "why": "budget"}
        if doc.ok and urlparse(doc.final_url).netloc and urlparse(doc.final_url).netloc != c.host:
            # the probed domain redirects (alquiler.es -> fotocasa.es): the source is the final site
            c = Candidate(urlparse(doc.final_url).netloc, doc.final_url, c.channel,
                          {**c.evidence, "reached_via": c.entry_url})
            if c.host in self.registry.known_hosts():
                return {"host": c.host, "status": "known", "why": f"already registered (reached via "
                                                                  f"{c.evidence['reached_via']})"}
        rid = recipe_id(self.domain, country, c.host)
        status, why = None, None
        if not doc.ok:
            status = "blocked" if doc.blocked else "rejected"
            why = (doc.blocked or {}).get("detail") if doc.blocked else (doc.error or f"HTTP {doc.status}")
        result = {"host": c.host, "channel": c.channel}
        n, trail = 0, []
        if status is None:
            found, trail = self.navigate_to_listings(c.entry_url, city, languages, render=render)
            n = self.vocab.verify(found, country, city) if found is not None else 0
            if n < 3:
                status, why = "rejected", f"no {city} listings reachable by navigation ({n} records)"
        self.engine.db.merge(AcqSourceRecipe(
            id=rid, domain=self.domain, host=c.host, scope=country, kind=self.vocab.kind, entry_url=c.entry_url,
            nav=["{city}"], render=render, origin=f"discovered:{c.channel}", status=status or "verified",
            hints={}, evidence={**c.evidence, "why": why, "trails": {city: trail} if not status else {}},
            records=n, uses=1, failures=0 if not status else 1, last_used_at=utcnow()))
        self.engine.db.flush()
        result.update({"status": status or "verified", "why": why, "records": n, "trail": trail})
        return result

    def nav_words(self, languages: list[str]) -> list[str]:
        words: list[str] = []
        for lang in (languages or []) + ["en"]:
            for w in self.vocab.nav_words.get(lang, []):
                if w not in words:
                    words.append(w)
        return words

    def navigate_to_listings(self, entry_url: str, city: str, languages: list[str], *, render: str = "auto",
                             kind: str | None = None, max_steps: int = 3, beam: int = 3, admin: str | None = None):
        """Beam search over anchors toward listing pages for ``city``: city names and the local
        words for renting pull the search forward; sale/buy paths are avoided. Stops at the first
        page the domain extractor turns into records for that city."""
        e = self.engine
        words = self.nav_words(languages)
        avoid = self.vocab.avoid_words
        start = e.fetch(entry_url, purpose="navigate", kind=kind or self.vocab.kind, render=render)
        if start is None or not start.ok:
            return None, [entry_url]
        frontier = [(start, [start.final_url])]
        seen = {start.final_url}
        for _ in range(max_steps + 1):
            nxt = []
            for d, trail in frontier:
                # ambiguous names ("Washington", "London") must also match the admin area (state/province)
                if not any(a in (d.final_url or "").lower() for a in avoid) and \
                        self.vocab.verify(d, "", city) >= 3 and self._in_city(d, city) and \
                        (not admin or _mentions_city(d, admin, url_only=True)):
                    return d, trail
            for d, trail in frontier:
                cands: list[tuple[float, str]] = []
                admin_hint = [_slug(admin)] if admin else []
                for i, u in enumerate(find_links(d.html, d.final_url, city, path_hints=words + admin_hint,
                                                 avoid=avoid)[:beam]):
                    cands.append((10 - i + _rent_bonus(u, words, avoid) + (4 if admin and _slug(admin) in u.lower()
                                                                             else 0), u))
                if admin:
                    for u in find_links(d.html, d.final_url, admin, path_hints=words, avoid=avoid)[:1]:
                        cands.append((8 + _rent_bonus(u, words, avoid), u))
                for w in words[:6]:
                    for u in find_links(d.html, d.final_url, w, path_hints=[city.lower()], avoid=avoid)[:1]:
                        cands.append((4 + _rent_bonus(u, words, avoid) + (3 if _slug(city) in u.lower() else 0), u))
                for _, u in sorted(cands, key=lambda x: -x[0])[:beam]:
                    if u in seen or not e.budget_left():
                        continue
                    seen.add(u)
                    d2 = e.fetch(u, purpose="navigate", kind=kind or self.vocab.kind, render=render)
                    if d2 is not None and d2.ok:
                        nxt.append((d2, trail + [d2.final_url]))
            if not nxt:
                break
            frontier = nxt[:beam]
        return None, [entry_url]

    def _in_city(self, doc, city: str) -> bool:
        """The page is this city's listings: the city is in its URL, or in a good share of its records
        (a nationwide page that merely mentions the city somewhere does not count)."""
        if _mentions_city(doc, city, url_only=True):
            return True
        if self.vocab.city_share is not None:
            return self.vocab.city_share(doc, city) >= 0.4
        return _mentions_city(doc, city)

    @staticmethod
    def paginate(doc) -> str | None:
        return next_page(doc.html, doc.final_url)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _rent_bonus(url: str, words: list[str], avoid: list[str]) -> float:
    u = url.lower()
    b = sum(2.0 for w in words if _slug(w) and _slug(w) in u)
    b -= sum(6.0 for a in avoid if a in u)
    return b


def _mentions_city(doc, city: str, url_only: bool = False) -> bool:
    c = city.lower()
    url = (doc.final_url or "").lower()
    if c in url or _slug(city) in url or c.replace(" ", "") in url:
        return True
    return not url_only and c in (doc.text or "")[:20000].lower()
