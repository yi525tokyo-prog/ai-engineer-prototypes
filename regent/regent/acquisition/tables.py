"""Persistent acquisition state.

Claims are append-only: a new observation adds a claim; it never edits or
deletes an earlier one (only its ``status`` moves to ``stale``/``retracted``).
Entities hold a derived belief summary recomputed from their claims, so the
"current value" is always an explainable function of evidence.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from regent.db import Base, JSONType
from regent.ids import utcnow


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow)


class AcqRequest(Base):
    __tablename__ = "acq_requests"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mission_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    domain: Mapped[str] = mapped_column(String(32))
    goal: Mapped[str] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    plan: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")   # pending|running|done|failed
    stage: Mapped[str] = mapped_column(String(32), default="plan")
    stats: Mapped[dict] = mapped_column(JSONType, default=dict)
    log: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class AcqSource(Base):
    """Per-host knowledge: robots, access outcome, yield, learned reliability."""

    __tablename__ = "acq_sources"
    host: Mapped[str] = mapped_column(String(120), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), default="portal")
    robots_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    robots_txt: Mapped[str] = mapped_column(Text, default="")
    fetches: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[int] = mapped_column(Integer, default=0)
    disallowed: Mapped[int] = mapped_column(Integer, default=0)
    records: Mapped[int] = mapped_column(Integer, default=0)
    agree: Mapped[float] = mapped_column(Float, default=1.0)     # Beta(agree, disagree) over reconciled claims
    disagree: Mapped[float] = mapped_column(Float, default=1.0)
    last_status: Mapped[str] = mapped_column(String(80), default="")
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[dict] = mapped_column(JSONType, default=dict)


class AcqDocument(Base):
    __tablename__ = "acq_documents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str] = mapped_column(Text, default="")
    host: Mapped[str] = mapped_column(String(120), index=True)
    purpose: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[int] = mapped_column(Integer, default=0)
    render: Mapped[str] = mapped_column(String(12), default="static")
    robots_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    blocked: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    cache_path: Mapped[str] = mapped_column(Text, default="")
    mentions: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime] = _ts()


class AcqEntity(Base):
    __tablename__ = "acq_entities"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    domain: Mapped[str] = mapped_column(String(32), index=True)
    entity_type: Mapped[str] = mapped_column(String(24), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    label: Mapped[str] = mapped_column(Text, default="")
    block_key: Mapped[str] = mapped_column(String(200), index=True, default="")
    features: Mapped[dict] = mapped_column(JSONType, default=dict)     # resolution features (representative)
    beliefs: Mapped[dict] = mapped_column(JSONType, default=dict)      # attribute -> belief summary
    stage: Mapped[str] = mapped_column(String(16), default="discovered")  # discovered|filtered|shortlisted|deep|rejected
    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_detail: Mapped[dict] = mapped_column(JSONType, default=dict)
    mention_count: Mapped[int] = mapped_column(Integer, default=0)
    source_hosts: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active|merged
    merged_into: Mapped[str | None] = mapped_column(String(40), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    region_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)   # competing region
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class AcqMention(Base):
    __tablename__ = "acq_mentions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(40), index=True)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(24))
    url: Mapped[str] = mapped_column(Text, default="")
    host: Mapped[str] = mapped_column(String(120), default="")
    features: Mapped[dict] = mapped_column(JSONType, default=dict)
    links: Mapped[dict] = mapped_column(JSONType, default=dict)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    entity_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    resolution: Mapped[dict] = mapped_column(JSONType, default=dict)
    observed_at: Mapped[datetime] = _ts()


class AcqClaim(Base):
    __tablename__ = "acq_claims"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(40), index=True)
    attribute: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    value_key: Mapped[str] = mapped_column(String(200), default="")
    source_host: Mapped[str] = mapped_column(String(120), default="")
    source_kind: Mapped[str] = mapped_column(String(24), default="portal")
    document_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mention_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    url: Mapped[str] = mapped_column(Text, default="")
    observed_at: Mapped[datetime] = _ts()
    ttl_s: Mapped[float] = mapped_column(Float, default=86400.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    extractor: Mapped[str] = mapped_column(String(60), default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="active")   # active|retracted


class AcqConflict(Base):
    __tablename__ = "acq_conflicts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)     # entity:attribute
    entity_id: Mapped[str] = mapped_column(String(40), index=True)
    attribute: Mapped[str] = mapped_column(String(64))
    hypotheses: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(12), default="open")   # open|resolved
    detected_at: Mapped[datetime] = _ts()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AcqLink(Base):
    """Every entity-resolution decision, including the ones that did *not* merge."""

    __tablename__ = "acq_links"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mention_id: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[str] = mapped_column(String(40), index=True)
    probability: Mapped[float] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(16))   # merged|new|ambiguous
    features: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _ts()


class AcqJob(Base):
    __tablename__ = "acq_jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(40))
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|done|failed|skipped|blocked
    priority: Mapped[float] = mapped_column(Float, default=1.0)
    result: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AcqSourceRecipe(Base):
    """A source Regent knows how to use for a domain in a geography -- seeded by a country
    pack or learned by discovery -- with how to reach listings and how well that worked."""

    __tablename__ = "acq_source_recipes"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)          # domain:scope:host
    domain: Mapped[str] = mapped_column(String(32), index=True)
    host: Mapped[str] = mapped_column(String(120), index=True)
    scope: Mapped[str] = mapped_column(String(8), index=True)               # ISO country code or "*" (global)
    kind: Mapped[str] = mapped_column(String(24), default="portal")          # portal|aggregator|operator|hostel|...
    entry_url: Mapped[str] = mapped_column(Text)
    nav: Mapped[list] = mapped_column(JSONType, default=list)                # anchor terms, "{city}" templated
    hints: Mapped[dict] = mapped_column(JSONType, default=dict)              # {"prefer": [...], "avoid": [...]}
    render: Mapped[str] = mapped_column(String(12), default="auto")
    origin: Mapped[str] = mapped_column(String(40), default="pack")          # pack:XX|discovered:<channel>|search
    status: Mapped[str] = mapped_column(String(16), default="candidate")     # candidate|verified|blocked|rejected
    evidence: Mapped[dict] = mapped_column(JSONType, default=dict)           # classification + learned trails
    records: Mapped[int] = mapped_column(Integer, default=0)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _ts()


Index("ix_acq_claims_entity_attr", AcqClaim.entity_id, AcqClaim.attribute)
