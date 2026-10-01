"""Application capabilities: registered, used through their own API, kept running, upgraded.

An accepted application becomes a ``SwCapability`` with ``implementation="application"``. Its
spec holds the design (the interface contract), the live version, workspace, data directory and
port; its verification is Regent's acceptance record. Its API becomes a Regent tool
(``app_<slug>.<endpoint>``), so later missions use it the way they use any tool. The maintenance
pass restarts the live instance if it is down.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from regent.software import appaccept as A
from regent.software import appservice as S
from regent.software import capability as K
from regent.software import secrets
from regent.software.tables import SwCapability


def register(s: Session, *, mission_id: str | None, need: dict[str, Any], signature: list[str], design: dict[str, Any],
             build: dict[str, Any], promote: dict[str, Any], capability_id: str | None = None,
             coverage: float = 0.0, provenance: dict[str, Any] | None = None) -> SwCapability:
    last = build["rounds"][-1]
    spec = {"title": (design.get("name") or "application").replace("-", " ").capitalize(),
            "summary": design.get("summary"), "slug": design.get("name"),
            "design": design, "requirements": need.get("requirements", []),
            "app": {"version": build["version"], "workspace": build["workspace"], "credential": build.get("credential"),
                    "data_dir": str(Path(build["workspace"]).parent / "data" / "live"), "port": promote.get("port"),
                    "url": promote.get("url"), "inspect": {k: last["inspect"].get(k) for k in
                                                          ("files", "source_files", "lines", "languages", "tests")},
                    "manifest": last["inspect"].get("manifest")},
            "sources": [], "metrics": [], "unanswered": []}
    cap = K.save_version(s, mission_id=mission_id, need=need, signature=signature, spec=spec,
                         implementation="application", provenance=provenance or {},
                         reason=f"application v{build['version']} accepted after {len(build['rounds'])} round(s)",
                         capability_id=capability_id)
    cap.verification = {"passed": True, "version": cap.version, "summary": _summary(build),
                        "rounds": [{k: r.get(k) for k in ("round", "passed", "failures")} for r in build["rounds"]],
                        "acceptance": last.get("acceptance"), "tests": last.get("tests"),
                        "migration": last.get("migration"), "isolation": last.get("isolation")}
    cap.coverage = coverage
    cap.status = "usable" if coverage >= 0.8 else "degraded"
    s.flush()
    return cap


def _summary(build: dict[str, Any]) -> str:
    last = build["rounds"][-1]
    acc = last.get("acceptance") or {}
    n = len(acc.get("scenarios", []))
    ok = sum(1 for x in acc.get("scenarios", []) if x["passed"])
    return (f"v{build['version']}: {ok}/{n} acceptance scenarios, worker tests "
            f"{'pass' if (last.get('tests') or {}).get('ok') else 'fail'}, {len(build['rounds'])} round(s)")


def live(cap: SwCapability) -> S.AppService:
    """The live instance, started from the current version if it is not running."""
    app = cap.spec["app"]
    cred = secrets.get(app["credential"]) if app.get("credential") else None
    svc = S.get(app_slug(cap), "live") or S.adopt(app_slug(cap), "live", Path(app["workspace"]),
                                                  Path(app["data_dir"]), credential=cred)
    if svc is not None and svc.healthy():
        return svc
    if svc is not None:
        svc.stop()
    svc = S.AppService(app_slug(cap), "live", Path(app["workspace"]), Path(app["data_dir"]),
                       credential=secrets.get(app["credential"]) if app.get("credential") else None,
                       port=app.get("port") or 0)
    st = svc.start()
    if not st["ok"]:
        raise RuntimeError(f"live instance of {cap.slug} does not start: {st.get('error')}")
    return svc


def app_slug(cap: SwCapability) -> str:
    return Path(cap.spec["app"]["workspace"]).parent.name


def runner(cap: SwCapability) -> A.Runner:
    app = cap.spec["app"]
    return A.Runner(live(cap), cap.spec["design"], secrets.get(app["credential"]) if app.get("credential") else None)


def read(cap: SwCapability) -> dict[str, Any]:
    app = cap.spec["app"]
    svc = S.get(app_slug(cap), "live")
    return {"capability": {"id": cap.id, "slug": cap.slug, "title": cap.title, "version": cap.version,
                           "status": cap.status, "implementation": cap.implementation, "tool": cap.tool_name},
            "app": {"url": svc.url if svc else app.get("url"), "running": bool(svc and svc.healthy()),
                    "version": app.get("version"), "files": (app.get("inspect") or {}).get("files"),
                    "lines": (app.get("inspect") or {}).get("lines"), "credential": app.get("credential")},
            "coverage": cap.coverage, "verification": {k: (cap.verification or {}).get(k) for k in ("passed", "summary")},
            "api": [{k: a.get(k) for k in ("id", "method", "path", "purpose")} for a in cap.spec["design"]["api"]],
            "metrics": [], "unanswered": [], "need": {"sentence": (cap.need or {}).get("sentence")}}


def register_tool(services: Any, cap: SwCapability) -> None:
    from regent import db as dbm
    from regent.schemas import ToolResult
    from regent.tools.base import ActionSpec, Tool

    cap_id = cap.id
    api = cap.spec["design"]["api"]

    def handler(action: str, inputs: dict[str, Any], ctx) -> ToolResult:
        s = dbm.session()
        try:
            c = s.get(SwCapability, cap_id)
            r = runner(c)
            resp = r.call(action, inputs.get("path_params"), inputs.get("body"))
            r.client.close()
            data = resp.json() if resp.content else None
            if resp.status_code >= 400:
                return ToolResult(status="failed", error=f"HTTP {resp.status_code}: {json.dumps(data)[:300]}")
            c.uses = (c.uses or 0) + 1
            s.commit()
            return ToolResult(status="ok", outputs={"status": resp.status_code, "data": data})
        finally:
            s.close()

    actions = {a["id"]: ActionSpec(a["id"], f"{a['method']} {a['path']}: {a['purpose']}",
                                   "AUTO" if a["method"] == "GET" else "COMMIT" if a["method"] == "DELETE" else "AUTO",
                                   capabilities=[f"app:{cap.slug}"]) for a in api}
    services.tools.register(Tool(name=cap.tool_name, executor="api", description=f"{cap.title} (application "
                                 f"v{cap.spec['app']['version']}, built and verified by Regent)", actions=actions,
                                 handler=handler, backend="live", built_by_regent=True))


def maintain(s: Session) -> list[str]:
    out = []
    for c in s.query(SwCapability).filter(SwCapability.implementation == "application",
                                          SwCapability.status.in_(("usable", "degraded"))):
        app = c.spec["app"]
        svc = S.get(app_slug(c), "live") or S.adopt(
            app_slug(c), "live", Path(app["workspace"]), Path(app["data_dir"]),
            credential=secrets.get(app["credential"]) if app.get("credential") else None)
        if svc is None or not svc.healthy():
            try:
                live(c)
                out.append(f"{c.slug}: restarted")
            except RuntimeError as e:
                out.append(f"{c.slug}: {e}")
    return out
