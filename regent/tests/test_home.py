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
    monkeypatch.setattr(home, "_route_now", lambda mid: None)
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
    ok = client.get("/?key=" + "k" * 32, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/"       # the key does not stay in the address bar
    assert "regent_key" in ok.headers.get("set-cookie", "")
    assert client.get("/").status_code == 200
    assert client.get("/api/home").status_code == 200                    # the cookie carries it from then on
    assert TestClient(client.app).get("/healthz").status_code == 200     # the host's health check needs no key
    monkeypatch.delenv("REGENT_ACCESS_KEY")


def test_apps_open_on_their_own_address_when_hosted(client, monkeypatch):
    from regent.api import appgate

    monkeypatch.setenv("REGENT_ACCESS_KEY", "k" * 32)
    apps = {"x-regent-surface": "apps"}
    other = TestClient(client.app)
    assert other.get("/", headers=apps).status_code == 401              # no key, no app
    r = other.get("/__open/reading-log?key=" + "k" * 32, headers=apps, follow_redirects=False)
    assert r.status_code == 303 and "regent_app=reading-log" in r.headers.get("set-cookie", "")
    monkeypatch.setattr(appgate, "_port", lambda slug, fresh=False: None)
    assert other.get("/", headers=apps).status_code == 404               # an app Regent does not have
    token = appgate.APPS_ORIGIN.set("https://regent-apps.example.workers.dev")
    try:
        assert appgate.open_url("reading-log", "http://127.0.0.1:5000") == \
            "https://regent-apps.example.workers.dev/__open/reading-log?key=" + "k" * 32
    finally:
        appgate.APPS_ORIGIN.reset(token)
    assert appgate.open_url("reading-log", "http://127.0.0.1:5000") == "http://127.0.0.1:5000"   # on this computer
    monkeypatch.delenv("REGENT_ACCESS_KEY")


def test_workers_sign_in_as_the_person_without_their_setup(monkeypatch, tmp_path):
    from regent.software.claude_env import claude_env

    for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
              "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"):
        monkeypatch.delenv(k, raising=False)
    env, extra = claude_env(tmp_path)            # their own login, where it lives; their settings switched off
    assert env["HOME"] != str(tmp_path) and "--strict-mcp-config" in extra
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    env, extra = claude_env(tmp_path)            # a token: a home of its own
    assert env["HOME"] == str(tmp_path) and env["CLAUDE_CODE_OAUTH_TOKEN"] == "t" and extra == []


def test_hosted_regent_keeps_its_data_across_containers(tmp_path):
    import sqlite3

    from regent import cloud

    home = tmp_path / "home"
    (home / "workspace" / "apps" / "a").mkdir(parents=True)
    (home / "workspace" / "apps" / "a" / "app.py").write_text("print('hi')")
    (home / "workspace" / "apps" / "a" / ".home").mkdir()
    (home / "workspace" / "apps" / "a" / ".home" / "junk").write_text("x")
    con = sqlite3.connect(home / "regent.db")
    con.execute("pragma journal_mode=wal")
    con.execute("create table t (x)")
    con.execute("insert into t values (42)")
    con.commit()                                  # still open: the copy must come from SQLite, not the raw file
    data = cloud.pack(home)
    con.close()
    fresh = tmp_path / "fresh"
    cloud.unpack(data, fresh)
    assert (fresh / "workspace" / "apps" / "a" / "app.py").read_text() == "print('hi')"
    assert not (fresh / "workspace" / "apps" / "a" / ".home").exists()
    assert sqlite3.connect(fresh / "regent.db").execute("select x from t").fetchone() == (42,)
    assert cloud.fingerprint(home) != () and cloud.fingerprint(fresh) != ()


