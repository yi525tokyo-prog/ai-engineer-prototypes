"""Locale-aware parsing for housing listings anywhere: money (currency, number format,
payment period), rooms and floor area, in the languages of the regions Regent looks at.

This is language/format knowledge (how people write "£1,500 pcm", "1.899 € Kaltmiete",
"$850 per week", "2 Zimmer", "650 sq ft"), not knowledge of any particular site.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ISO country -> (currency, languages, default rent period). Reference data, like an ISO table.
COUNTRIES: dict[str, tuple[str, tuple[str, ...], str]] = {
    "JP": ("JPY", ("ja",), "month"), "GB": ("GBP", ("en",), "month"), "IE": ("EUR", ("en",), "month"),
    "US": ("USD", ("en", "es"), "month"), "CA": ("CAD", ("en", "fr"), "month"), "AU": ("AUD", ("en",), "week"),
    "NZ": ("NZD", ("en",), "week"), "DE": ("EUR", ("de",), "month"), "AT": ("EUR", ("de",), "month"),
    "CH": ("CHF", ("de", "fr", "it"), "month"), "FR": ("EUR", ("fr",), "month"), "BE": ("EUR", ("fr", "nl"), "month"),
    "NL": ("EUR", ("nl", "en"), "month"), "ES": ("EUR", ("es",), "month"), "PT": ("EUR", ("pt",), "month"),
    "IT": ("EUR", ("it",), "month"), "SE": ("SEK", ("sv",), "month"), "NO": ("NOK", ("no",), "month"),
    "DK": ("DKK", ("da",), "month"), "FI": ("EUR", ("fi",), "month"), "PL": ("PLN", ("pl",), "month"),
    "CZ": ("CZK", ("cs",), "month"), "HU": ("HUF", ("hu",), "month"), "GR": ("EUR", ("el",), "month"),
    "SG": ("SGD", ("en",), "month"), "MY": ("MYR", ("en", "ms"), "month"), "TH": ("THB", ("th", "en"), "month"),
    "VN": ("VND", ("vi",), "month"), "PH": ("PHP", ("en",), "month"), "ID": ("IDR", ("id",), "month"),
    "KR": ("KRW", ("ko",), "month"), "TW": ("TWD", ("zh",), "month"), "HK": ("HKD", ("zh", "en"), "month"),
    "CN": ("CNY", ("zh",), "month"), "IN": ("INR", ("en", "hi"), "month"), "AE": ("AED", ("en", "ar"), "year"),
    "MX": ("MXN", ("es",), "month"), "BR": ("BRL", ("pt",), "month"), "AR": ("ARS", ("es",), "month"),
    "CL": ("CLP", ("es",), "month"), "CO": ("COP", ("es",), "month"), "ZA": ("ZAR", ("en",), "month"),
    "TR": ("TRY", ("tr",), "month"), "EE": ("EUR", ("et", "en"), "month"), "LT": ("EUR", ("lt",), "month"),
    "LV": ("EUR", ("lv",), "month"), "RO": ("RON", ("ro",), "month"), "HR": ("EUR", ("hr",), "month"),
    "GE": ("GEL", ("ka", "en"), "month"), "IS": ("ISK", ("is",), "month"),
}
CONTINENT = {**{c: "Europe" for c in ("GB", "IE", "DE", "AT", "CH", "FR", "BE", "NL", "ES", "PT", "IT", "SE", "NO",
                                      "DK", "FI", "PL", "CZ", "HU", "GR", "EE", "LT", "LV", "RO", "HR", "IS", "SI",
                                      "SK", "MT", "CY", "LU", "BG", "RS", "UA")},
             **{c: "Asia" for c in ("JP", "KR", "TW", "HK", "CN", "SG", "MY", "TH", "VN", "PH", "ID", "IN", "AE", "TR",
                                    "GE", "IL")},
             **{c: "Oceania" for c in ("AU", "NZ")},
             **{c: "Americas" for c in ("US", "CA", "MX", "BR", "AR", "CL", "CO", "PE", "CR", "PA")},
             **{c: "Africa" for c in ("ZA", "EG", "MA", "KE", "NG")}}
EUR_LIKE = {"EUR", "CHF", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "RON", "TRY", "BRL", "ARS", "CLP", "COP", "IDR",
            "VND"}  # decimal comma, dot thousands

SYMBOLS = [("NZ$", "NZD"), ("A$", "AUD"), ("AU$", "AUD"), ("C$", "CAD"), ("CA$", "CAD"), ("US$", "USD"),
           ("S$", "SGD"), ("HK$", "HKD"), ("R$", "BRL"), ("£", "GBP"), ("€", "EUR"), ("¥", "JPY"), ("￥", "JPY"),
           ("₩", "KRW"), ("₹", "INR"), ("฿", "THB"), ("₫", "VND"), ("₱", "PHP"), ("zł", "PLN"), ("Kč", "CZK"),
           ("CHF", "CHF"), ("kr", "SEK"), ("$", "$")]
CODES = ("USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF|SEK|NOK|DKK|PLN|CZK|HUF|SGD|HKD|KRW|TWD|THB|MYR|INR|MXN|BRL|ZAR|AED|"
         "TRY|RON|ISK|GEL|PHP|IDR|VND|CNY|ARS|CLP|COP")
_SYM = "|".join(re.escape(s) for s, _ in SYMBOLS)
NUM = r"\d{1,3}(?:[.,  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
PERIOD_WORDS = [
    ("month", r"pcm|p\.?c\.?m\.?|per\s*month|/\s*(?:mo|mth|month|mon|monat|mes|mois|mese|maand|mnd)\b|a\s+month|"
              r"monthly|p\.?\s?m\.?|pro\s+monat|im\s+monat|monatlich|mtl\.?|kaltmiete|warmmiete|nettokaltmiete|"
              r"al\s+mes|/\s*mes|par\s+mois|/\s*mois|al\s+mese|per\s+maand|/\s*maand|por\s+m[eê]s|miesi[eę]cznie"),
    ("week", r"pw|p\.?w\.?|per\s*week|/\s*(?:wk|week|woche|semana|semaine)\b|a\s+week|weekly|pro\s+woche"),
    ("night", r"per\s*night|/\s*night|a\s+night|nightly|pro\s+nacht|/\s*noche|par\s+nuit|/\s*nuit|per\s+bed"),
    ("year", r"per\s*(?:year|annum)|p\.?a\.?|/\s*(?:yr|year)\b|yearly|annually|pro\s+jahr"),
    ("day", r"per\s*day|/\s*day\b|pro\s+tag|/\s*d[ií]a"),
]
MONEY = re.compile(
    rf"(?P<sym>{_SYM})\s?(?P<n1>{NUM})(?!\s?(?:%|m²|m2|sq))"
    rf"|(?P<n2>{NUM})\s?(?P<sym2>€|£|zł|Kč|kr|CHF|(?:{CODES})\b)"
    rf"|(?P<code>(?:{CODES}))\s?(?P<n3>{NUM})",
    re.I)
PERIOD_AFTER = re.compile(r"^\s*(?:\(?\s*)(" + "|".join(f"(?P<{k}>{v})" for k, v in PERIOD_WORDS) + r")", re.I)
PERIOD_ANY = {k: re.compile(v, re.I) for k, v in PERIOD_WORDS}

AREA_M2 = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s?(?:m²|m2|qm|sqm|sq\.?\s?m\b|㎡|mq\b|m\s?2\b)", re.I)
AREA_FT = re.compile(r"(\d{1,2}[,.]?\d{3}|\d{2,4})\s?(?:sq\.?\s?ft\.?|sqft|ft²|square\s+feet)", re.I)
BEDS = re.compile(
    r"(\d{1,2})\s?(?:\+\s?\d\s?)?(?:bed(?:room)?s?|bd|br\b|bdr|beds?\b|schlafzimmer|dormitorios?|habitaci[oó]n(?:es)?|"
    r"habs?\.?|chambres?|camere\s+da\s+letto|slaapkamers?|quartos?|sovrum|soverom|sypialni[ae]?)", re.I)
ROOMS = re.compile(r"(\d{1,2}(?:[.,]5)?)\s?(?:-\s?)?(?:zimmer|zi\.|rooms?\b|pi[eè]ces?|p\.\b|locali|kamers?|rum\b|"
                   r"værelser|pokoje|pokój|pokoi|habitaciones)", re.I)
STUDIO = re.compile(r"\b(studio|bedsit|monolocale|estudio|einzimmer|1-zimmer|ein-zimmer|studette|kitchenette)\b", re.I)
ROOM_IN_SHARE = re.compile(r"(flatshare|flat\s?share|house\s?share|room\s+(?:to\s+rent|for\s+rent|in\s+(?:a\s+)?"
                           r"(?:shared|house|flat|apartment))|wg-zimmer|\bwg\b|habitaci[oó]n\s+en\s+piso|"
                           r"chambre\s+en\s+colocation|colocation|coliving|co-living|kamer\b|private\s+room|double\s+room|"
                           r"single\s+room|ensuite\s+room)", re.I)
HOUSE = re.compile(r"\b(house|detached|semi-detached|terraced|townhouse|bungalow|haus|einfamilienhaus|casa|chalet|"
                   r"maison|villa|cottage)\b", re.I)
HOSTEL = re.compile(r"\b(hostel|dorm(?:itory)?|bed\s+in|backpacker)\b", re.I)
AVAILABLE = re.compile(r"(?:available\s+(?:from|now|immediately)|avail\.?|verfügbar\s+ab|frei\s+ab|bezugsfrei\s+ab|ab\s+sofort|"
                       r"disponible\s+(?:desde|à\s+partir)|disponibile\s+da|beschikbaar\s+(?:per|vanaf))"
                       r"\s*[:：]?\s*([^\n|]{0,24})", re.I)
UK_POSTCODE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s?(\d[A-Z]{2})?\b")
US_ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip()


def parse_number(raw: str, currency: str | None = None) -> float | None:
    s = raw.replace(" ", "").replace(" ", "").replace("'", "")
    if not s:
        return None
    comma_decimal = currency in EUR_LIKE
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", s):        # 1.899  / 1.899,50
        return float(s.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", s):        # 1,899 / 1,899.50
        return float(s.replace(",", ""))
    if re.fullmatch(r"\d+,\d{1,2}", s):                              # 35,58 (decimal comma) or 1,5
        return float(s.replace(",", ".")) if comma_decimal or len(s.split(",")[1]) != 3 else float(s.replace(",", ""))
    if re.fullmatch(r"\d+\.\d{3}", s):                                # 1.200: thousands in EUR locales
        return float(s.replace(".", "")) if comma_decimal else float(s)
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


@dataclass
class Money:
    amount: float
    currency: str
    period: str | None          # month|week|night|year|day|None
    raw: str
    start: int = 0

    def monthly(self, default_period: str = "month") -> float | None:
        per = self.period or default_period
        f = {"month": 1.0, "week": 52 / 12, "year": 1 / 12, "night": 365 / 12, "day": 365 / 12}.get(per)
        return round(self.amount * f, 2) if f else None


def resolve_symbol(sym: str, default_currency: str) -> str:
    s = sym.strip()
    for k, v in SYMBOLS:
        if s.lower() == k.lower():
            if v == "$":
                return default_currency if default_currency in ("USD", "NZD", "AUD", "CAD", "SGD", "HKD", "MXN",
                                                                "ARS", "CLP", "COP") else "USD"
            if v == "SEK" and default_currency in ("NOK", "DKK", "ISK"):
                return default_currency
            return v
    return s.upper()


def money(text: str, default_currency: str = "USD") -> list[Money]:
    out = []
    for m in MONEY.finditer(text):
        sym = m.group("sym") or m.group("sym2") or m.group("code") or ""
        raw_n = m.group("n1") or m.group("n2") or m.group("n3") or ""
        cur = resolve_symbol(sym, default_currency)
        amt = parse_number(raw_n, cur)
        if amt is None:
            continue
        per = None
        pm = PERIOD_AFTER.match(text[m.end():m.end() + 24])
        if pm:
            per = next(k for k, _ in PERIOD_WORDS if pm.group(k))
        out.append(Money(amt, cur, per, m.group(0) + (pm.group(0) if pm else ""), m.start()))
    return out


def area_m2(text: str) -> float | None:
    m = AREA_M2.search(text)
    if m:
        v = parse_number(m.group(1), "EUR" if "," in m.group(1) and "." not in m.group(1) else None)
        if v and 5 <= v <= 1000:
            return round(v, 2)
    m = AREA_FT.search(text)
    if m:
        v = parse_number(m.group(1))
        if v and 50 <= v <= 10000:
            return round(v * 0.092903, 1)
    return None


def bedrooms(text: str) -> int | None:
    if STUDIO.search(text):
        return 0
    m = BEDS.search(text)
    if m:
        n = int(m.group(1))
        return n if n <= 12 else None
    return None


def rooms(text: str) -> float | None:
    m = ROOMS.search(text)
    if m:
        v = float(m.group(1).replace(",", "."))
        return v if 0 < v <= 15 else None
    return None


def unit_kind(text: str) -> str:
    if HOSTEL.search(text):
        return "hostel_bed"
    if ROOM_IN_SHARE.search(text):
        return "room"
    if STUDIO.search(text):
        return "studio"
    if HOUSE.search(text):
        return "house"
    return "apartment"


def available_from(text: str) -> str | None:
    m = AVAILABLE.search(text)
    if not m:
        return None
    v = m.group(1).strip(" .,:")
    head = m.group(0).lower()
    if any(w in head for w in ("now", "immediately", "sofort")) or re.match(r"(now|immediately|sofort)", v, re.I):
        return "immediate"
    d = re.search(r"(\d{4})-(\d{2})-(\d{2})|(\d{1,2})[./](\d{1,2})[./](\d{2,4})", v)
    if d:
        if d.group(1):
            return f"{d.group(1)}-{d.group(2)}-{d.group(3)}"
        y = d.group(6)
        y = f"20{y}" if len(y) == 2 else y
        return f"{y}-{int(d.group(5)):02d}-{int(d.group(4)):02d}"
    mo = re.search(r"(\d{1,2})?\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(\d{4})?", v, re.I)
    if mo:
        return v[:mo.end()].strip()[:24]
    return None     # "available to rent" etc. is not a date


EU_POSTCODE_PLACE = re.compile(r"\b(\d{5})\s+([A-ZÄÖÜÉ][\wäöüßéè-]+(?:[ -][A-ZÄÖÜ][\wäöüß-]+)?)")


def postcode_place(text: str, country: str | None) -> tuple[str | None, str | None]:
    """(postcode, locality) from free text, by the country's postcode format."""
    if country == "GB":
        m = re.search(r"\b([A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2})\b", text) or re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\b(?=\s|,|$)", text)
        return (m.group(1), None) if m else (None, None)
    if country == "US":
        m = re.search(r"\b([A-Z]{2})\s(\d{5})\b", text)
        return (m.group(2), None) if m else (None, None)
    if country in ("DE", "FR", "ES", "IT", "FI"):
        m = EU_POSTCODE_PLACE.search(text)
        return (m.group(1), m.group(2)) if m else (None, None)
    return None, None
