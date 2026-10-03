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
    reuse = {"existing_capabilities": [{"id": "c1", "slug": "shelf", "title": "Shelf", "relation": "can_do",
                                        "implementation": "application"}]}
    assert [r.key for r in strategies({"id": "m2"}, need, reuse)] == ["software-use-shelf"]
    ext = {"existing_capabilities": [{"id": "c1", "slug": "shelf", "title": "Shelf", "relation": "extend",
                                      "gaps": ["sharing"], "implementation": "application"}]}
    assert {r.key for r in strategies({"id": "m3"}, need, ext)} == {"software-extend-shelf", "software-separate-shelf"}


class _Judge:
    """Stands in for the reasoning worker that adjudicates disputes."""

    def __init__(self, corrected):
        self.corrected = corrected
        self.asked = []

    def ask(self, task, instructions, payload, schema, **kw):
        from types import SimpleNamespace

        self.asked.append(payload)
        return SimpleNamespace(output={"verdict": "scenario_wrong", "why": "the scenario uses a literal placeholder",
                                       "corrected_scenario": self.corrected})

    def available(self):
        return True


def _disputed_design():
    d = json.loads(json.dumps(DESIGN))
    d["scenarios"].append({"id": "login_api", "requirement": "private", "kind": "api", "steps": [
        {"do": "call", "api": "login", "auth": False, "body": {"passphrase": "MY_SECRET"}, "expect_status": 200}]})
    d["api"].append({"id": "login", "method": "POST", "path": "/api/login", "purpose": "login", "public": True})
    return d


def _dispute(ws: Path) -> None:
    write_app(ws, persist=True)
    (ws / "DISPUTES.json").write_text(json.dumps([{"scenario": "login_api", "reason": "sends a literal value, not "
                                                   "the passphrase; accepting it would be a backdoor"}]))


@pytest.mark.parametrize("weaken", [False, True])
def test_a_worker_dispute_is_judged_and_never_weakens_acceptance(apps, weaken):
    from regent.software.reasoner import set_reasoner

    good = {"id": "login_api", "requirement": "private", "kind": "api", "steps": [
        {"do": "call", "api": "login", "auth": False, "body": {"passphrase": "{passphrase}"}, "expect_status": 200}]}
    weaker = {"id": "login_api", "requirement": "private", "kind": "api", "steps": [
        {"do": "call", "api": "health", "auth": False}]}
    judge = _Judge(weaker if weaken else good)
    set_reasoner(judge)
    try:
        design = _disputed_design()
        worker = ScriptedWorker([lambda ws: write_app(ws, persist=True), _dispute, lambda ws: None])
        b = B.AppBuild(slug="shelf", need=NEED, design=design, sources=[], agent=worker, version=1, max_rounds=2)
        res = b.run()
    finally:
        set_reasoner(None)
    assert not res["rounds"][0]["passed"]                       # the literal value fails against the real app
    assert "DISPUTES.json" in worker.briefs[1]                  # the worker was told how to contest a check
    [d] = res["disputes"]
    if weaken:
        assert d["verdict"] == "correction_rejected" and "fewer checks" in d["why"]
        assert not res["accepted"]
        assert design["scenarios"][-1]["steps"][0]["body"]["passphrase"] == "MY_SECRET"
    else:
        assert d.get("corrected") and res["accepted"] and len(res["rounds"]) == 2
        assert design["revisions"][0]["scenario"] == "login_api"
        assert "corrected its scenario login_api" in worker.briefs[-1] or len(worker.briefs) == 2


def test_a_running_application_outlives_regent_and_is_adopted_not_duplicated(apps):
    worker = ScriptedWorker([lambda ws: write_app(ws, persist=True)])
    b = B.AppBuild(slug="shelf", need=NEED, design=DESIGN, sources=[], agent=worker, version=1, max_rounds=0)
    res = b.run()
    promo = b.promote()
    live = S.get("shelf", "live")
    pid = live.proc.pid
    S._RUNNING.clear()                       # Regent's process restarts; the application keeps running
    data = Path(apps) / "apps" / "shelf" / "data" / "live"
    again = S.adopt("shelf", "live", Path(res["workspace"]), data, credential=b.passphrase)
    assert again is not None and again.pid == pid and again.port == promo["port"] and again.healthy()
    # a stale instance of another version is stopped, not adopted
    S._RUNNING.clear()
    assert S.adopt("shelf", "live", Path(res["workspace"]).parent / "v9", data) is None
    assert not again._alive()


