"""Geography is part of the search space.

Given a housing need, the resolver decides *where* to look instead of assuming the
principal's current country:

1. **Principal evidence** -- world facts (``principal.country``, ``principal.citizenship``,
   ``principal.stay_in_country``, ``principal.regions``) and weak signals such as the
   language the mission was written in. Evidence about where the principal *is* is not a
   decision about where they *should live*; it only changes the prior on the right to stay
   and the cost of moving.
2. **Candidate world** -- cities acquired from public directories: global rental
   aggregators' city lists (with live listing counts), a worldwide classifieds site list,
   and each country pack's own region list. Nothing is hand-picked.
3. **Screening** -- geocode (Open-Meteo/GeoNames: coordinates, population), score on
   supply, size, source coverage and diversity, keep a shortlist.
4. **Price signal** -- one live page per shortlisted city (the pack's market page or a
   global aggregator's city page), normalized to monthly rent in a reference currency with
   ECB exchange rates.
5. **Choice** -- a transparent utility (affordability, supply, coverage, stay prior,
   relocation distance) with diversity constraints picks the regions to acquire in depth.
   Every region, chosen or not, is recorded with its evidence and the reason.
"""

from __future__ import annotations

import math
import re
import statistics
from typing import Any
from urllib.parse import quote

from sqlalchemy import select

from regent.acquisition.housing import locale as L
from regent.acquisition.housing.packs.seeds import AUTHORITY, COUNTRY_NAMES, COUNTRY_SEEDS
from regent.acquisition.navigate import anchors
from regent.acquisition.tables import AcqEntity, AcqSourceRecipe
from regent.acquisition.types import ClaimIn
from regent.ids import new_id

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
GEOCODER = "https://geocoding-api.open-meteo.com/v1/search?name={q}&count=3&language={lang}"
DIRECTORIES = [
    ("housinganywhere.com", "https://housinganywhere.com/", r"housinganywhere\.com/s/([^/?#]+)--([^/?#]+)$"),
    ("www.craigslist.org", "https://www.craigslist.org/about/sites", None),
    ("www.spotahome.com", "https://www.spotahome.com/", r"spotahome\.com/s/([a-z-]+)$"),
]
US_STATES = {"alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
             "district of columbia", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
             "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
             "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "new york",
             "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
             "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "washington",
             "west virginia", "wisconsin", "wyoming", "territories"}
CA_PROVINCES = {"alberta", "british columbia", "manitoba", "new brunswick", "newfoundland and labrador",
                "nova scotia", "ontario", "prince edward island", "quebec", "saskatchewan", "yukon",
                "northwest territories", "nunavut"}
NAME_TO_CC = {v.lower(): k for k, v in COUNTRY_NAMES.items()}
NAME_TO_CC.update({"england": "GB", "czech-republic": "CZ", "the netherlands": "NL", "uk": "GB", "usa": "US"})
_KANA = re.compile(r"[぀-ヿ]")
_CJK = re.compile(r"[一-鿿]")
_HANGUL = re.compile(r"[가-힯]")


def haversine_km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def language_of(text: str) -> str | None:
    if _KANA.search(text or ""):
        return "ja"
    if _HANGUL.search(text or ""):
        return "ko"
    if _CJK.search(text or ""):
        return "zh"
    return "en" if re.search(r"[A-Za-z]{3}", text or "") else None


LANG_HOME = {"ja": "JP", "ko": "KR"}     # languages spoken essentially in one country


