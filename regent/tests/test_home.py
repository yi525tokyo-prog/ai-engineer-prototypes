"""The page a person uses: say what you want, see what is happening in plain words, answer only
what needs you. These tests hold the product contract, not the machinery behind it."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(db, monkeypatch):
    from regent.api import app as appmod
    from regent.api import home

    monkeypatch.setattr(home, "_kick", lambda bg: None)      # no loop run: these tests look at the surface
    monkeypatch.setattr(appmod.settings, "background_loop", False)
    with TestClient(appmod.app) as c:
        yield c


def test_one_place_to_say_what_you_want(client):
    page = client.get("/")
    assert page.status_code == 200 and 'id="intent"' in page.text and "Hand it over" in page.text
    assert client.get("/api/home").json()["items"] == []
    r = client.post("/api/intent", json={"text": "Tell me tomorrow at 9 to water the plants.", "timezone": "Asia/Tokyo"})
    [item] = client.get("/api/home").json()["items"]
    assert item["id"] == r.json()["id"] and item["asked"].startswith("Tell me tomorrow")
    assert item["state"] == "working" and item["now"]                       # plain words, never a tool name
    assert "." not in item["now"].split()[0]


def test_questions_are_plain_and_answerable(client, db):
    from regent.core.goals.missions import MissionGraph
    from regent.core.human.interrupts import HumanInterruptManager
    from regent.db import Operation

    m = MissionGraph(db).create(title="x", objective="Keep my reading notes somewhere private.")
    op = Operation(id="op_b", mission_id=m.id, key="sw.build", goal="build", executor="code", tool="software",
                   action="build_app", required_authority="COMMIT", status="waiting_human")
    db.add(op)
    db.flush()
    HumanInterruptManager(db).for_authorization(op, "needs approval")
    db.commit()
    [q] = client.get("/api/home").json()["questions"]
    assert q["title"] == "Build a small private app for this?" and "software." not in q["title"] + q["body"]
    assert [a["id"] for a in q["actions"]] == ["approve", "deny"]
    assert client.post(f"/api/questions/{q['id']}", json={"action": "approve"}).json()["ok"]
    db.expire_all()
    assert client.get("/api/home").json()["questions"] == []
    assert (db.get(Operation, "op_b").authority_decision or {}).get("approved_once")


def test_a_reminder_is_delivered_by_regent_itself_at_its_time(client, db):
    from regent import reminders as RM
    from regent.core.goals.missions import MissionGraph
    from regent.ids import utcnow

    m = MissionGraph(db).create(title="r", objective="Remind me to call my mother on Sunday.")
    soon = (utcnow() + timedelta(minutes=5)).astimezone(RM._zone("Asia/Tokyo")).strftime("%Y-%m-%dT%H:%M")
    r = RM.schedule(db, m.id, {"message": "Call your mother.", "once_at_local": soon}, "Asia/Tokyo")
    db.commit()
    [item] = client.get("/api/home").json()["items"]
    assert item["result"]["kind"] == "reminder" and item["result"]["items"][0]["active"]
    assert RM.deliver_due(db) == []                                     # not yet
    r.due_at = utcnow() - timedelta(seconds=1)
    assert RM.deliver_due(db) == [r.id]
    db.commit()
    home = client.get("/api/home").json()
    assert home["messages"][0]["text"] == "Call your mother." and not home["items"][0]["result"]["items"][0]["active"]
    with pytest.raises(ValueError):                                     # a time in the past is refused, not guessed
        RM.schedule(db, m.id, {"message": "x", "once_at_local": "2001-01-01T09:00"}, "Asia/Tokyo")
    daily = RM.schedule(db, m.id, {"message": "Water the plants.", "daily_at_local": "07:30"}, "Asia/Tokyo")
    assert daily.due_at > utcnow() and daily.due_at.astimezone(RM._zone("Asia/Tokyo")).strftime("%H:%M") == "07:30"


def test_what_regent_cannot_do_is_said_plainly(client, db):
    from regent.core.goals.missions import MissionGraph

    m = MissionGraph(db).create(title="x", objective="Fold my laundry.")
    m.attrs = {"need": {"handled_as": "other"}, "unsupported": {"why": "This isn't something Regent can take on yet."}}
    m.status = "abandoned"
    db.commit()
    [item] = client.get("/api/home").json()["items"]
    assert item["state"] == "cannot" and "can take on yet" in item["now"]


def test_a_remote_regent_needs_its_key(client, monkeypatch):
    monkeypatch.setenv("REGENT_ACCESS_KEY", "k" * 32)
    assert client.get("/api/home").status_code == 401
    assert client.get("/").status_code == 401
    ok = client.get("/?key=" + "k" * 32)
    assert ok.status_code == 200 and "regent_key" in ok.headers.get("set-cookie", "")
    assert client.get("/api/home").status_code == 200                    # the cookie carries it from then on
    monkeypatch.delenv("REGENT_ACCESS_KEY")