def test_an_application_judged_the_same_need_is_used_not_read_like_a_dashboard():
    from regent.software.routes import strategies

    inv = {"existing_capabilities": [{"id": "c1", "slug": "shelf", "title": "Shelf", "relation": "same_need",
                                      "implementation": "application"}]}
    [r] = strategies({"id": "m2"}, {"requirements": NEED["requirements"]}, inv)
    assert r.key == "software-use-shelf" and r.operations[0].action == "use_app"


def test_the_next_version_is_held_to_everything_the_current_one_was_accepted_for():
    from regent.software.tool import _scoped_need, with_regression

    v2 = {**DESIGN, "api": [{**a, "id": a["id"] + "_v2"} for a in DESIGN["api"]] +
          [{"id": "share", "method": "POST", "path": "/api/books/{id}/share", "purpose": "share"}],
          "scenarios": [{"id": "private", "requirement": "v2-share", "kind": "negative", "steps": [
              {"do": "call", "api": "list_books_v2", "auth": False, "expect_status": 401}]}], "ui": []}
    merged = with_regression(v2, DESIGN)
    ids = [s["id"] for s in merged["scenarios"]]
    assert ids == ["private", "add_and_survive_restart", "prev-private"]
    carried = merged["scenarios"][1]
    assert {st.get("api") for st in carried["steps"]} == {"add_book_v2", None, "list_books_v2"}
    need = _scoped_need({"requirements": [{"id": "share", "capability": "x", "acceptance": "y"}]}, 2)
    assert need["requirements"][0]["id"] == "v2-share"
    assert D.check(merged, need, DESIGN) == []


def test_fields_that_change_between_two_reads_are_not_records():
    a = {"/api/export": {"exported_at": "t1", "books": [{"id": 1, "title": "The Odyssey"}]}}
    b = {"/api/export": {"exported_at": "t2", "books": [{"id": 1, "title": "The Odyssey"}]}}
    v = A.volatile(a, b)
    assert v == {"exported_at"}
    after = {"/api/export": {"exported_at": "t3", "books": [{"id": 1, "title": "The Odyssey", "shared": False}]}}
    assert A.preserved(a, after, ignore=v) == [] and A.preserved(a, after) != []
    assert A.preserved(a, {"/api/export": {"exported_at": "t3", "books": []}}, ignore=v)


def test_read_back_matches_structure_not_exact_lists():
    data = {"notes": [{"id": 1, "book_id": 1, "text": "The Butler translation reads well.", "position": None}]}
    assert A.contains(data, {"notes": [{"text": "The Butler translation reads well."}]}) == []
    assert A.contains(data, {"notes": [{"text": "Pope"}]}) != []
    assert A.contains(data, {"notes": []}) == []


def test_a_browser_step_fills_dropdowns_and_checkboxes_like_a_person(tmp_path):
    from playwright.sync_api import sync_playwright

    from regent.browser.driver import _chromium_executable

    page_html = ('<select data-testid="s"><option value="want">Want to read</option><option value="reading">Reading'
                 '</option></select><input type="checkbox" data-testid="c"><input data-testid="t">')
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception:
            b = pw.chromium.launch(executable_path=_chromium_executable())
        pg = b.new_page()
        pg.set_content(page_html)
        A._set_value(pg, '[data-testid="s"]', "reading")
        assert pg.eval_on_selector('[data-testid="s"]', "e => e.value") == "reading"
        A._set_value(pg, '[data-testid="s"]', "Want to read")          # by its visible label
        assert pg.eval_on_selector('[data-testid="s"]', "e => e.value") == "want"
        A._set_value(pg, '[data-testid="c"]', "true")
        assert pg.eval_on_selector('[data-testid="c"]', "e => e.checked")
        A._set_value(pg, '[data-testid="t"]', "Neuromancer")
        assert pg.eval_on_selector('[data-testid="t"]', "e => e.value") == "Neuromancer"
        b.close()


def test_a_dashboard_judged_extendable_is_reused_not_rebuilt_as_an_app():
    from regent.software.routes import strategies

    inv = {"existing_capabilities": [{"id": "c1", "slug": "lindy", "title": "LindyBooks readers", "relation": "extend",
                                      "implementation": "composed"}]}
    [r] = strategies({"id": "m2"}, {"questions": []}, inv)
    assert r.key == "software-reuse-lindy" and r.operations[0].action == "reuse"