class GeographyResolver:
    def __init__(self, adapter):
        self.adapter = adapter

    # ------------------------------------------------------------ principal

    def principal(self, params: dict[str, Any]) -> dict[str, Any]:
        """What is known about the principal's location/status -- as evidence, not a decision."""
        p = {"home": None, "home_confidence": 0.0, "home_evidence": None, "citizenship": params.get("citizenship"),
             "stay_in_country": params.get("stay_in_country"), "languages": params.get("languages") or []}
        if params.get("country"):
            p.update(home=str(params["country"]).upper(), home_confidence=0.95,
                     home_evidence="principal.country stated")
        else:
            lang = language_of(params.get("mission_text", ""))
            if lang and lang not in p["languages"]:
                p["languages"] = [lang] + list(p["languages"])
            if lang in LANG_HOME:
                p.update(home=LANG_HOME[lang], home_confidence=0.6,
                         home_evidence=f"mission written in '{lang}' (weak signal of current country)")
        return p

    # ------------------------------------------------------------ public data

    def fx(self, engine) -> dict[str, float]:
        """ECB reference rates: units of currency per 1 EUR (EUR=1)."""
        doc = engine.fetch(ECB_URL, purpose="market", kind="public_data", render="static")
        rates = {"EUR": 1.0}
        if doc is not None and doc.ok:
            for cur, r in re.findall(r"currency='(\w+)'\s+rate='([\d.]+)'", doc.html):
                rates[cur] = float(r)
        return rates

    def geocode(self, engine, name: str, country: str | None, lang: str = "en") -> dict[str, Any] | None:
        tries = [name]
        if lang == "ja" and re.search(r"[都府県]$", name) and not name.endswith("都"):
            tries.append(name[:-1] + "市")                       # 大阪府 -> 大阪市 (the prefecture's main city)
        for q in tries:
            data = engine.fetcher.get_json(GEOCODER.format(q=quote(q), lang=lang))
            for r in (data or {}).get("results") or []:
                if country and r.get("country_code") != country:
                    continue
                if r.get("feature_code", "").startswith("PPL"):
                    return {"lat": r["latitude"], "lon": r["longitude"], "population": r.get("population"),
                            "country": r.get("country_code"), "matched": r.get("name"), "query": q,
                            "admin1": r.get("admin1"),
                            "url": GEOCODER.format(q=quote(q), lang=lang)}
        return None

    # ------------------------------------------------------------ candidates

    def candidates(self, engine, packs: dict[str, Any]) -> list[dict[str, Any]]:
        pool: dict[tuple[str, str], dict[str, Any]] = {}

        def add(name: str, cc: str | None, origin: str, supply: int | None = None, url: str | None = None,
                lang: str = "en") -> None:
            if not name:
                return
            if not cc:   # directory without countries: attach to a city another directory placed -- if unique
                ccs = {k[0] for k in pool if k[1] == name.lower()}
                if len(ccs) != 1:
                    return          # "London" is in GB and CA: ambiguous evidence is not attached anywhere
                cc = ccs.pop()
            key = (cc, name.lower())
            c = pool.setdefault(key, {"name": name, "country": cc, "origins": [], "supply": 0, "urls": {},
                                      "lang": lang})
            if origin not in c["origins"]:
                c["origins"].append(origin)
            if supply:
                c["supply"] = max(c["supply"], supply)
            if url:
                c["urls"][origin] = url

        for host, entry, pat in DIRECTORIES:
            if not engine.budget_left():
                break
            doc = engine.fetch(entry, purpose="discovery", kind="portal", render="static")
            if doc is None or not doc.ok:
                engine.log("geography", f"directory {host} unavailable ({_why(doc)})")
                continue
            n0 = len(pool)
            if pat:
                for text, url in anchors(doc.html, doc.final_url):
                    m = re.search(pat, url)
                    if not m:
                        continue
                    city = m.group(1).replace("-", " ").title()
                    cc = NAME_TO_CC.get(m.group(2).replace("-", " ").lower()) if m.lastindex and m.lastindex > 1 \
                        else _spotahome_country(city)
                    sup = re.search(r"(\d[\d,.]*)\s*(?:listings|properties)", text.replace("+", " "), re.I)
                    add(city, cc, host, int(re.sub(r"\D", "", sup.group(1))) if sup else None, url)
            else:
                for cc, city, url, admin in _craigslist_sites(doc.html):
                    add(city, cc, host, None, url)
                    if admin:
                        pool[(cc, city.lower())].setdefault("admin", admin)
            engine.log("geography", f"directory {host}: {len(pool) - n0} new candidate cities")
        for cc, pack in packs.items():
            try:
                for c in pack.city_candidates(engine):
                    add(c["name"], cc, f"pack:{cc}", c.get("supply"), None, lang=pack.languages[0])
            except Exception as e:  # a pack's directory failing never blocks geography
                engine.log("geography", f"pack {cc} candidates failed: {type(e).__name__}: {e}"[:200])
        return list(pool.values())

    # --------------------------------------------------------------- resolve

    def resolve(self, engine, params: dict[str, Any], packs: dict[str, Any], pack_for) -> list[dict[str, Any]]:
        prin = self.principal(params)
        ref = params.get("ref_currency") or (L.COUNTRIES.get(prin["home"] or "", ("USD",))[0])
        engine.log("geography", f"principal evidence: home={prin['home']} ({prin['home_evidence'] or 'unknown'}), "
                                f"citizenship={prin['citizenship'] or 'unknown'}; reference currency {ref}")
        if params.get("regions"):          # the principal named places: respect that
            chosen = [dict(r) for r in params["regions"]]
            for r in chosen:
                r.setdefault("country", prin["home"] or "JP")
            return self._materialize(engine, chosen, prin, ref, self.fx(engine), rationale="principal-specified regions",
                                     rejected=[])
        fx = self.fx(engine)
        pool = self.candidates(engine, {})
        if prin["home"] and not any(c["country"] == prin["home"] for c in pool):
            pool += [{"name": c["name"], "country": prin["home"], "origins": ["pack"], "supply": 0, "urls": {},
                      "lang": pack_for(prin["home"]).languages[0]} for c in pack_for(prin["home"]).city_candidates(engine)]
        if prin["stay_in_country"] and prin["home"]:
            pool = [c for c in pool if c["country"] == prin["home"]]
        engine.log("geography", f"candidate world: {len(pool)} cities in {len({c['country'] for c in pool})} countries")
        if not pool:
            return []
        # screening, level 1 -- countries: source coverage, directory supply, home evidence
        coverage = self._coverage(engine)
        by_cc: dict[str, list[dict]] = {}
        for c in pool:
            by_cc.setdefault(c["country"], []).append(c)
        cscore = {cc: 1.5 * min(coverage.get(cc, 0), 4) + math.log1p(max(c["supply"] for c in cs))
                  + 0.5 * min(len(cs), 10) / 10 + (3.0 if cc == prin["home"] else 0) for cc, cs in by_cc.items()}
        # diversity: the best country of every continent first, then the next best overall
        ranked = sorted(cscore, key=lambda cc: -cscore[cc])
        firsts, seen_ct = [], set()
        for cc in ranked:
            ct = L.CONTINENT.get(cc, "Other")
            if ct not in seen_ct:
                seen_ct.add(ct)
                firsts.append(cc)
        countries = (firsts + [cc for cc in ranked if cc not in firsts])[:int(params.get("shortlist_countries", 8))]
        engine.log("geography", "countries screened: " + ", ".join(f"{cc} {cscore[cc]:.1f}" for cc in countries))
        # level 2 -- cities inside each country: supply, salience on the country's own portals, population
        short = []
        for cc in countries:
            cs = by_cc[cc]
            sal = self._salience(engine, cc, cs, pack_for)
            for c in cs:
                c["coverage"] = coverage.get(cc, 0) + len(c["origins"])
                c["salience"] = sal.get(c["name"].lower(), 0)
            cs.sort(key=lambda c: -(math.log1p(c["supply"]) + 2 * c["salience"] + len(c["origins"])))
            n_geo = 8 if cc == prin["home"] else 3
            for c in cs[:n_geo]:
                if not engine.budget_left():
                    break
                c["geo"] = self.geocode(engine, c["name"], cc, pack_for(cc).languages[0] if cc == "JP" else "en")
            ranked = sorted((c for c in cs[:n_geo] if c.get("geo")),
                            key=lambda c: -(math.log1p((c["geo"] or {}).get("population") or 0)
                                            + math.log1p(c["supply"]) + c["salience"]))
            short += ranked[:2 if cc == prin["home"] else 1]
        # price signal for the shortlist
        for c in short:
            if not engine.budget_left():
                break
            g = c.get("geo") or {}
            c["nav_name"] = g.get("admin1") if c["country"] == "JP" and g.get("admin1") else c["name"]
            if c["country"] in ("US", "CA", "AU") and not c.get("admin") and g.get("admin1"):
                c["admin"] = g["admin1"]
            pack = pack_for(c["country"])
            price, cur, how = pack.price_signal(engine, c) if hasattr(pack, "price_signal") else (None, None, None)
            c["price_local"], c["price_currency"], c["price_how"] = price, cur, how
            c["price_kind"] = "rooms" if how and "rooms only" in how else "homes"
            c["price_ref"] = _to_ref(price, cur, ref, fx)
            engine.log("geography", f"{c['name']} ({c['country']}): price signal "
                                    f"{_fmt(price, cur)} -> {_fmt(c['price_ref'], ref)} ({how or 'none'}); "
                                    f"population {g.get('population') or '?'}")
        engine.checkpoint()
        scored = self._choose(short, prin, ref, params)
        k = int(params.get("max_regions", 5))
        chosen, rejected, per_cc, per_ct = [], [], {}, {}
        for c in scored:
            reason = None
            ct = L.CONTINENT.get(c["country"], "Other")
            if c.get("price_ref") is None:
                reason = "no live price signal acquired"
            elif per_cc.get(c["country"], 0) >= (2 if c["country"] == prin["home"] else 1):
                reason = "another city in the same country ranks higher"
            elif per_ct.get(ct, 0) >= 2:
                reason = f"two regions in {ct} already rank higher (diversity)"
            elif len(chosen) >= k:
                reason = f"ranked below the {k} regions acquired in depth"
            if reason:
                rejected.append({**_brief(c), "reason": reason})
                continue
            per_cc[c["country"]] = per_cc.get(c["country"], 0) + 1
            if c["country"] != prin["home"]:
                per_ct[ct] = per_ct.get(ct, 0) + 1
            chosen.append(c)
        rationale = (f"{len(pool)} candidate cities from public directories; shortlist of {len(short)} by supply, "
                     f"source coverage and diversity; live price signals converted to {ref} with ECB rates; "
                     f"utility = affordability 0.40 + supply 0.20 + coverage 0.15 + stay prior 0.15 + "
                     f"relocation distance 0.10; one city per country")
        return self._materialize(engine, chosen, prin, ref, fx, rationale=rationale, rejected=rejected)

    def _salience(self, engine, cc: str, cands: list[dict], pack_for) -> dict[str, int]:
        """How prominently the country's own housing sources link each city (popular-city links on
        their entry pages): a supply signal where directories give none."""
        names = {c["name"].lower() for c in cands}
        seen: dict[str, int] = {}
        entries = [s.entry_url for s in pack_for(cc).seed_sources()][:2]
        entries += [r.entry_url for r in engine.db.scalars(select(AcqSourceRecipe).where(
            AcqSourceRecipe.domain == self.adapter.name, AcqSourceRecipe.scope == cc,
            AcqSourceRecipe.status == "verified"))][:2]
        for url in entries[:2]:
            if "{" in url or not engine.budget_left():
                continue
            doc = engine.fetch(url, purpose="navigate", kind="portal", render="static")
            if doc is None or not doc.ok:
                continue
            for text, _u in anchors(doc.html, doc.final_url):
                t = text.lower()
                for n in names:
                    if n and (t == n or t.startswith(n + " ") or f" in {n}" in t or t.endswith(" " + n)
                              or (len(t) >= 4 and n.startswith(t + " "))):
                        seen[n] = seen.get(n, 0) + 1
        return {k: min(v, 3) for k, v in seen.items()}

    def _coverage(self, engine) -> dict[str, int]:
        cov: dict[str, int] = {cc: len(v) for cc, v in COUNTRY_SEEDS.items()}
        cov["JP"] = cov.get("JP", 0) + 7
        for r in engine.db.scalars(select(AcqSourceRecipe).where(AcqSourceRecipe.domain == self.adapter.name,
                                                                 AcqSourceRecipe.status == "verified")):
            cov[r.scope] = cov.get(r.scope, 0) + 1
        return cov

    def _choose(self, short: list[dict], prin: dict, ref: str, params: dict) -> list[dict]:
        priced = [c["price_ref"] for c in short if c.get("price_ref") and c.get("price_kind") != "rooms"]
        hi = max(priced) if priced else 1.0
        home_geo = next((c.get("geo") for c in short if c["country"] == prin["home"] and c.get("geo")), None)
        for c in short:
            # a rooms-only signal is not comparable with apartment rents: no affordability credit
            afford = 1 - min(c["price_ref"] / hi, 1.0) if c.get("price_ref") and c.get("price_kind") != "rooms" else 0.0
            supply = min(math.log1p(c["supply"]) / 9, 1.0)
            coverage = min(c["coverage"] / 6, 1.0)
            stay = stay_prior(c["country"], prin)
            dist = haversine_km((home_geo["lat"], home_geo["lon"]), (c["geo"]["lat"], c["geo"]["lon"])) \
                if home_geo and c.get("geo") else None
            near = 1.0 if dist is None else max(0.0, 1 - dist / 12000)
            parts = {"affordability": 0.40 * afford, "supply": 0.20 * supply, "coverage": 0.15 * coverage,
                     "stay_prior": 0.15 * stay, "relocation": 0.10 * near}
            c["utility"] = round(sum(parts.values()), 4)
            c["utility_parts"] = {k: round(v, 4) for k, v in parts.items()}
            c["stay_p"] = stay
            c["distance_km"] = round(dist) if dist is not None else None
        return sorted(short, key=lambda c: -c["utility"])

    # --------------------------------------------------------------- persist

    def _materialize(self, engine, chosen, prin, ref, fx, *, rationale: str, rejected: list) -> list[dict]:
        out = []
        now = engine.claims.now()
        for c in chosen:
            ent = engine.db.scalar(select(AcqEntity).where(AcqEntity.domain == self.adapter.name,
                                                           AcqEntity.entity_type == "region",
                                                           AcqEntity.label == f"{c['name']}, {c['country']}"))
            if ent is None:
                ent = AcqEntity(id=new_id("rg"), domain=self.adapter.name, entity_type="region",
                                label=f"{c['name']}, {c['country']}", block_key=c["country"],
                                features={"name": c["name"], "country": c["country"]}, beliefs={}, source_hosts=[],
                                request_id=engine.request.id, stage="selected")
                engine.db.add(ent)
                engine.db.flush()
            cur = L.COUNTRIES.get(c["country"], ("USD",))[0]

            def add(attr, value, conf, ev, host, kind, url=""):
                if value is None:
                    return
                engine.claims.add(ent.id, ClaimIn(attr, value, conf, ev[:300], now, "geography"), source_host=host,
                                  source_kind=kind, url=url)

            add("country", c["country"], 0.95, "directory/pack", "regent", "derived")
            add("region_currency", cur, 0.95, "ISO country reference", "regent", "derived")
            if fx.get(cur) and fx.get(ref):
                add("fx_per_ref", round(fx[cur] / fx[ref], 6), 0.95, f"ECB: {cur} {fx[cur]} / {ref} {fx[ref]} per EUR",
                    "www.ecb.europa.eu", "public_data", ECB_URL)
            g = c.get("geo")
            if g:
                add("region_coords", [g["lat"], g["lon"]], 0.9, f"GeoNames via Open-Meteo: {g['matched']}",
                    "geocoding-api.open-meteo.com", "public_data", g["url"])
                add("population", g.get("population"), 0.8, f"GeoNames population of {g['matched']}",
                    "geocoding-api.open-meteo.com", "public_data", g["url"])
            if c.get("price_local"):
                add("price_signal_monthly", round(c["price_local"]), 0.6, f"{c.get('price_how')}",
                    "regent", "derived")
            add("languages", list(L.COUNTRIES.get(c["country"], ("", ("?",)))[1]), 0.9, "ISO reference", "regent",
                "derived")
            add("stay_rules", stay_rules(c["country"], prin), 0.5, "prior; see stay_rules.basis", "regent", "derived")
            engine.dirty.add(ent.id)
            region = {"id": ent.id, "name": c["name"], "nav_name": c.get("nav_name") or c["name"],
                      "admin": c.get("admin") or ((c.get("geo") or {}).get("admin1") if c["country"] in ("US", "CA", "AU")
                                                  else None),
                      "country": c["country"], "currency": cur, "ref_currency": ref,
                      "utility": c.get("utility"), "utility_parts": c.get("utility_parts"),
                      "stay_p": c.get("stay_p", stay_prior(c["country"], prin)), "distance_km": c.get("distance_km"),
                      "price_ref": c.get("price_ref"), "origins": c.get("origins"), "urls": c.get("urls", {}),
                      "home": c["country"] == prin["home"], "lang": c.get("lang", "en")}
            out.append(region)
        engine.refresh_dirty()
        ref_per_usd = round(fx[ref] / fx["USD"], 6) if fx.get(ref) and fx.get("USD") else None
        engine.request.plan = {**(engine.request.plan or {}), "principal": prin, "ref_currency": ref,
                               "fx": {"source": ECB_URL, "per_eur": fx, "ref_per_usd": ref_per_usd},
                               "regions": out, "regions_rejected": rejected[:30], "geography_rationale": rationale}
        names = ", ".join(f"{r['name']} ({r['country']})" for r in out)
        engine.log("geography", f"regions chosen: {names}", rationale=rationale)
        engine.checkpoint()
        return out


