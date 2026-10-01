"""Obtaining an application: delegate, inspect, build, test, run, accept, repair, register, upgrade.

The coding worker (Claude Code, file tools only, its own workspace) writes the application from
Regent's brief. It cannot run anything; Regent runs everything and believes only what it observes:

  inspect    the files exist, the manifest is valid, tests exist, no tracking code, no calls to hosts
             the design did not allow
  build      the manifest's build command (dependency install), with a timeout
  test       the worker's own test suite
  stage      start a staging instance on a scratch data directory (or on a *copy* of the live data
             for an upgrade) with a credential Regent generated
  accept     Regent's acceptance scenarios: API, real browser, restart persistence, negative
             checks; for an upgrade also: every record the live version returned is still there
  isolate    the running application wrote nothing outside its data directory

Every failure goes back to the worker as a repair brief, bounded rounds. A version is promoted to
live only when all of the above pass; an upgrade backs up the live data, starts the new version
on it, re-checks every record, and rolls back to the previous version if anything is missing.
"""

from __future__ import annotations

import json
import re
import secrets as pysecrets
import shutil
import time
from pathlib import Path
from typing import Any

from regent.software import appaccept as A
from regent.software import appservice as S
from regent.software import secrets
from regent.software.probe import ANALYTICS_SIGNATURES

SKIP = {".home", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".git"}
CODE_EXT = {".py", ".js", ".mjs", ".ts", ".html", ".css", ".json", ".toml", ".txt", ".md", ".sh", ".cfg", ".ini",
            ".jsx", ".tsx", ".yaml", ".yml", ".sql"}

CONTRACT = """## Runtime contract (Regent runs, tests and operates the application; it must follow this exactly)

- Write `regent.json` at the root: {"name": ..., "version": ..., "build": <shell command or null>,
  "test": <shell command>, "start": <shell command>, "health": "/api/health"}.
  `start` must listen on 127.0.0.1 at the port in the PORT environment variable (or `{port}` in the command)
  and keep ALL state under the directory in the DATA_DIR environment variable (or `{data_dir}`). Nothing else on
  disk may be written at run time.
- Dependencies: declare them (e.g. requirements.txt, package.json) and install them in `build`
  into the project directory (a virtualenv or node_modules inside it). Python 3.11 and Node 22 are available.
- `test` runs your own automated tests and exits non-zero on failure. They must not need network access.
- GET /api/health returns 200 without credentials.
- Credential: if the design requires one, the passphrase is in the APP_PASSPHRASE environment variable.
  POST /api/login with {"passphrase": "..."} returns {"token": "..."} (401 on a wrong passphrase); every
  non-public endpoint requires the header `Authorization: Bearer <token>` and returns 401 without it. Tokens
  must survive an application restart or be re-obtainable by logging in again. The UI login form uses
  data-testid="login-passphrase" for the input and data-testid="login-submit" for the button.
- The UI is served at / and works in a phone-sized browser. Put the listed data-testid attributes on the elements.
- Network: only the hosts listed under external_hosts may be called, from the server side. No analytics,
  tracking, ads, CDNs or third-party scripts; serve all assets yourself.
- You cannot run commands. Regent will build, test, start and exercise the application and send you the
  failures to fix. Write code that you are confident runs as written.
"""


def _files(ws: Path) -> list[Path]:
    out = []
    for p in ws.rglob("*"):
        if p.is_file() and not any(part in SKIP for part in p.relative_to(ws).parts) \
                and not p.name.startswith(".run-"):
            out.append(p)
    return sorted(out)


