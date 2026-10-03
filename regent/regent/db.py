"""Persistent data model.

PostgreSQL is the primary store (JSONB + pgvector). SQLite is supported as a
fallback for quick local runs; the schema is identical apart from column types.

Tables fall into three groups:

* the append-only **event store** (``events``) -- the source of truth for world state;
* **projections** rebuilt from events (entities, relations, facts, resources,
  ledger, constitution, authority grants, capabilities, skills, global facts);
* **operational state** owned by the loop (missions, routes, operations,
  interrupts, evidence, decisions, model calls). Every change to operational
  state is also written to the event store and the decision log, so history is
  never lost even though these rows are mutated in place.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from regent.config import settings
from regent.ids import monotonic_now

EMBEDDING_DIM = 256

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Embedding(TypeDecorator):
    """pgvector ``vector(N)`` on PostgreSQL, JSON array elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(EMBEDDING_DIM))
        return dialect.type_descriptor(JSON())

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in value]


class AwareDateTime(TypeDecorator):
    """Timestamps are always timezone-aware UTC, whatever the database returns (SQLite drops the
    zone; comparing those with aware times breaks)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if isinstance(value, str):          # events carry ISO strings
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def _ts() -> Mapped[datetime]:
    return mapped_column(AwareDateTime(), default=monotonic_now)


class Base(DeclarativeBase):
    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for col in self.__table__.columns:
            v = getattr(self, col.key)
            if isinstance(v, datetime):
                v = v.isoformat()
            out[col.key] = v
        return out


# --------------------------------------------------------------- event store


class Event(Base):
    __tablename__ = "events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    source: Mapped[str] = mapped_column(String(80), default="user")
    mission_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    domain: Mapped[str] = mapped_column(String(16), default="private")
    causation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = _ts()


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    event_seq: Mapped[int] = mapped_column(Integer, index=True)
    reason: Mapped[str] = mapped_column(String(120), default="")
    state: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _ts()


# --------------------------------------------------------------- projections


class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(200))
    attrs: Mapped[dict] = mapped_column(JSONType, default=dict)
    domain: Mapped[str] = mapped_column(String(16), default="private")
    version: Mapped[int] = mapped_column(Integer, default=1)
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class Relation(Base):
    __tablename__ = "relations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    src_id: Mapped[str] = mapped_column(String(64), index=True)
    rel: Mapped[str] = mapped_column(String(40), index=True)
    dst_id: Mapped[str] = mapped_column(String(64), index=True)
    attrs: Mapped[dict] = mapped_column(JSONType, default=dict)
    domain: Mapped[str] = mapped_column(String(16), default="private")
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _ts()


class Fact(Base):
    __tablename__ = "facts"
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    evidence_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source: Mapped[str] = mapped_column(String(80), default="")
    domain: Mapped[str] = mapped_column(String(16), default="private")
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = _ts()


class Resource(Base):
    __tablename__ = "resources"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))  # money|compute|api_spend|time|storage|subscription|infrastructure
    name: Mapped[str] = mapped_column(String(120))
    unit: Mapped[str] = mapped_column(String(16))
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    attrs: Mapped[dict] = mapped_column(JSONType, default=dict)
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = _ts()


class LedgerEntry(Base):
    __tablename__ = "ledger"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(64), index=True)
    amount: Mapped[float] = mapped_column(Float)
    operation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mission_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason: Mapped[str] = mapped_column(String(200), default="")
    event_seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _ts()


class ConstitutionItem(Base):
    __tablename__ = "constitution"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(24))  # hard_constraint|strong_preference|weak_preference|priority|conflict
    statement: Mapped[str] = mapped_column(Text)
    dimension: Mapped[str | None] = mapped_column(String(40), nullable=True)
    direction: Mapped[float] = mapped_column(Float, default=1.0)
    rule: Mapped[dict] = mapped_column(JSONType, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    alpha: Mapped[float] = mapped_column(Float, default=1.0)
    beta: Mapped[float] = mapped_column(Float, default=1.0)
    sources: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(16), default="active")
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = _ts()


class AuthorityGrant(Base):
    __tablename__ = "authority_grants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(80), index=True)  # "email.send", "calendar.*", ...
    level: Mapped[str] = mapped_column(String(12))  # AUTO|COMMIT|IDENTITY
    granted: Mapped[bool] = mapped_column(Boolean, default=True)
    constraints: Mapped[dict] = mapped_column(JSONType, default=dict)
    note: Mapped[str] = mapped_column(String(200), default="")
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = _ts()


class Capability(Base):
    __tablename__ = "capabilities"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="missing")  # available|missing|acquiring|degraded
    provided_by: Mapped[list] = mapped_column(JSONType, default=list)
    acquisition_mission_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    attrs: Mapped[dict] = mapped_column(JSONType, default=dict)
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = _ts()


class Skill(Base):
    __tablename__ = "skills"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain: Mapped[str] = mapped_column(String(16), default="private")
    name: Mapped[str] = mapped_column(String(160))
    task_pattern: Mapped[str] = mapped_column(Text)
    preconditions: Mapped[list] = mapped_column(JSONType, default=list)
    procedure: Mapped[list] = mapped_column(JSONType, default=list)
    failure_modes: Mapped[list] = mapped_column(JSONType, default=list)
    verification: Mapped[dict] = mapped_column(JSONType, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    provenance: Mapped[list] = mapped_column(JSONType, default=list)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    last_verified: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    created_at: Mapped[datetime] = _ts()


class GlobalFact(Base):
    __tablename__ = "global_facts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain: Mapped[str] = mapped_column(String(16), default="global")
    subject: Mapped[str] = mapped_column(String(160), index=True)
    statement: Mapped[str] = mapped_column(Text)
    value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    provenance: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = _ts()


class Memory(Base):
    __tablename__ = "memory"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    domain: Mapped[str] = mapped_column(String(16), default="private")
    kind: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(Embedding(), nullable=True)
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    mission_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = _ts()


# --------------------------------------------------------- operational state


class Mission(Base):
    __tablename__ = "missions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(String(40), ForeignKey("missions.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    objective: Mapped[str] = mapped_column(Text)
    success_criteria: Mapped[list] = mapped_column(JSONType, default=list)
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(20), default="active")
    phase: Mapped[str] = mapped_column(String(40), default="observe")
    value_scale: Mapped[float] = mapped_column(Float, default=1.0)
    horizon_days: Mapped[float] = mapped_column(Float, default=30.0)
    selected_route_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_event_seq_seen: Mapped[int] = mapped_column(Integer, default=0)
    tick_count: Mapped[int] = mapped_column(Integer, default=0)
    attrs: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class Route(Base):
    __tablename__ = "routes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mission_id: Mapped[str] = mapped_column(String(40), ForeignKey("missions.id"), index=True)
    key: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    thesis: Mapped[str] = mapped_column(Text)
    archetype: Mapped[str] = mapped_column(String(40), default="")
    generated_by: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(16), default="alive")  # alive|selected|invalidated|abandoned|completed
    estimates: Mapped[dict] = mapped_column(JSONType, default=dict)
    effective: Mapped[dict] = mapped_column(JSONType, default=dict)
    estimate_sources: Mapped[dict] = mapped_column(JSONType, default=dict)
    sensitivities: Mapped[list] = mapped_column(JSONType, default=list)
    applied_sensitivities: Mapped[list] = mapped_column(JSONType, default=list)
    dependencies: Mapped[list] = mapped_column(JSONType, default=list)
    blockers: Mapped[list] = mapped_column(JSONType, default=list)
    required_capabilities: Mapped[list] = mapped_column(JSONType, default=list)
    operation_specs: Mapped[list] = mapped_column(JSONType, default=list)
    uncertainty: Mapped[list] = mapped_column(JSONType, default=list)
    critiques: Mapped[list] = mapped_column(JSONType, default=list)
    evidence_ids: Mapped[list] = mapped_column(JSONType, default=list)
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[dict] = mapped_column(JSONType, default=dict)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    invalidated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class RouteScore(Base):
    __tablename__ = "route_scores"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mission_id: Mapped[str] = mapped_column(String(40), index=True)
    route_id: Mapped[str] = mapped_column(String(40), index=True)
    tick: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer)
    effective: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _ts()


class Operation(Base):
    __tablename__ = "operations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mission_id: Mapped[str] = mapped_column(String(40), index=True)
    route_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    key: Mapped[str] = mapped_column(String(120))
    goal: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="step")  # step|probe|acquire|verify
    executor: Mapped[str] = mapped_column(String(24))  # llm|search|browser|code|api|connector|os|agent|human
    tool: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(60))
    required_authority: Mapped[str] = mapped_column(String(12), default="AUTO")
    authority_decision: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    inputs: Mapped[dict] = mapped_column(JSONType, default=dict)
    outputs: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_s: Mapped[float] = mapped_column(Float, default=60.0)
    retry_policy: Mapped[dict] = mapped_column(JSONType, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    attempt_log: Mapped[list] = mapped_column(JSONType, default=list)
    verification: Mapped[dict] = mapped_column(JSONType, default=dict)
    verification_result: Mapped[dict] = mapped_column(JSONType, default=dict)
    cost_estimate: Mapped[dict] = mapped_column(JSONType, default=dict)
    actual_cost: Mapped[dict] = mapped_column(JSONType, default=dict)
    evidence_ids: Mapped[list] = mapped_column(JSONType, default=list)
    depends_on: Mapped[list] = mapped_column(JSONType, default=list)
    resolves: Mapped[list] = mapped_column(JSONType, default=list)
    emits: Mapped[dict] = mapped_column(JSONType, default=dict)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = _ts()
    started_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)


class HumanInterrupt(Base):
    __tablename__ = "human_interrupts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mission_id: Mapped[str] = mapped_column(String(40), index=True)
    operation_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(20))  # identity|authorization|physical|information
    reason: Mapped[str] = mapped_column(Text)
    required_action: Mapped[str] = mapped_column(Text)
    estimated_time_seconds: Mapped[int] = mapped_column(Integer, default=30)
    blocking_operation: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resume_condition: Mapped[dict] = mapped_column(JSONType, default=dict)
    response_schema: Mapped[dict] = mapped_column(JSONType, default=dict)
    context: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|resolved|cancelled
    resolution: Mapped[str | None] = mapped_column(String(40), nullable=True)
    response: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _ts()
    resolved_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mission_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    route_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    operation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(24))  # tool_result|human_report|observation|verification|model_estimate
    claim: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    source: Mapped[str] = mapped_column(String(80))
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    fact_keys: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = _ts()


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mission_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    tick: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    event_seq: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text)
    routes_considered: Mapped[list] = mapped_column(JSONType, default=list)
    selected_route_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    previous_route_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rationale: Mapped[dict] = mapped_column(JSONType, default=dict)
    evidence_ids: Mapped[list] = mapped_column(JSONType, default=list)
    model_outputs: Mapped[list] = mapped_column(JSONType, default=list)
    authority: Mapped[dict] = mapped_column(JSONType, default=dict)
    outcome: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _ts()


class ModelCall(Base):
    __tablename__ = "model_calls"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    task: Mapped[str] = mapped_column(String(40))
    mission_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    output_summary: Mapped[str] = mapped_column(Text, default="")
    output: Mapped[Any] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = _ts()


class BuiltTool(Base):
    """Tools Regent built for itself during capability acquisition."""

    __tablename__ = "built_tools"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    capability_id: Mapped[str] = mapped_column(String(64))
    spec: Mapped[dict] = mapped_column(JSONType, default=dict)
    code_path: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts()


class ConnectorRecord(Base):
    """State for local connector backends (mail outbox, calendar, portal, bookings)."""

    __tablename__ = "connector_records"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    connector: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _ts()


Index("ix_ops_mission_status", Operation.mission_id, Operation.status)


# ------------------------------------------------------------ engine / session

_engine: Engine | None = None
@event.listens_for(Session, "before_flush")
def _merge_mission_attrs(session: Session, flush_context: Any, instances: Any) -> None:
    """A request's attrs are written by several hands at once (the front door, the loop, tools in
    their own sessions). Writing a whole stale copy would erase what another wrote meanwhile, so a
    write applies only the keys this session changed, on top of what is stored now."""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import select

    for obj in list(session.dirty):
        if not isinstance(obj, Mission):
            continue
        hist = sa_inspect(obj).attrs.attrs.history
        if not hist.has_changes() or not hist.deleted:
            continue                            # new object, or nothing known about what was there before
        before = hist.deleted[0] or {}
        after = obj.attrs or {}
        stored = session.connection().execute(select(Mission.attrs).where(Mission.id == obj.id)).scalar() or {}
        merged = {**stored, **{k: v for k, v in after.items() if before.get(k) != v}}
        for k in before:
            if k not in after:
                merged.pop(k, None)
        if merged != after:
            obj.attrs = merged


@event.listens_for(Session, "after_flush")
def _writes_began(session: Session, flush_context: Any) -> None:
    """From the first write until commit, SQLite lets no one else write: note where that started."""
    if "writing_since" not in session.info:
        import time
        import traceback

        session.info["writing_since"] = time.time()
        session.info["writing_from"] = "".join(traceback.format_stack(limit=14)[:-3])


def _writes_ended(session: Session, *_: Any) -> None:
    import time

    since = session.info.pop("writing_since", None)
    where = session.info.pop("writing_from", "")
    if since is not None and time.time() - since > 5:
        import logging

        logging.getLogger("regent.db").warning("held the database for %.1fs; writing began at:\n%s",
                                               time.time() - since, where)


event.listen(Session, "after_commit", _writes_ended)
event.listen(Session, "after_rollback", _writes_ended)


_SessionLocal: sessionmaker | None = None
_lock = threading.RLock()


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 60})

        @event.listens_for(eng, "connect")
        def _pragma(dbapi_conn, _):  # pragma: no cover - sqlite only
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=60000")
            cur.close()

        return eng
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)


def configure(url: str | None = None) -> Engine:
    global _engine, _SessionLocal
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = make_engine(url or settings.database_url)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
        return _engine


def engine() -> Engine:
    if _engine is None:
        configure()
    assert _engine is not None
    return _engine


def session() -> Session:
    if _SessionLocal is None:
        configure()
    assert _SessionLocal is not None
    return _SessionLocal()


def init_db(drop: bool = False) -> None:
    import regent.acquisition.tables  # noqa: F401  (register acquisition tables)
    import regent.software.tables  # noqa: F401  (register software capability tables)
    import regent.reminders  # noqa: F401  (register reminders)

    eng = engine()
    if eng.dialect.name == "postgresql":
        with eng.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    if drop:
        Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    _add_missing_columns(eng)


def _add_missing_columns(eng) -> None:
    """Additive schema evolution: columns added to a model after its table was created are
    added as nullable columns (never dropped or altered)."""
    from sqlalchemy import inspect

    insp = inspect(eng)
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    ddl = col.type.compile(dialect=eng.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}'))


