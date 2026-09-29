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

        register(HousingAdapter())
    return _ADAPTERS


def for_mission(tags: list[str]) -> list[DomainAdapter]:
    return [a for a in adapters().values() if set(a.tags) & set(tags or [])]