def inspect(ws: Path, design: dict[str, Any]) -> dict[str, Any]:
    files = _files(ws)
    src = [f for f in files if f.suffix in CODE_EXT]
    problems, notes = [], []
    try:
        man = S.manifest(ws)
    except Exception as e:
        man = None
        problems.append(f"manifest: {e}")
    tests = [f for f in src if re.search(r"(^|/)(tests?|spec)(/|_|\.)|_test\.|\.test\.|test_", str(f.relative_to(ws)))]
    if not tests:
        problems.append("no automated tests")
    allowed = {h.lower() for h in design.get("external_hosts", [])} | {"127.0.0.1", "localhost", "0.0.0.0"}
    # what loads or calls something; a plain <a href> is a link the reader may follow, not a call
    calls = re.compile(r"(fetch\(|requests\.|httpx\.|urlopen|urllib|https?\.get\(|axios|src=|<link[^>]+href=|@import|"
                       r"XMLHttpRequest|EventSource|WebSocket\()", re.I)
    undeclared = set()
    tracking = []
    lines = 0
    for f in src:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        lines += text.count("\n")
        for rx, name in ANALYTICS_SIGNATURES:
            if re.search(rx, text, re.I) and f.suffix in (".html", ".js", ".mjs", ".ts", ".jsx", ".tsx"):
                tracking.append(f"{name} in {f.relative_to(ws)}")
        for line in text.splitlines():
            if not calls.search(line):
                continue
            for host in re.findall(r"https?://([A-Za-z0-9.-]+)", line):
                h = host.lower()
                if h not in allowed and not any(h.endswith("." + a) for a in allowed):
                    undeclared.add(h)
    if tracking:
        problems.append(f"tracking code: {tracking}")
    if undeclared:
        problems.append(f"calls hosts the design did not allow: {sorted(undeclared)}")
    langs: dict[str, int] = {}
    for f in src:
        langs[f.suffix] = langs.get(f.suffix, 0) + 1
    return {"ok": not problems, "problems": problems, "notes": notes, "files": len(files), "source_files": len(src),
            "lines": lines, "languages": langs, "tests": [str(t.relative_to(ws)) for t in tests][:20],
            "manifest": man}


def _snapshot_tree(ws: Path) -> dict[str, float]:
    return {str(p.relative_to(ws)): p.stat().st_mtime for p in _files(ws)}


def brief(need: dict[str, Any], design: dict[str, Any], *, sources: list[dict[str, Any]],
          upgrade_of: dict[str, Any] | None = None) -> str:
    parts = [
        "# Build this application\n",
        "## What the person needs\n", need.get("sentence", ""), "\n\nRequirements:\n",
        "\n".join(f"- [{r['id']}] ({r.get('priority')}) {r['capability']} — accepted when: {r['acceptance']}"
                  for r in need.get("requirements", [])),
        "\n\n## Design (Regent's interface contract; keep paths, fields and data-testids exactly)\n```json\n",
        json.dumps({k: design.get(k) for k in ("name", "summary", "entities", "auth", "api", "ui", "external_hosts",
                                                "migration")}, ensure_ascii=False, indent=1),
        "\n```\n\n## How Regent will test it (it runs these itself against your running application)\n```json\n",
        json.dumps(design.get("scenarios", []), ensure_ascii=False, indent=1),
        "\n```\n\n## Data sources you may use (observed live by Regent)\n```json\n",
        json.dumps(sources, ensure_ascii=False, indent=1)[:6000], "\n```\n\n", CONTRACT]
    if upgrade_of:
        parts += ["\n## This is an upgrade of version ", str(upgrade_of["version"]), " which is in use\n",
                  "The current code is in this directory. Real data written by that version will be in DATA_DIR "
                  "when this version starts: migrate it on start, losing nothing, and keep every existing endpoint "
                  "working with the same paths and fields. Regent will start this version on a copy of the real data "
                  "and compare every record before and after.\n"]
    return "".join(parts)


def _repair_brief(round_: int, failures: list[dict[str, Any]], revised: list[dict[str, Any]] | None = None) -> str:
    out = [f"# Repair round {round_}\n\nRegent built, tested and ran your application. These checks failed. Fix the "
           "code so they pass, without breaking the contract in BRIEF.md.\n\n"
           "If a failing check itself contradicts BRIEF.md or cannot be passed by a correct application, do not work "
           "around it (never weaken security or the contract to satisfy a check). Instead write `DISPUTES.json`: "
           '`[{"scenario": "<id>", "reason": "...", "evidence": "<quote from BRIEF.md or the failure>"}]`. Regent '
           "judges each dispute itself; a dispute it does not accept stays a failure.\n"]
    for d in revised or []:
        out.append(f"\n## Regent corrected its scenario {d['scenario']} (your dispute was upheld)\n{d['why']}\n```json\n"
                   f"{json.dumps(d['new'], ensure_ascii=False, indent=1)[:2500]}\n```\n")
    for f in failures:
        out.append(f"\n## {f['stage']}: {f['summary']}\n```\n{f.get('detail', '')[:3500]}\n```\n")
    return "".join(out)


