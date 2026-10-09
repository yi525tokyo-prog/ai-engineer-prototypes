"""Site-agnostic listing extraction for any geography.

Three independent record sources, merged by listing URL:

1. **DOM cards** -- the smallest page region holding a plausible rent (currency amount,
   not a filter menu, deposit or per-m2 price) together with a housing signal (bedrooms,
   rooms, floor area, a period like "pcm"/"per week"/"Kaltmiete", or a unit-type word),
   grown to the largest ancestor that still holds only that one listing;
2. **schema.org JSON-LD** (Apartment, Residence, RealEstateListing, Offer, ItemList...);
3. **embedded application state** (``__NEXT_DATA__``, ``window.__X__ = {...}``,
   ``application/json`` scripts): arrays of objects whose keys look like price + rooms/address.

Every record becomes the same Unit/Building mentions and claims as any other source; the
amount is normalized to a monthly rent in the local currency with the raw text kept as
evidence.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from lxml import html as LH

from regent.acquisition.fetch import separate_blocks
from regent.acquisition.housing import locale as L
from regent.acquisition.types import ClaimIn, FetchedDocument, Mention

SKIP_TAGS = ("select", "option", "optgroup", "nav", "footer", "script", "style", "noscript", "svg", "template")
NOT_RENT_BEFORE = re.compile(r"(deposit|kaution|fianza|dép[oô]t|caution|cauzione|borg|holding|admin|fee|gebühr|"
                             r"provision|nebenkosten|bills?|save|from\s+only|up\s+to|min(?:imum)?|max(?:imum)?|"
                             r"reduced|was)\W{0,3}$", re.I)
PER_AREA_AFTER = re.compile(r"^\s*(?:/|per)\s*(?:m²|m2|qm|sq)", re.I)
HOUSING_WORD = re.compile(r"(apartment|flat|studio|room|house|bed|bath|condo|unit|wohnung|zimmer|piso|apartamento|"
                          r"habitaci|appartement|chambre|logement|appartamento|stanza|woning|kamer|residence|"
                          r"to\s+rent|for\s+rent|zu\s+vermieten|mieten|alquiler|louer|affitto|huur|let\b)", re.I)
ADDRESS_CLASS = re.compile(r"(address|location|street|locality|place|district|suburb|area|ort|lage|direcci|adresse)", re.I)
TITLE_CLASS = re.compile(r"(title|heading|name|headline)", re.I)
ADDRESSY = re.compile(r"^\s*(?:(?:flat|apt|unit|#)\s*\w+[,/ ]\s*)?\d+[a-z]?(?:[/-]\d+[a-z]?)?\s+[A-ZÄÖÜ][\w'. -]{2,40}"
                      r"(?:street|st|road|rd|avenue|ave|lane|ln|drive|dr|place|pl|way|crescent|terrace|close|court|"
                      r"straße|strasse|str\.?|weg|allee|platz|gasse|calle|via|rue|boulevard|blvd)\b"
                      r"|^[A-ZÄÖÜ][\w'. -]{2,40}(?:straße|strasse|str\.|weg|allee|platz|gasse)\s+\d+"
                      r"|,\s*[A-Z][a-z]+(?:\s[A-Z][a-z]+)?\s*(?:,|$)", re.I)
UI_LABEL = re.compile(r"^\W*(pricing|price|prices|details|detail|contact|overview|features|description|map|photos?|"
                      r"gallery|share|save|saved|new|featured|premium|top|sponsored|ad|anzeige|more|mehr|ver m[aá]s|"
                      r"precio|preis|prix|info|information|highlights?|amenities|location)\W*$", re.I)
LD_LISTING = {"Apartment", "Residence", "House", "SingleFamilyResidence", "Accommodation", "Room", "Suite",
              "RealEstateListing", "Offer", "Product", "ApartmentComplex", "Place", "LodgingBusiness", "Hostel"}
MIN_MONTHLY_BY_CUR = {"JPY": 10000, "KRW": 100000, "INR": 3000, "THB": 2000, "PHP": 3000, "IDR": 500000,
                      "VND": 1500000, "HUF": 30000, "CZK": 3000, "CLP": 80000, "COP": 400000, "ARS": 20000}


class GenericListingExtractor:
    name = "generic-listing-extractor-v1"

    def __init__(self, country: str | None = None, currency: str | None = None, default_period: str | None = None,
                 locality: str | None = None):
        cc = (country or "").upper()
        cur, _langs, per = L.COUNTRIES.get(cc, ("USD", ("en",), "month"))
        self.country = cc or None
        self.currency = currency or cur
        self.default_period = default_period or per
        self.locality = locality

    # ------------------------------------------------------------------ api

    def extract(self, doc: FetchedDocument, purpose: str = "") -> list[Mention]:
        if not doc.html:
            return []
        try:
            raw = LH.fromstring(doc.html)
        except Exception:
            return []
        ld, aggregates = self._jsonld(raw, doc)
        state = self._app_state(raw, doc)
        tree = LH.fromstring(doc.html)
        for bad in tree.xpath("//script|//style|//noscript|//template|//svg"):
            bad.drop_tree()
        separate_blocks(tree)
        cards = self._dom_cards(tree, doc)
        records = self._merge(cards, ld, state)
        out = [m for r in records if (m := self._mention(r, doc)) is not None]
        if aggregates and (purpose == "market" or not out):
            out += aggregates
        return out

    def has_content(self, text: str) -> bool:
        ms = [m for m in L.money(text[:200000], self.currency) if m.amount >= 50]
        return len(ms) >= 3 and bool(HOUSING_WORD.search(text))

    # ------------------------------------------------------------- DOM cards

    def _rent(self, text: str) -> L.Money | None:
        best = None
        nightly = bool(L.HOSTEL.search(text) or L.PERIOD_ANY["night"].search(text))
        for m in L.money(text, self.currency):
            if m.period is None and nightly:
                m.period = "night"      # hostel prices are per night even when the unit is not written
            before = text[max(0, m.start - 22):m.start]
            after = text[m.start + len(m.raw):m.start + len(m.raw) + 10]
            if NOT_RENT_BEFORE.search(before) or PER_AREA_AFTER.match(after):
                continue
            monthly = m.monthly(self.default_period)
            if monthly is None or monthly < MIN_MONTHLY_BY_CUR.get(m.currency, 100):
                continue
            if m.period == "month":
                return m
            if best is None:
                best = m
        return best

    def _signal(self, text: str) -> bool:
        return bool(L.bedrooms(text) is not None or L.rooms(text) or L.area_m2(text)
                    or L.PERIOD_ANY["month"].search(text) or L.PERIOD_ANY["week"].search(text)
                    or HOUSING_WORD.search(text))

    def _dom_cards(self, tree, doc: FetchedDocument) -> list[dict[str, Any]]:
        hits = []
        for e in tree.iter():
            if not isinstance(e.tag, str) or e.tag in ("html", "body") or e.tag in SKIP_TAGS:
                continue
            if any(a.tag in SKIP_TAGS for a in e.iterancestors()):
                continue
            t = L.norm(e.text_content())
            if len(t) > 1200 or len(t) < 6:
                continue
            if self._rent(t) is not None and self._signal(t):
                hits.append(e)
        hs = set(hits)
        minimal = [e for e in hits if not any(d in hs for d in e.iterdescendants())]
        mins = set(minimal)
        monthly_of = {id(e): (self._rent(L.norm(e.text_content())) or L.Money(0, "", None, "")).monthly(
            self.default_period) or 0 for e in minimal}

        def one_listing(p) -> bool:
            # "£2,850 per month" and "£658 per week" in one card are one price, not two listings
            vals = [monthly_of[id(d)] for d in ([p] if p in mins else []) + [d for d in p.iterdescendants() if d in mins]]
            return len(vals) <= 1 or (max(vals) - min(vals)) <= 0.02 * max(vals)
        cards = []
        seen = set()
        for e in minimal:
            card = e
            while card.getparent() is not None and card.getparent().tag not in ("body", "html"):
                p = card.getparent()
                if len(L.norm(p.text_content())) > 2500:
                    break
                if not one_listing(p):
                    break
                card = p
            if id(card) in seen:
                continue
            seen.add(id(card))
            rec = self._card_record(card, doc)
            if rec:
                cards.append(rec)
        return cards

    def _card_record(self, card, doc: FetchedDocument) -> dict[str, Any] | None:
        t = L.norm(card.text_content())
        m = self._rent(t)
        if m is None:
            return None
        url = self._card_link(card, doc)
        title = None
        for el in card.iter():
            if not isinstance(el.tag, str):
                continue
            cls = f"{el.get('class') or ''} {el.get('data-testid') or ''} {el.get('itemprop') or ''}"
            if el.tag in ("h1", "h2", "h3", "h4") or TITLE_CLASS.search(cls):
                tt = L.norm(el.text_content())
                if 3 <= len(tt) <= 160 and not L.money(tt, self.currency) and not UI_LABEL.match(tt):
                    title = tt
                    break
        address = None
        for el in card.iter():
            if not isinstance(el.tag, str):
                continue
            cls = f"{el.get('class') or ''} {el.get('itemprop') or ''} {el.get('data-testid') or ''} {el.tag}"
            if el.tag == "address" or ADDRESS_CLASS.search(cls):
                at = L.norm(el.text_content())
                if 3 <= len(at) <= 140 and not L.money(at, self.currency):
                    address = at
                    break
        if address is None and title and ADDRESSY.search(title):
            address = title       # many portals title a listing by its street address
        postcode, place = L.postcode_place(t, self.country)
        if address is None and place:
            address = f"{postcode} {place}"
        return {"source": "dom", "url": url, "title": title, "address": address, "text": t[:600],
                "postcode": postcode, "locality": place,
                "price": m.amount, "currency": m.currency, "period": m.period, "price_raw": m.raw,
                "bedrooms": L.bedrooms(t), "rooms": L.rooms(t), "area_m2": L.area_m2(t), "kind": L.unit_kind(t),
                "available_from": L.available_from(t)}

    @staticmethod
    def _card_link(card, doc: FetchedDocument) -> str | None:
        host = doc.host
        best, score = None, -1
        for a in card.xpath(".//a[@href]") + ([card] if card.tag == "a" and card.get("href") else []):
            href = a.get("href") or ""
            if href.startswith(("javascript", "#", "mailto:", "tel:")):
                continue
            u = urldefrag(urljoin(doc.final_url, href))[0]
            p = urlparse(u)
            if p.netloc and p.netloc != host and not p.netloc.endswith("." + host.removeprefix("www.")):
                continue
            s = len(p.path) + 5 * len(L.norm(a.text_content())[:40]) / 40 + (5 if re.search(r"\d{4,}", p.path) else 0)
            if s > score:
                best, score = u, s
        return best

    # ------------------------------------------------------------ JSON-LD

    def _jsonld(self, raw, doc: FetchedDocument) -> tuple[list[dict[str, Any]], list[Mention]]:
        items: list[dict[str, Any]] = []
        aggregates: list[Mention] = []

        def visit(x: Any, depth: int = 0) -> None:
            if depth > 8:
                return
            if isinstance(x, list):
                for v in x:
                    visit(v, depth + 1)
                return
            if not isinstance(x, dict):
                return
            types = x.get("@type")
            types = set(types) if isinstance(types, list) else {types}
            if "ListItem" in types and isinstance(x.get("item"), dict):
                visit(x["item"], depth + 1)
                return
            offers = x.get("offers")
            if isinstance(offers, dict) and offers.get("@type") == "AggregateOffer" and not x.get("address"):
                agg = self._aggregate(offers, x, doc)
                if agg is not None:
                    aggregates.append(agg)
            if types & LD_LISTING and (x.get("address") or x.get("geo") or x.get("numberOfBedrooms") is not None
                                       or x.get("numberOfRooms") is not None or isinstance(offers, dict)):
                rec = self._ld_record(x, doc)
                if rec:
                    items.append(rec)
            for k in ("@graph", "itemListElement", "mainEntity", "containsPlace", "itemOffered"):
                if k in x:
                    visit(x[k], depth + 1)

        for s in raw.xpath("//script[@type='application/ld+json']"):
            try:
                visit(json.loads(s.text_content() or ""))
            except Exception:
                continue
        return items, aggregates

    def _ld_record(self, x: dict, doc: FetchedDocument) -> dict[str, Any] | None:
        addr = x.get("address") or {}
        if isinstance(addr, str):
            address, locality, postcode = addr, None, None
        else:
            parts = [addr.get("streetAddress"), addr.get("addressLocality"), addr.get("addressRegion")]
            address = ", ".join(p for p in parts if isinstance(p, str) and p.strip()) or None
            locality, postcode = addr.get("addressLocality"), addr.get("postalCode")
        geo = x.get("geo") or {}
        lat = x.get("latitude") or geo.get("latitude")
        lon = x.get("longitude") or geo.get("longitude")
        offers = x.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price, cur = None, None
        if isinstance(offers, dict) and offers.get("@type") != "AggregateOffer":
            price = _num(offers.get("price"))
            cur = offers.get("priceCurrency")
        area = x.get("floorSize") or {}
        area_m2 = None
        if isinstance(area, dict) and _num(area.get("value")):
            v = _num(area.get("value"))
            unit = str(area.get("unitCode") or area.get("unitText") or "").upper()
            area_m2 = round(v * 0.092903, 1) if unit in ("FTK", "SQFT", "FT2", "SQ FT") else v
        url = x.get("url") or x.get("@id")
        url = urldefrag(urljoin(doc.final_url, url))[0] if isinstance(url, str) and not url.startswith("#") else None
        rec = {"source": "jsonld", "url": url, "title": x.get("name"), "address": address, "locality": locality,
               "postcode": postcode, "lat": _num(lat), "lon": _num(lon),
               "bedrooms": _int(x.get("numberOfBedrooms")), "rooms": _num(x.get("numberOfRooms")),
               "bathrooms": _num(x.get("numberOfBathroomsTotal")), "area_m2": area_m2,
               "price": price, "currency": cur, "period": None}
        return rec if (url or address) else None

    def _aggregate(self, offers: dict, x: dict, doc: FetchedDocument) -> Mention | None:
        lo, hi, n = _num(offers.get("lowPrice")), _num(offers.get("highPrice")), _int(offers.get("offerCount"))
        cur = offers.get("priceCurrency") or self.currency
        if not (lo or hi):
            return None
        now = doc.fetched_at
        ev = f"schema.org AggregateOffer on {doc.final_url}: {lo}-{hi} {cur}, {n} offers"
        claims = [ClaimIn("listing_count", n, 0.8, ev, now, self.name)] if n else []
        if lo:
            claims.append(ClaimIn("market_rent_low", lo, 0.6, ev, now, self.name))
        if hi:
            claims.append(ClaimIn("market_rent_high", hi, 0.6, ev, now, self.name))
        claims.append(ClaimIn("market_currency", cur, 0.9, ev, now, self.name))
        area = self.locality or L.norm(x.get("name") or "")[:60]
        return Mention(entity_type="area", url=doc.final_url, claims=claims, raw_text=ev,
                       key_fields={"area": f"{self.country or ''}|{area}".lower()})

    # ----------------------------------------------------- embedded app state

    def _app_state(self, raw, doc: FetchedDocument) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for s in raw.xpath("//script"):
            txt = s.text_content() or ""
            if len(txt) < 500 or s.get("type") == "application/ld+json":
                continue
            blob = None
            if s.get("type") == "application/json" or s.get("id") == "__NEXT_DATA__":
                blob = txt
            else:
                m = re.search(r"^\s*(?:window\.[\w$.\[\]'\"]+|var\s+\w+|self\.\w+)\s*=\s*(\{.*\}|\[.*\])\s*;?\s*$",
                              txt, re.S)
                blob = m.group(1) if m else None
            if not blob:
                continue
            try:
                data = json.loads(blob)
            except Exception:
                continue
            for arr in _record_arrays(data):
                for o in arr[:200]:
                    rec = self._state_record(o, doc)
                    if rec:
                        out.append(rec)
        return out

    def _state_record(self, o: dict, doc: FetchedDocument) -> dict[str, Any] | None:
        flat = _flatten(o, 3)
        price, cur, period = None, None, None
        for k, v in flat.items():
            kl = k.lower()
            if re.search(r"(^|\.)(price|rent|monthlyrent|amount|baseprice|unformattedprice|priceamount|miete|"
                         r"kaltmiete|precio|prix|value)$", kl) and not re.search(r"(deposit|fee|sqm|perm2|persq|"
                                                                               r"min|max|old|previous|range)", kl):
                n = _num(v)
                if n is None and isinstance(v, str):
                    ms = L.money(v, self.currency)
                    if ms:
                        n, cur, period = ms[0].amount, ms[0].currency, ms[0].period
                if n and n >= 50:
                    price = n
                    break
        if price is None:
            return None
        for k, v in flat.items():
            kl = k.lower()
            if cur is None and re.search(r"currency", kl) and isinstance(v, str) and len(v) == 3:
                cur = v.upper()
            if period is None and re.search(r"(frequency|period|pricequalifier|interval)", kl) and isinstance(v, str):
                period = next((p for p, rx in L.PERIOD_ANY.items() if rx.search(v)), None) or \
                    {"monthly": "month", "weekly": "week", "daily": "day", "yearly": "year"}.get(v.lower())
        get = lambda *pats: next((v for k, v in flat.items()  # noqa: E731
                                  if any(re.search(p, k.lower()) for p in pats) and v not in (None, "", [])), None)
        url = get(r"(^|\.)(url|link|href|detailurl|propertyurl|canonicalurl|path)$")
        url = urldefrag(urljoin(doc.final_url, url))[0] if isinstance(url, str) and len(url) > 1 else None
        address = get(r"(^|\.)(address|displayaddress|streetaddress|fulladdress|location\.name|addressline)$",
                      r"(^|\.)address\.(street|line1|full)")
        if isinstance(address, dict):
            address = ", ".join(str(v) for v in address.values() if isinstance(v, str))[:140]
        rec = {"source": "state", "url": url, "title": get(r"(^|\.)(title|name|headline|summary)$"),
               "address": address if isinstance(address, str) else None,
               "locality": get(r"(city|locality|town|suburb|district)$"),
               "postcode": get(r"(postcode|postalcode|zip|zipcode)$"),
               "lat": _num(get(r"(^|\.)(lat|latitude)$")), "lon": _num(get(r"(^|\.)(lng|lon|longitude)$")),
               "bedrooms": _int(get(r"(^|\.)(bedrooms|beds|numberofbedrooms|bedroomcount|bedroom)$")),
               "rooms": _num(get(r"(^|\.)(rooms|numberofrooms|roomcount|zimmer|numrooms)$")),
               "bathrooms": _num(get(r"(^|\.)(bathrooms|baths|bathroomcount)$")),
               "area_m2": _area_from(flat), "price": price, "currency": cur, "period": period,
               "available_from": get(r"(availablefrom|availabilitydate|available_date|dateavailable)$")}
        return rec if (rec["url"] or rec["address"]) else None

    # ----------------------------------------------------------------- merge

    def _merge(self, cards, ld, state) -> list[dict[str, Any]]:
        by_url: dict[str, dict[str, Any]] = {}
        rest: list[dict[str, Any]] = []
        for r in cards + ld + state:
            k = _url_key(r.get("url"))
            if not k:
                if r.get("price"):
                    rest.append(r)
                continue
            cur = by_url.get(k)
            if cur is None:
                by_url[k] = dict(r)
                continue
            for f, v in r.items():
                if cur.get(f) in (None, "", []) and v not in (None, "", []):
                    cur[f] = v
            cur["source"] = "+".join(sorted(set(cur["source"].split("+")) | {r["source"]}))
        return [r for r in list(by_url.values()) + rest if r.get("price")]

    # --------------------------------------------------------------- mention

    def _mention(self, r: dict[str, Any], doc: FetchedDocument) -> Mention | None:
        cur = (r.get("currency") or self.currency or "").upper()
        m = L.Money(float(r["price"]), cur, r.get("period"), str(r.get("price_raw") or r["price"]))
        monthly = m.monthly(self.default_period)
        if monthly is None or monthly < MIN_MONTHLY_BY_CUR.get(cur, 100):
            return None
        now = doc.fetched_at
        conf = 0.9 if r["source"] != "dom" or r.get("period") else 0.75
        ev = (r.get("price_raw") or f"{r['price']} {cur}") + (f" | {r.get('text', '')[:200]}" if r.get("text") else "")
        claims = [ClaimIn("rent", round(monthly), conf, ev, now, self.name),
                  ClaimIn("currency", cur, 0.95, ev, now, self.name),
                  ClaimIn("rent_period", r.get("period") or f"{self.default_period} (assumed)",
                          0.9 if r.get("period") else 0.5, ev, now, self.name),
                  ClaimIn("availability", True, 0.7, "listed on a live index page", now, self.name)]
        kind = r.get("kind") or L.unit_kind(" ".join(str(r.get(k) or "") for k in ("title", "text")))
        claims.append(ClaimIn("unit_kind", kind, 0.7, (r.get("title") or "")[:120] or ev[:120], now, self.name))
        for k in ("bedrooms", "rooms", "bathrooms", "area_m2"):
            if r.get(k) is not None:
                claims.append(ClaimIn(k, r[k], 0.85, ev[:200], now, self.name))
        if r.get("bedrooms") is None and r.get("rooms"):
            # continental room counts include the living room: "2 Zimmer" ~ 1 bedroom
            est = 0 if kind in ("studio", "room") else max(0, int(r["rooms"]) - 1)
            claims.append(ClaimIn("bedrooms", est, 0.6, f"derived from {r['rooms']} rooms", now, self.name))
        if r.get("available_from"):
            claims.append(ClaimIn("move_in", str(r["available_from"])[:24], 0.75, ev[:200], now, self.name))
        if r.get("title") and not UI_LABEL.match(str(r["title"])):
            claims.append(ClaimIn("title", L.norm(str(r["title"]))[:160], 0.9, str(r["title"])[:160], now, self.name))
        bclaims = []
        address = L.norm(str(r.get("address") or "")) or None
        if address:
            bclaims.append(ClaimIn("address", address[:160], 0.8, address, now, self.name))
        if r.get("locality"):
            bclaims.append(ClaimIn("locality", L.norm(str(r["locality"]))[:80], 0.85, str(r["locality"]), now, self.name))
        if r.get("postcode"):
            bclaims.append(ClaimIn("postcode", str(r["postcode"])[:12], 0.9, str(r["postcode"]), now, self.name))
        if r.get("lat") and r.get("lon"):
            bclaims.append(ClaimIn("coords", [round(r["lat"], 6), round(r["lon"], 6)], 0.9, "listing coordinates",
                                   now, self.name))
        building = Mention(entity_type="building", url=doc.final_url, claims=bclaims, raw_text=address or "",
                           key_fields={"address_key": address_key(address), "locality": (r.get("locality") or "").lower(),
                                       "postcode": (r.get("postcode") or "").upper().replace(" ", ""),
                                       "coords": [r["lat"], r["lon"]] if r.get("lat") and r.get("lon") else None,
                                       "country": self.country})
        if not bclaims:
            building = None      # no location evidence: a unit without a building beats a fake building
        return Mention(entity_type="unit", url=doc.final_url, parent=building, claims=claims,
                       raw_text=(r.get("text") or r.get("title") or "")[:400],
                       links={"detail_url": r["url"]} if r.get("url") else {},
                       key_fields={"listing_url": _url_key(r.get("url")), "country": self.country,
                                   "bedrooms": r.get("bedrooms"),
                                   "area_m2": r.get("area_m2"), "rent": round(monthly), "currency": cur,
                                   "kind": kind, "title_key": _title_key(r.get("title"))})


# ---------------------------------------------------------------- helpers

def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = re.sub(r"[^\d.,]", "", v)
        return L.parse_number(s) if s else None
    return None


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(n) if n is not None and n == int(n) and 0 <= n <= 20 else None


def _flatten(o: Any, depth: int, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if depth < 0:
        return out
    if isinstance(o, dict):
        for k, v in o.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                out.update(_flatten(v if isinstance(v, dict) else (v[0] if v and isinstance(v[0], dict) else {}),
                                    depth - 1, key))
            else:
                out[key] = v
    return out


def _record_arrays(data: Any, depth: int = 0) -> list[list[dict]]:
    """Arrays of >=3 similar objects that look like listings (price-ish + address/rooms-ish keys)."""
    found = []
    if depth > 12:
        return found
    if isinstance(data, list):
        objs = [x for x in data if isinstance(x, dict)]
        if len(objs) >= 3:
            keys = " ".join(" ".join(_flatten(o, 2).keys()) for o in objs[:3]).lower()
            if re.search(r"price|rent|miete|precio|prix", keys) and \
                    re.search(r"bed|room|zimmer|address|street|location|lat|area|size|sqft", keys):
                found.append(objs)
                return found
        for x in data[:300]:
            found += _record_arrays(x, depth + 1)
    elif isinstance(data, dict):
        for v in data.values():
            found += _record_arrays(v, depth + 1)
    return found


def _area_from(flat: dict[str, Any]) -> float | None:
    for k, v in flat.items():
        kl = k.lower()
        n = _num(v)
        if n is None:
            continue
        if re.search(r"(sqft|squarefeet|livingareasqft)$", kl) and 50 <= n <= 10000:
            return round(n * 0.092903, 1)
        if re.search(r"(livingarea|area|size|surface|wohnflaeche|wohnfläche|superficie|sqm|m2)$", kl) and 5 <= n <= 1000:
            return round(n, 1)
    return None


def _url_key(u: str | None) -> str | None:
    if not u:
        return None
    p = urlparse(u)
    return f"{p.netloc.removeprefix('www.')}{p.path.rstrip('/')}".lower() or None


def _title_key(t: Any) -> str:
    return re.sub(r"[\W_]+", "", L.norm(str(t or "")).lower())[:80]


def address_key(a: str | None) -> str:
    """Street-level key for any Latin-script address: lowercased, abbreviations unified."""
    if not a:
        return ""
    s = L.norm(a).lower()
    for full, ab in ((r"\bstreet\b", "st"), (r"\bavenue\b", "ave"), (r"\broad\b", "rd"), (r"\bstraße\b", "str"),
                     (r"\bstrasse\b", "str"), (r"\bcalle\b", "c"), (r"\bdrive\b", "dr"), (r"\bplace\b", "pl"),
                     (r"\bboulevard\b", "blvd"), (r"\blane\b", "ln"), (r"\bapartment\b|\bapt\b|\bunit\b|#", "#")):
        s = re.sub(full, ab, s)
    return re.sub(r"[^\w#]+", " ", s).strip()