# --------------------------------------------------------------- helpers

def stay_prior(country: str, prin: dict) -> float:
    """P(the principal may live there) with what is known. Unknown citizenship abroad is a real
    uncertainty -- the route that needs it carries a sensitivity on principal.citizenship."""
    cit = (prin.get("citizenship") or "").upper()
    if cit:
        return 0.95 if cit == country else 0.3
    if prin.get("home") == country:
        return round(0.5 + 0.45 * prin.get("home_confidence", 0), 2)
    return 0.3


def stay_rules(country: str, prin: dict) -> dict[str, Any]:
    return {"p_allowed": stay_prior(country, prin),
            "basis": ("principal appears to live here" if prin.get("home") == country and not prin.get("citizenship")
                      else "citizenship stated" if prin.get("citizenship") else
                      "citizenship unknown: long stays usually need a visa or residence permit"),
            "official_guidance": AUTHORITY.get(country, [])}


def _to_ref(amount, cur, ref, fx) -> float | None:
    if amount is None or not cur or cur not in fx or ref not in fx:
        return None
    return round(float(amount) / fx[cur] * fx[ref], 2)


def _fmt(v, cur) -> str:
    return f"{int(v):,} {cur}" if v else "none"


def _brief(c: dict) -> dict:
    return {k: c.get(k) for k in ("name", "country", "supply", "price_ref", "utility", "utility_parts", "origins")}


def _why(doc) -> str:
    if doc is None:
        return "budget"
    if doc.blocked:
        return f"blocked: {doc.blocked.get('type')}"
    return f"HTTP {doc.status}"


def _spotahome_country(city: str) -> str | None:
    """Spotahome city slugs carry no country; the city is matched to one another directory placed."""
    return None


def _craigslist_sites(html: str) -> list[tuple[str, str, str, str | None]]:
    from lxml import html as LH

    try:
        t = LH.fromstring(html)
    except Exception:
        return []
    out = []
    for h4 in t.xpath("//h4"):
        head = h4.text_content().strip().lower()
        cc = "US" if head in US_STATES else "CA" if head in CA_PROVINCES else NAME_TO_CC.get(head)
        ul = h4.getnext()
        if not cc or ul is None:
            continue
        for a in ul.xpath(".//a[@href]"):
            city = re.split(r"\s*[/-]\s*", a.text_content().strip())[0].title()
            out.append((cc, city, a.get("href"), head.title() if cc in ("US", "CA") else None))
    return out


def median_or_none(vals: list[float]) -> float | None:
    vals = [v for v in vals if v]
    return statistics.median(vals) if vals else None
