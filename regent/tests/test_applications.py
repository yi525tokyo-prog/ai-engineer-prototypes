"""The application lifecycle, offline: a scripted worker writes real (small) applications and
Regent's own pipeline -- inspect, test, stage, accept over HTTP, restart, repair, promote, upgrade
on a copy of live data, roll back -- decides what happens. No network, no model."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from regent.software import appaccept as A
from regent.software import appbuild as B
from regent.software import appdesign as D
from regent.software import appservice as S

SERVER = r'''
import json, os, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.environ["DATA_DIR"]
PASS = os.environ.get("APP_PASSPHRASE", "")
PERSIST = __PERSIST__
DROP_ON_START = __DROP__
os.makedirs(DATA, exist_ok=True)
STORE = os.path.join(DATA, "books.json")
TOKENS = os.path.join(DATA, "tokens.json")

def load(p, d):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return d

books = load(STORE, []) if PERSIST else []
if DROP_ON_START:
    books = books[1:]
tokens = load(TOKENS, [])

def save():
    if PERSIST:
        with open(STORE, "w") as f:
            json.dump(books, f)
    with open(TOKENS, "w") as f:
        json.dump(tokens, f)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def authed(self):
        h = self.headers.get("Authorization", "")
        return h.startswith("Bearer ") and h[7:] in tokens
    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")
    def do_GET(self):
        if self.path == "/api/health":
            return self.send(200, {"ok": True})
        if not self.authed():
            return self.send(401, {"error": "unauthorized"})
        if self.path == "/api/books":
            return self.send(200, {"books": books})
        self.send(404, {})
    def do_POST(self):
        if self.path == "/api/login":
            if self.body().get("passphrase") != PASS:
                return self.send(401, {"error": "wrong"})
            t = secrets.token_hex(8)
            tokens.append(t)
            save()
            return self.send(200, {"token": t})
        if not self.authed():
            return self.send(401, {"error": "unauthorized"})
        if self.path == "/api/books":
            b = self.body()
            b["id"] = len(books) + 1
            books.append(b)
            save()
            return self.send(201, b)
        self.send(404, {})

ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
'''



def write_app(ws: Path, *, persist: bool, drop_on_start: bool = False) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "server.py").write_text(SERVER.replace("__PERSIST__", str(persist)).replace("__DROP__", str(drop_on_start)))
    (ws / "test_server.py").write_text("def test_source_is_there():\n    assert open('server.py').read()\n")
    (ws / "regent.json").write_text(json.dumps({"name": "shelf", "version": 1, "build": None,
                                                "test": "python -c \"import ast;ast.parse(open('server.py').read())\"",
                                                "start": "python server.py", "health": "/api/health"}))


DESIGN = {
    "name": "shelf", "summary": "a private reading list", "entities": [{"name": "book"}],
    "auth": {"scheme": "passphrase"}, "external_hosts": [], "ui": [],
    "api": [{"id": "health", "method": "GET", "path": "/api/health", "purpose": "health", "public": True},
            {"id": "list_books", "method": "GET", "path": "/api/books", "purpose": "list"},
            {"id": "add_book", "method": "POST", "path": "/api/books", "purpose": "add"}],
    "scenarios": [
        {"id": "add_and_survive_restart", "requirement": "keep", "kind": "api", "steps": [
            {"do": "call", "api": "add_book", "body": {"title": "The Odyssey", "position": "book 3"},
             "expect_status": 201, "save": {"bid": "id"}},
            {"do": "restart_app"},
            {"do": "call", "api": "list_books", "expect_json_contains": {"title": "The Odyssey"}}]},
        {"id": "private", "requirement": "private", "kind": "negative", "steps": [
            {"do": "call", "api": "list_books", "auth": False, "expect_status": 401}]}],
}
NEED = {"sentence": "a private place to keep my reading", "requirements": [
    {"id": "keep", "capability": "keep books and where I stopped", "acceptance": "still there later", "priority": "core"},
    {"id": "private", "capability": "private", "acceptance": "nobody else can read it", "priority": "core"}]}


class ScriptedWorker:
    """Stands in for the coding agent: what it writes in each session is scripted."""

    def __init__(self, sessions):
        self.sessions = list(sessions)
        self.briefs: list[str] = []

    def run(self, ws: Path, brief, *, prompt="", budget_usd=None):
        if brief is not None:
            (ws / "BRIEF.md").write_text(brief)
        repairs = sorted(ws.glob("REPAIR-*.md"))
        self.briefs.append(repairs[-1].read_text() if repairs and "REPAIR" in prompt else (brief or ""))
        self.sessions.pop(0)(ws)
        return {"claimed_done": True, "claim": "done", "cost_usd": 0.0, "seconds": 0, "files": [], "files_changed": []}


@pytest.fixture()
def apps(tmp_path, monkeypatch):
    from regent.config import settings
    from regent.software import secrets

    monkeypatch.setattr(settings, "workspace", tmp_path)
    store: dict[str, str] = {}
    monkeypatch.setattr(secrets, "get", lambda k: store.get(k))
    monkeypatch.setattr(secrets, "put", lambda k, v: store.__setitem__(k, v))
    yield tmp_path
    S.stop_all()


def test_the_design_is_a_claim_regent_checks():
    assert D.check(DESIGN, NEED) == []
    open_design = {**DESIGN, "auth": {"scheme": "none"}, "scenarios": DESIGN["scenarios"][:1]}
    problems = D.check(open_design, NEED)
    assert any("credential" in p for p in problems) and any("negative" in p for p in problems)
    assert any("private" in p and "no acceptance scenario" in p for p in problems)
    shrunk = {**DESIGN, "api": DESIGN["api"][:2]}
    assert any("undeclared endpoint add_book" in p for p in D.check(shrunk, NEED))
    assert any("dropped" in p for p in D.check(shrunk, NEED, previous=DESIGN))


def test_a_broken_build_is_repaired_then_promoted_and_upgraded_without_losing_data(apps):
    # session 1: an app that forgets everything on restart; session 2 (repair): it persists
    worker = ScriptedWorker([lambda ws: write_app(ws, persist=False), lambda ws: write_app(ws, persist=True)])
    b = B.AppBuild(slug="shelf", need=NEED, design=DESIGN, sources=[], agent=worker, version=1, max_rounds=2)
    res = b.run()
    assert res["accepted"] and len(res["rounds"]) == 2
    first = res["rounds"][0]
    assert not first["passed"] and any(f["stage"] == "acceptance" and "add_and_survive_restart" in f["summary"]
                                       for f in first["failures"])
    assert "add_and_survive_restart" in worker.briefs[1]          # the failure went back to the worker
    assert res["rounds"][1]["acceptance"]["by_id"] == {"add_and_survive_restart": True, "private": True}
    assert D.requirement_coverage(DESIGN, NEED, res["rounds"][1]["acceptance"]["by_id"]) == 1.0

    promo = b.promote()
    assert promo["live"]
    live = S.get("shelf", "live")
    r = A.Runner(live, DESIGN, b.passphrase)
    assert r.call("add_book", None, {"title": "Moby-Dick", "position": "ch. 12"}).status_code == 201
    r.client.close()

    # v2 that drops a record on start: staging on a copy of the real data catches it, live is untouched
    prev = {"version": 1, "workspace": res["workspace"], "design": DESIGN, "port": promo["port"]}
    bad = B.AppBuild(slug="shelf", need=NEED, design=DESIGN, sources=[], agent=ScriptedWorker(
        [lambda ws: write_app(ws, persist=True, drop_on_start=True)]), version=2, previous=prev, max_rounds=0)
    bad_res = bad.run()
    assert not bad_res["accepted"]
    assert bad_res["rounds"][0]["migration"]["lost"]
    snap = A.snapshot(S.get("shelf", "live"), DESIGN, b.passphrase)
    assert any(x.get("title") == "Moby-Dick" for x in A.records(snap))

    # a correct v2 is promoted on the live data and keeps every record
    good = B.AppBuild(slug="shelf", need=NEED, design=DESIGN, sources=[], agent=ScriptedWorker(
        [lambda ws: write_app(ws, persist=True)]), version=3, previous=prev, max_rounds=0)
    assert good.run()["accepted"]
    p2 = good.promote()
    assert p2["live"] and p2["backup"] and p2["port"] == promo["port"]
    after = A.records(A.snapshot(S.get("shelf", "live"), DESIGN, b.passphrase))
    assert {x.get("title") for x in after} >= {"Moby-Dick"}


def test_promotion_rolls_back_when_live_records_are_lost(apps):
    worker = ScriptedWorker([lambda ws: write_app(ws, persist=True)])
    b = B.AppBuild(slug="shelf", need=NEED, design=DESIGN, sources=[], agent=worker, version=1, max_rounds=0)
    res = b.run()
    assert res["accepted"] and b.promote()["live"]
    r = A.Runner(S.get("shelf", "live"), DESIGN, b.passphrase)
    r.call("add_book", None, {"title": "The Odyssey"})
    r.call("add_book", None, {"title": "Moby-Dick"})
    r.client.close()
    prev = {"version": 1, "workspace": res["workspace"], "design": DESIGN}
    # pretend this version passed staging; promotion itself must still catch the loss and roll back
    v2 = B.AppBuild(slug="shelf", need=NEED, design=DESIGN, sources=[], agent=None, version=2, previous=prev)
    v2.passphrase = b.passphrase
    write_app(v2.ws, persist=True, drop_on_start=True)
    out = v2.promote()
    assert out["live"] is False and out["rolled_back"] and out["lost"]
    titles = {x.get("title") for x in A.records(A.snapshot(S.get("shelf", "live"), DESIGN, b.passphrase))}
    assert titles >= {"The Odyssey", "Moby-Dick"}


def test_inspection_rejects_what_the_contract_forbids(tmp_path):
    write_app(tmp_path, persist=True)
    assert B.inspect(tmp_path, DESIGN)["ok"]
    (tmp_path / "index.html").write_text(textwrap.dedent('''
        <script async src="https://www.googletagmanager.com/gtag/js?id=G-1"></script>
        <script>fetch("https://api.example.org/x")</script>'''))
    probs = B.inspect(tmp_path, DESIGN)["problems"]
    assert any("tracking" in p for p in probs) and any("api.example.org" in p for p in probs)
    (tmp_path / "regent.json").unlink()
    assert any("manifest" in p for p in B.inspect(tmp_path, DESIGN)["problems"])


def test_migration_check_compares_records_not_bytes():
    before = {"/api/books": {"books": [{"id": 1, "title": "The Odyssey", "updated_at": "t1"}]}}
    kept = {"/api/books": {"books": [{"id": 1, "title": "The Odyssey", "updated_at": "t2", "shared": False}]}}
    lost = {"/api/books": {"books": [{"id": 1, "title": "Odyssey"}]}}
    assert A.preserved(before, kept) == []
    assert A.preserved(before, lost)
    assert A.contains({"books": [{"title": "The Odyssey (Butler)"}]}, {"title": "odyssey"}) == []


def test_a_tool_need_gets_build_routes_even_without_public_data():
    from regent.software.routes import strategies

    need = {"requirements": NEED["requirements"]}
    inv = {"kind": "tool", "app_sources": [], "alternatives": [], "existing_capabilities": [], "constraints": []}
    keys = [r.key for r in strategies({"id": "m1"}, need, inv)]
    assert "software-build-app" in keys and "software-manual" in keys
    reuse = {"existing_capabilities": [{"id": "c1", "slug": "shelf", "title": "Shelf", "relation": "can_do"}]}
    assert [r.key for r in strategies({"id": "m2"}, need, reuse)] == ["software-use-shelf"]
    ext = {"existing_capabilities": [{"id": "c1", "slug": "shelf", "title": "Shelf", "relation": "extend",
                                      "gaps": ["sharing"]}]}
    assert {r.key for r in strategies({"id": "m3"}, need, ext)} == {"software-extend-shelf", "software-separate-shelf"}
