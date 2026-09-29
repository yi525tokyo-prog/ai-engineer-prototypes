"""Provider-neutral types for World Acquisition.

The acquisition subsystem answers "what must I know to decide?" by going to
the reachable public internet. Its unit of storage is the *claim* -- an
attribute value asserted by a source at a time, with confidence and a TTL --
never the page. Domain adapters (housing, jobs, universities, ...) plug in
extractors, resolvers, enrichers and planners; everything else is generic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

SourceKind = Literal["search", "portal", "operator", "maps", "reviews", "public_data", "derived", "principal"]

# Prior reliability of a *kind* of source; learned per host afterwards.
SOURCE_KIND_PRIOR: dict[str, float] = {
    "operator": 0.9, "public_data": 0.95, "portal": 0.8, "maps": 0.85, "reviews": 0.55,
    "search": 0.5, "derived": 0.6, "principal": 0.99,
}


@dataclass
class AttrSpec:
    """How one attribute behaves: type, tolerance for agreement, and freshness."""

    name: str
    level: str                       # entity type the attribute belongs to ("building", "unit", ...)
    kind: Literal["number", "text", "bool", "date", "json"] = "text"
    unit: str = ""
    ttl_s: float = 86400.0
    rel_tol: float = 0.0             # numbers within this relative tolerance agree
    abs_tol: float = 0.0             # ... or within this absolute tolerance
    material: bool = True            # material claims require provenance in the UI


@dataclass
class FreshnessPolicy:
    specs: dict[str, AttrSpec]
    default_ttl_s: float = 7 * 86400.0

    def ttl(self, attribute: str) -> float:
        s = self.specs.get(attribute)
        return s.ttl_s if s else self.default_ttl_s


@dataclass
class SourceSpec:
    """A source the adapter knows how to *start* from. Candidates are never seeded:
    a source is an entry point that must be navigated to reach live content."""

    host: str
    kind: SourceKind
    entry_url: str
    nav: list[str] = field(default_factory=list)   # anchor-text terms to follow, templated by plan params
    render: Literal["static", "browser", "auto"] = "auto"
    purpose: str = ""
    max_pages: int = 2


@dataclass
class SourceQuery:
    """One unit of planned acquisition work."""

    purpose: str                      # "market", "discovery", "detail", "verify", "enrich:<kind>"
    source: SourceSpec | None = None
    url: str | None = None
    search_query: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    priority: float = 1.0


@dataclass
class QueryPlan:
    request_id: str
    stages: list[dict[str, Any]] = field(default_factory=list)   # [{name, queries: [SourceQuery...], why}]
    assumptions: list[dict[str, Any]] = field(default_factory=list)
    expansions: list[str] = field(default_factory=list)


@dataclass
class FetchedDocument:
    id: str
    url: str
    final_url: str
    host: str
    status: int
    html: str
    text: str
    fetched_at: datetime
    render: str
    blocked: dict[str, Any] | None = None
    error: str | None = None
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 200 and not self.blocked and not self.error


@dataclass
class ClaimIn:
    attribute: str
    value: Any
    confidence: float = 0.8          # extractor confidence that the source said this
    evidence: str = ""               # the snippet the value was read from
    observed_at: datetime | None = None
    extractor: str = ""


@dataclass
class Mention:
    """One record as seen in one document (a listing row, a detail page, a market row).

    ``entity_type`` is the level it describes; ``parent`` optionally carries the
    enclosing entity's mention (a unit row's building)."""

    entity_type: str
    claims: list[ClaimIn]
    url: str
    key_fields: dict[str, Any] = field(default_factory=dict)   # resolution features
    parent: "Mention | None" = None
    links: dict[str, str] = field(default_factory=dict)        # e.g. detail_url, source_url
    raw_text: str = ""

    def value(self, attr: str, default: Any = None) -> Any:
        for c in self.claims:
            if c.attribute == attr:
                return c.value
        return self.key_fields.get(attr, default)


@dataclass
class EnrichmentJobSpec:
    kind: str
    entity_id: str
    params: dict[str, Any] = field(default_factory=dict)
    priority: float = 1.0
    reason: str = ""