DISPUTE_SCHEMA: dict[str, Any] = {"type": "object", "required": ["verdict", "why"], "properties": {
    "verdict": {"type": "string", "enum": ["scenario_wrong", "app_wrong", "unclear"]},
    "why": {"type": "string"},
    "corrected_scenario": {"type": ["object", "null"], "description": "only for scenario_wrong: the same scenario "
                           "(same id, requirement and kind) with the defect fixed and every check kept"}}}

DISPUTE_INSTRUCTIONS = """An operational agent wrote an acceptance scenario for an application a coding agent built.
The scenario failed, and the coding agent disputes it. Judge from the design (the contract both sides were given),
the scenario, the observed failure and the dispute -- not from who is asking. scenario_wrong only if a correct
application honouring the design could not pass it (e.g. a literal placeholder, a status code the design itself
specifies differently, text the design never defines). Then give the corrected scenario: fix only the defect, keep
every assertion's intent, use {passphrase} for the credential, data-testid hooks and expect_visible for messages whose
wording the design does not fix. Never drop a check, never turn a negative scenario into a positive one. If the app
could reasonably be fixed instead, say app_wrong."""

ASSERTS = ("expect_text", "expect_no_text", "expect_visible", "expect_hidden", "restart_app")


def _assertions(sc: dict[str, Any]) -> int:
    n = 0
    for st in sc.get("steps", []):
        if st.get("do") in ASSERTS:
            n += 1
        elif st.get("do") == "call":
            n += 1 + bool(st.get("expect_json_contains")) + bool(st.get("expect_status"))
    return n


def not_weaker(old: dict[str, Any], new: dict[str, Any], design: dict[str, Any], need: dict[str, Any]) -> list[str]:
    """Why a corrected scenario would weaken acceptance (empty: it does not)."""
    from regent.software import appdesign as D

    ids = {r["id"] for r in need.get("requirements", [])}
    why = []
    if new.get("id") != old.get("id") or new.get("kind") != old.get("kind"):
        why.append("changes the scenario's id or kind")
    if not D.scenario_requirements(old, ids) <= D.scenario_requirements(new, ids):
        why.append("covers fewer requirements")
    if _assertions(new) < _assertions(old):
        why.append(f"has fewer checks ({_assertions(new)} < {_assertions(old)})")
    trial = {**design, "scenarios": [new if s["id"] == old["id"] else s for s in design.get("scenarios", [])]}
    before = set(D.check(design, need))
    added = [p for p in D.check(trial, need) if p not in before]
    why += added
    return why