def test_a_question_to_think_through_gets_a_direct_answer(client, db, monkeypatch):
    from regent.core.goals.missions import MissionGraph
    from regent.software import reply as R
    from regent.software.domain import SoftwareAdapter

    class Said:
        output = {"reply": "主な方法は三つあります。", "language": "ja", "unsure": ["UBIの効果は議論が分かれます"]}
        cost_usd = 0.01

    class Thinker:
        def ask(self, task, *a, **k):
            assert task == "reply"
            return Said()

    monkeypatch.setattr(R, "get_reasoner", lambda: Thinker())
    m = MissionGraph(db).create(title="x", objective="世界の労働をなくす方法を教えて")
    SoftwareAdapter.__new__(SoftwareAdapter)._adopt(db, m, {"handled_as": "conversation", "sentence": m.objective})
    db.commit()
    [item] = client.get("/api/home").json()["items"]
    assert item["state"] == "done" and item["result"]["kind"] == "reply"
    assert item["result"]["text"].startswith("主な方法") and item["result"]["unsure"]


class _Said:
    def __init__(self, output):
        self.output, self.cost_usd = output, 0.0


class _FrontDoor:
    def __init__(self, output):
        self.out = output

    def ask(self, task, *a, **k):
        assert task == "route"
        return _Said(self.out)


def _routed(db, monkeypatch, sentence, output):
    from regent.core.goals.missions import MissionGraph
    from regent.software import router as R

    monkeypatch.setattr(R, "get_reasoner", lambda: _FrontDoor(output))
    m = MissionGraph(db).create(title=sentence, objective=sentence,
                                attrs={"route": "pending", "timezone": "Asia/Tokyo"})
    outcome = R.route_mission(db, m)
    db.commit()
    return m, outcome


def test_each_sentence_is_routed_by_what_it_should_make_happen(client, db, monkeypatch):
    from regent import reminders as RM
    from regent.software.router import shape_need

    # answer: replied at the front door, finished
    m, out = _routed(db, monkeypatch, "世界の労働をなくす方法を教えて",
                     {"mode": "answer", "language": "ja", "why": "think", "reply": "三つの道があります。", "unsure": []})
    assert out == "handled" and m.status == "completed" and m.attrs["reply"]["text"].startswith("三つ")
    # remember: kept, and a dated appointment is mentioned on the morning of the day
    m, out = _routed(db, monkeypatch, "10月15日に野田さんと面談", {
        "mode": "remember", "language": "ja", "why": "keep", "remember": {
            "note": "10月15日 野田さんと面談", "date_local": "2099-10-15", "time_local": None,
            "remind_at_local": "2099-10-15T08:00", "daily_at_local": None, "message": "今日は野田さんと面談です"}})
    assert out == "handled" and m.attrs["remembered"]["note"].startswith("10月15日")
    [r] = RM.for_mission(db, m.id)
    assert r.text == "今日は野田さんと面談です" and r.active
    item = next(i for i in client.get("/api/home").json()["items"] if i["id"] == m.id)
    assert item["state"] == "done" and item["result"]["kind"] == "remembered"
    # investigate / watch / act continue into the longer path, shaped by what should happen
    m, out = _routed(db, monkeypatch, "日本の失業率いま何%？", {"mode": "investigate", "language": "ja", "why": "look"})
    assert out == "continue" and m.attrs["mode"] == "investigate" and m.status != "completed"
    need = {"handled_as": "software_capability", "deliverable": {"form": "glance_view", "refresh": "daily"}}
    assert shape_need(need, "investigate")["deliverable"] == {"form": "answer_once", "refresh": "once"}
    assert shape_need(need, "watch")["deliverable"]["form"] == "alert"


def test_a_watch_speaks_only_when_what_was_asked_about_changes():
    from regent.software import capability as K
    from regent.software.tables import SwCapability

    cap = SwCapability(id="c1", slug="jobless", title="t", spec={"delivery": {"schedule": "on_change"}}, provenance={})

    def look(value, display):
        return {"metrics": [{"label": "Above 5%?", "display": display, "value": value, "form": "decision",
                             "headline": 1}]}

    assert K.change_worth_telling(cap, look(False, "No")) is None        # first look, nothing happening: silent
    assert K.change_worth_telling(cap, look(False, "No")) is None        # unchanged: silent
    assert cap.provenance["watched"] == {"Above 5%?": "No"}              # ...but every look is recorded
    assert K.change_worth_telling(cap, look(True, "Yes")) == "Yes"       # it happened: say so, once
    assert K.change_worth_telling(cap, look(True, "Yes")) is None
