"""Persistence for software capabilities Regent creates and keeps using."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from regent.db import Base, JSONType, _ts, AwareDateTime


class SwCapability(Base):
    """A capability Regent owns: what need it answers, how it is built, whether it is usable."""

    __tablename__ = "sw_capabilities"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    mission_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    signature: Mapped[list] = mapped_column(JSONType, default=list)      # need signatures it answers
    need: Mapped[dict] = mapped_column(JSONType, default=dict)
    implementation: Mapped[str] = mapped_column(String(24), default="composed")  # composed|external|delegated
    status: Mapped[str] = mapped_column(String(16), default="draft")   # draft|built|usable|degraded|failed|retired
    version: Mapped[int] = mapped_column(Integer, default=0)
    spec: Mapped[dict] = mapped_column(JSONType, default=dict)
    verification: Mapped[dict] = mapped_column(JSONType, default=dict)
    provenance: Mapped[dict] = mapped_column(JSONType, default=dict)
    tool_name: Mapped[str] = mapped_column(String(80), default="")
    coverage: Mapped[float] = mapped_column(Float, default=0.0)
    last_collect_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class SwCapabilityVersion(Base):
    __tablename__ = "sw_capability_versions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    capability_id: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict] = mapped_column(JSONType, default=dict)
    verification: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="built")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _ts()


class SwObservation(Base):
    """One reading of one source by a capability (aggregate numbers only, never identities)."""

    __tablename__ = "sw_observations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    capability_id: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")      # ok|blocked|error
    fields: Mapped[dict] = mapped_column(JSONType, default=dict)
    url: Mapped[str] = mapped_column(Text, default="")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocker: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    observed_at: Mapped[datetime] = _ts()
