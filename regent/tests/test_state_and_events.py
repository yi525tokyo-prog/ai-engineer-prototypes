"""Persistent state, event ingestion, reconstruction from events/snapshots."""

from sqlalchemy import select

from regent import db as dbm
from regent.core.observe.events import EventStore
from regent.core.world.projector import rebuild, serialize_projection, take_snapshot
from regent.core.world.state import WorldView, what_changed
from regent.db import Entity, Event, Fact, Relation, Resource


def _ingest(db):
    es = EventStore(db)
    es.append("entity_upserted", {"id": "org_a", "kind": "organization", "name": "Acme"})
    es.append("entity_upserted", {"id": "p_1", "kind": "person", "name": "Ann", "attrs": {"known_contact": True},
                                  "relations": [{"rel": "works_for", "dst": "org_a"}]})
    es.append("email_received", {"id": "msg_1", "from": {"id": "p_1", "name": "Ann"}, "subject": "Hello",
                                 "body": "Can we meet?"})
    es.append("calendar_event_changed", {"id": "ev_1", "title": "Meeting", "start": "2026-10-01T10:00:00+09:00",
                                         "format": "remote", "attendees": ["p_1"]})
    es.append("resource_changed", {"id": "cash", "kind": "money", "name": "Cash", "unit": "JPY", "balance": 1000,
                                   "attrs": {"monthly_burn": 500}})
    es.append("resource_spent", {"resource_id": "cash", "amount": 200, "reason": "coffee"})
    es.append("user_moved", {"place_id": "place_x"})
    es.append("some_unknown_signal", {"x": 1})
    db.commit()


def test_event_ingestion_projects_world(db):
    _ingest(db)
    w = WorldView.load(db)
    assert w.entities["p_1"].kind == "person"
    msg = w.entities["msg_1"]
    assert msg.kind == "message" and msg.attrs["answered"] is False
    assert w.fact("message.msg_1.answered") is False
    assert w.fact("event.ev_1.format") == "remote"
    assert w.fact("user.location") == "place_x"
    assert any(r.src_id == "msg_1" and r.rel == "sent_by" and r.dst_id == "p_1" for r in w.relations)
    assert any(r.src_id == "p_1" and r.rel == "works_for" for r in w.relations)
    assert w.resources["cash"].balance == 800
    assert w.runway_months() == 1.6
    # unknown event types are stored, never dropped
    assert db.scalar(select(Event).where(Event.type == "some_unknown_signal")) is not None


def test_state_persists_across_sessions(db):
    _ingest(db)
    s2 = dbm.session()
    try:
        assert s2.get(Entity, "msg_1") is not None
        assert s2.get(Resource, "cash").balance == 800
        assert EventStore(s2).head() == EventStore(db).head()
    finally:
        s2.close()


def test_message_sent_marks_answered(db):
    _ingest(db)
    EventStore(db).append("message_sent", {"message_id": "out_1", "in_reply_to": "msg_1"})
    w = WorldView.load(db)
    assert w.entities["msg_1"].attrs["answered"] is True
    assert w.fact("message.msg_1.answered") is True
    assert w.unanswered_messages() == []


def test_rebuild_from_events_reproduces_projection(db):
    _ingest(db)
    before = serialize_projection(db)
    n = rebuild(db)
    db.flush()
    after = serialize_projection(db)
    assert n == EventStore(db).head()
    strip = lambda rows: sorted(({k: v for k, v in r.items() if k not in ("created_at", "updated_at", "id")}  # noqa: E731
                                 for r in rows), key=str)
    for table in ("entities", "relations", "facts", "resources"):
        assert strip(before[table]) == strip(after[table]), table


def test_rebuild_from_snapshot_and_diff(db):
    _ingest(db)
    snap = take_snapshot(db, "t")
    EventStore(db).append("fact_observed", {"key": "k.new", "value": 42})
    snap2 = take_snapshot(db, "t2")
    diff = what_changed(db, snap.id, snap2.id)
    assert "k.new" in [f["key"] for f in diff["facts"]["added"]]
    replayed = rebuild(db, from_snapshot=snap.id)
    assert replayed == 1  # only the event after the snapshot
    assert db.get(Fact, "k.new").value == 42
    assert db.get(Entity, "msg_1") is not None
    assert db.scalar(select(Relation).where(Relation.src_id == "msg_1")) is not None


def test_tool_outcomes_learn_reliability(db):
    es = EventStore(db)
    es.append("tool_succeeded", {"tool": "search"})
    es.append("tool_failed", {"tool": "search"})
    es.append("tool_succeeded", {"tool": "search"})
    f = db.get(Fact, "tool.search.reliability")
    assert f.value["ok"] == 2 and f.value["fail"] == 1
    assert f.value["estimate"] == round(3 / 5, 3)
