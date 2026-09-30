"""Domain adapter interface.

Everything domain-specific lives behind this interface. Housing is the first
implementation; jobs, universities, travel and products plug in the same way:
provide attribute specs (with TTLs), an extractor, a resolver, a planner,
enrichment jobs, a funnel, a world projection and strategy generation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from regent.acquisition.types import AttrSpec, EnrichmentJobSpec, FetchedDocument, FreshnessPolicy, Mention

if TYPE_CHECKING:  # pragma: no cover
    from regent.acquisition.engine import AcquisitionEngine
    from regent.acquisition.tables import AcqEntity, AcqJob, AcqRequest
    from regent.core.world.state import WorldView
    from regent.db import Mission
    from regent.schemas import RouteProposal


class DomainAdapter(ABC):
    name: str = "domain"
    #: mission tags that make this adapter responsible for a mission
    tags: tuple[str, ...] = ()
    #: words in a mission's title/objective that make this adapter responsible when no tag says so
    keywords: tuple[str, ...] = ()
    attributes: dict[str, AttrSpec] = {}
    #: attributes whose staleness triggers a recheck
    time_sensitive: tuple[str, ...] = ()

    def policy(self) -> FreshnessPolicy:
        return FreshnessPolicy(self.attributes)

    # ---- what don't I know?
    @abstractmethod
    def information_needs(self, mission: "Mission", world: "WorldView", state: dict[str, Any]) -> list[dict[str, Any]]:
        """Acquisition work the mission needs now: [{action, params, reason}]."""

    # ---- acquisition
    @abstractmethod
    def run_discovery(self, engine: "AcquisitionEngine", request: "AcqRequest") -> None: ...

    @abstractmethod
    def extract(self, doc: FetchedDocument, purpose: str) -> list[Mention]: ...

    @abstractmethod
    def resolver(self): ...

    @abstractmethod
    def funnel(self, engine: "AcquisitionEngine", request: "AcqRequest") -> dict[str, Any]: ...

    @abstractmethod
    def enrichment_jobs(self, engine: "AcquisitionEngine", entity: "AcqEntity") -> list[EnrichmentJobSpec]: ...

    @abstractmethod
    def run_job(self, engine: "AcquisitionEngine", job: "AcqJob") -> dict[str, Any]: ...

    # ---- back into Regent
    @abstractmethod
    def project(self, engine: "AcquisitionEngine", request: "AcqRequest") -> list[dict[str, Any]]:
        """World events (entity_upserted / fact_observed) summarizing the acquired world."""

    @abstractmethod
    def strategies(self, mission: dict[str, Any], world: dict[str, Any]) -> list["RouteProposal"]: ...


_ADAPTERS: dict[str, DomainAdapter] = {}


def register(adapter: DomainAdapter) -> DomainAdapter:
    _ADAPTERS[adapter.name] = adapter
    return adapter


def adapters() -> dict[str, DomainAdapter]:
    if not _ADAPTERS:
        from regent.acquisition.housing.adapter import HousingAdapter
        from regent.software.domain import SoftwareAdapter

        register(HousingAdapter())
        register(SoftwareAdapter())
    return _ADAPTERS


def for_mission(mission: Any) -> list[DomainAdapter]:
    """Adapters responsible for a mission: by tag, else by the words of its title/objective.
    Accepts a Mission row, a mission dict, or a plain list of tags."""
    import unicodedata

    if isinstance(mission, (list, tuple, set)):
        tags, text = set(mission), ""
    elif isinstance(mission, dict):
        tags, text = set(mission.get("tags") or []), f"{mission.get('title', '')} {mission.get('objective', '')}"
    else:
        tags = set(getattr(mission, "tags", None) or [])
        text = f"{getattr(mission, 'title', '')} {getattr(mission, 'objective', '')}"
    text = unicodedata.normalize("NFKC", text).lower()
    matched = [a for a in adapters().values()
               if set(a.tags) & tags or (text and any(k in text for k in a.keywords))]
    if matched or isinstance(mission, (list, tuple, set)):
        return matched
    # nothing claims it by tag or wording: adapters that can find out what it needs may take it
    return [a for a in adapters().values() if getattr(a, "claims", None) and a.claims(mission)]