class AppBuild:
    def __init__(self, *, slug: str, need: dict[str, Any], design: dict[str, Any], sources: list[dict[str, Any]],
                 agent, version: int, previous: dict[str, Any] | None = None, max_rounds: int = 4, log=None):
        self.slug, self.need, self.design, self.sources = slug, need, design, sources
        self.agent, self.version, self.previous = agent, version, previous
        self.max_rounds = max_rounds
        self.log = log or (lambda *a, **k: None)
        self.root = S.apps_root() / slug
        self.ws = self.root / f"v{version}"
        self.cred_name = re.sub(r"[^A-Z0-9]+", "_", f"APP_{slug}_PASSPHRASE".upper())
        self.rounds: list[dict[str, Any]] = []
        self.disputes: list[dict[str, Any]] = []

    # -------------------------------------------------------------- pipeline

    def run(self) -> dict[str, Any]:
        self.ws.mkdir(parents=True, exist_ok=True)
        if self.previous and not any(self.ws.iterdir()):
            prev_ws = Path(self.previous["workspace"])
            shutil.copytree(prev_ws, self.ws, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(*SKIP, ".run-*", "BRIEF.md", "REPAIR-*.md",
                                                          "agent_result*.json"))
        passphrase = secrets.get(self.cred_name)
        if self.design.get("auth", {}).get("scheme") == "passphrase" and not passphrase:
            passphrase = pysecrets.token_urlsafe(18)
            secrets.put(self.cred_name, passphrase)
        self.passphrase = passphrase if self.design.get("auth", {}).get("scheme") == "passphrase" else None
        work = self.agent.run(self.ws, brief(self.need, self.design, sources=self.sources, upgrade_of=self.previous),
                              prompt="Read BRIEF.md and build exactly what it asks. Write all files in this directory.")
        self.log("delegate", f"worker v{self.version} round 0: claimed_done={work['claimed_done']} "
                             f"${work.get('cost_usd')} {work.get('seconds')}s files={len(work.get('files', []))}")
        worker_runs = [work]
        revised: list[dict[str, Any]] = []
        for round_ in range(self.max_rounds + 1):
            r = self._verify_round(round_)
            r["worker"] = worker_runs[-1]
            self.rounds.append(r)
            if r["passed"]:
                return {"accepted": True, "version": self.version, "workspace": str(self.ws), "rounds": self.rounds,
                        "credential": self.cred_name if self.passphrase else None, "disputes": self.disputes}
            if round_ == self.max_rounds:
                break
            (self.ws / f"REPAIR-{round_ + 1}.md").write_text(_repair_brief(round_ + 1, r["failures"], revised))
            work = self.agent.run(self.ws, None, prompt=f"Read REPAIR-{round_ + 1}.md (and BRIEF.md for the contract) "
                                                         "and fix the application accordingly.")
            worker_runs.append(work)
            self.log("repair", f"worker v{self.version} round {round_ + 1}: {len(r['failures'])} failures sent; "
                               f"claimed_done={work['claimed_done']} ${work.get('cost_usd')}")
            revised = self._judge_disputes(round_ + 1, r)
        return {"accepted": False, "version": self.version, "workspace": str(self.ws), "rounds": self.rounds,
                "disputes": self.disputes}

    # -------------------------------------------------------------- disputes

    def _judge_disputes(self, round_: int, last: dict[str, Any]) -> list[dict[str, Any]]:
        """The worker may contest a check. Regent judges each dispute against the design and the
        observed failure, and corrects its own scenario only when the correction keeps every check
        (``not_weaker``). Upheld or not, every dispute is recorded."""
        from regent.software.reasoner import ReasonerUnavailable, get_reasoner

        path = self.ws / "DISPUTES.json"
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text())
        except ValueError:
            raw = []
        path.rename(self.ws / f"DISPUTES-{round_}.json")
        failed = {f["scenario"]: f for f in last["failures"] if f.get("scenario")}
        by_id = {s["id"]: s for s in self.design.get("scenarios", [])}
        upheld = []
        for d in raw if isinstance(raw, list) else []:
            sid = str(d.get("scenario") or "")
            rec = {"round": round_, "scenario": sid, "reason": str(d.get("reason") or "")[:1000], "verdict": None}
            self.disputes = self.disputes + [rec]
            if sid not in by_id or sid not in failed:
                rec.update(verdict="ignored", why="not a scenario that failed in the last round")
                continue
            payload = {"design": {k: self.design.get(k) for k in ("api", "ui", "auth", "entities")},
                       "scenario": by_id[sid], "failure": failed[sid]["summary"],
                       "dispute": {"reason": rec["reason"], "evidence": str(d.get("evidence") or "")[:1500]}}
            try:
                ans = get_reasoner().ask("app_dispute", DISPUTE_INSTRUCTIONS, payload, DISPUTE_SCHEMA, budget_usd=0.5)
            except ReasonerUnavailable as e:
                rec.update(verdict="unjudged", why=str(e)[:200])
                continue
            v = ans.output
            rec.update(verdict=v["verdict"], why=v["why"][:800])
            if v["verdict"] != "scenario_wrong" or not v.get("corrected_scenario"):
                continue
            new = {**v["corrected_scenario"], "id": sid}
            weaker = not_weaker(by_id[sid], new, self.design, self.need)
            if weaker:
                rec.update(verdict="correction_rejected", why=f"{v['why'][:400]} -- but the correction {weaker}")
                continue
            self.design["scenarios"] = [new if s["id"] == sid else s for s in self.design["scenarios"]]
            self.design.setdefault("revisions", []).append({"scenario": sid, "round": round_, "why": v["why"],
                                                            "old": by_id[sid], "new": new, "raised_by": "worker"})
            by_id[sid] = new
            rec["corrected"] = True
            upheld.append({"scenario": sid, "why": v["why"], "new": new})
            self.log("dispute", f"scenario {sid} corrected (dispute upheld): {v['why'][:160]}")
        return upheld

    def _verify_round(self, round_: int) -> dict[str, Any]:
        failures: list[dict[str, Any]] = []
        out: dict[str, Any] = {"round": round_}
        insp = inspect(self.ws, self.design)
        out["inspect"] = insp
        if not insp["ok"]:
            failures.append({"stage": "inspect", "summary": "; ".join(insp["problems"])[:300],
                             "detail": "\n".join(insp["problems"])})
            if insp.get("manifest") is None:          # nothing Regent could run
                return {**out, "passed": False, "failures": failures}
        man = insp["manifest"]
        env = S.runtime_env(self.ws)
        if man.get("build"):
            b = S.run(man["build"], self.ws, timeout=900, env=env)
            out["build"] = b
            if not b["ok"]:
                failures.append({"stage": "build", "summary": f"`{man['build']}` failed (exit {b['code']})",
                                 "detail": b["tail"]})
                return {**out, "passed": False, "failures": failures}
        t = S.run(man["test"], self.ws, timeout=600, env={**env, "DATA_DIR": str(self.root / "data" / "test-tmp")})
        out["tests"] = t
        if not t["ok"]:
            failures.append({"stage": "tests", "summary": f"your tests failed (`{man['test']}`, exit {t['code']})",
                             "detail": t["tail"]})
        scratch = self.root / "data" / f"staging-v{self.version}-r{round_}"
        if scratch.exists():
            shutil.rmtree(scratch)
        tree0 = _snapshot_tree(self.ws)
        # every scenario starts from empty state of its own: scenarios cannot depend on, or trip over,
        # each other's records (or the principal's)
        svc = S.AppService(self.slug, "staging", self.ws, scratch / "s0", credential=self.passphrase)
        st = svc.start()
        out["start"] = st
        if not st["ok"]:
            failures.append({"stage": "start", "summary": st.get("error", "did not start"),
                             "detail": st.get("log_tail", "")})
            return {**out, "passed": False, "failures": failures}
        n = iter(range(1, 10_000))

        def fresh(sc: dict[str, Any]) -> None:
            svc.stop()
            svc.data_dir = scratch / f"s{next(n)}-{re.sub(r'[^A-Za-z0-9_-]+', '_', sc['id'])[:40]}"
            res = svc.start()
            if not res["ok"]:
                raise AssertionError(f"did not start for this scenario: {res.get('error')}")

        try:
            acc = A.Runner(svc, self.design, self.passphrase).run(before_each=fresh)
            out["acceptance"] = acc
            for sc in acc["scenarios"]:
                if not sc["passed"]:
                    failures.append({"stage": "acceptance", "scenario": sc["id"],
                                     "summary": f"scenario {sc['id']} ({sc.get('requirement')}, "
                                     f"{sc.get('kind')}) failed at step {sc.get('failed_step')}: {sc.get('error')}",
                                     "detail": "\n".join(sc.get("log", [])) + "\n" + (sc.get("page") or "")
                                     + "\n--- server log ---\n" + svc.logs(1500)})
        finally:
            svc.stop()
        if self.previous:
            mig = self._migration_check(scratch / "migrate")
            out["migration"] = mig
            if mig.get("lost") or mig.get("error"):
                failures.append({"stage": "migration", "summary": mig.get("error") or (
                                 f"{len(mig['lost'])} records of the version in use are missing after starting this "
                                 "version on a copy of the real data"),
                                 "detail": "\n".join(mig.get("lost") or [])[:3000] + "\n" + mig.get("log_tail", "")})
        changed = [k for k, v in _snapshot_tree(self.ws).items() if tree0.get(k) != v]
        out["isolation"] = {"written_outside_data_dir": changed[:20]}
        if changed:
            failures.append({"stage": "isolation", "summary": "the running application wrote outside DATA_DIR",
                             "detail": "\n".join(changed[:30])})
        return {**out, "passed": not failures, "failures": failures}

    def _migration_check(self, data_dir: Path) -> dict[str, Any]:
        """This version, started on a copy of the live data, must still return every record the
        version in use returns (fields that change between two reads of unchanged data -- the time
        of the request, say -- are not records)."""
        live = self._previous_live()
        if live is None:
            return {"error": "the version in use does not run, so its data cannot be compared"}
        before = A.snapshot(live, self.previous["design"], self.passphrase)
        volatile = A.volatile(before, A.snapshot(live, self.previous["design"], self.passphrase))
        shutil.copytree(self.root / "data" / "live", data_dir)
        svc = S.AppService(self.slug, "staging", self.ws, data_dir, credential=self.passphrase)
        st = svc.start()
        if not st["ok"]:
            return {"error": f"does not start on the real data: {st.get('error')}", "log_tail": st.get("log_tail", "")}
        try:
            lost = A.preserved(before, A.snapshot(svc, self.design, self.passphrase), ignore=volatile)
        finally:
            svc.stop()
        return {"records_before": sum(len(A.records(v)) for v in before.values()), "lost": lost[:20],
                "ignored_volatile": sorted(volatile)}

    def _previous_live(self) -> S.AppService | None:
        """The version in use, running (started from its own workspace on the live data if this
        process has not started it yet)."""
        live = S.get(self.slug, "live") or S.adopt(self.slug, "live", Path(self.previous["workspace"]),
                                                   self.root / "data" / "live", credential=self.passphrase)
        if live is not None and live.healthy():
            return live
        if live is not None:
            live.stop()
        live = S.AppService(self.slug, "live", Path(self.previous["workspace"]), self.root / "data" / "live",
                            credential=self.passphrase, port=self.previous.get("port") or 0)
        return live if live.start()["ok"] else None

    # ------------------------------------------------------------ promotion

    def promote(self) -> dict[str, Any]:
        """Make this version the live one. An upgrade keeps the previous version runnable and its
        data backed up, and rolls back if any record of the live data does not survive."""
        live_dir = self.root / "data" / "live"
        prev = self._previous_live() if self.previous else S.get(self.slug, "live")
        before = None
        backup = None
        volatile: set[str] = set()
        if prev is not None:
            before = A.snapshot(prev, self.previous["design"], self.passphrase) if self.previous else None
            volatile = A.volatile(before, A.snapshot(prev, self.previous["design"], self.passphrase)) \
                if before is not None else set()
            prev.stop()
            backup = S.backup(live_dir, f"before-v{self.version}")
        svc = S.AppService(self.slug, "live", self.ws, live_dir, credential=self.passphrase,
                           port=prev.port if prev is not None else 0)
        st = svc.start()
        if st["ok"] and before is not None:
            lost = A.preserved(before, A.snapshot(svc, self.design, self.passphrase), ignore=volatile)
            if lost:
                st = {"ok": False, "error": f"{len(lost)} live records missing after upgrade", "lost": lost[:10]}
        if not st["ok"]:
            svc.stop()
            if prev is not None and backup is not None:
                S.restore(backup, live_dir)
                prev.start()
            return {"live": False, "rolled_back": prev is not None, **st}
        return {"live": True, "url": svc.url, "port": svc.port, "backup": str(backup) if backup else None,
                "records_before": sum(len(A.records(v)) for v in (before or {}).values()) if before else None}
