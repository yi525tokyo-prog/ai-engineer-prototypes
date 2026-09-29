"""HTTP API: cockpit read model, event ingestion, overrides, rebuild, privacy."""

import httpx
from sqlalchemy import select

from regent.core.loop import RegentLoop
from regent.db import ConstitutionItem, Mission
from regent.sim import scenario


def test_cockpit_and_writes(db, services, live_server):
    scenario.seed(db, live_server)
    RegentLoop(db, services).run_all()
    c = httpx.get(f"{live_server}/api/missions/{scenario.ROOT_MISSION}/cockpit").json()
    for section in ("mission", "now", "best_route", "why", "executing", "blocked_by_you", "alternatives",
                    "changes", "world", "treasury", "constitution"):
        assert section in c, section
    assert c["best_route"]["operations"]
    assert c["why"]["uncertainties"][0]["flips_selection"]
    assert len(c["alternatives"]) >= 3
    assert c["blocked_by_you"][0]["kind"] == "identity"

    sysinfo = httpx.get(f"{live_server}/api/system").json()
    assert "ANTHROPIC_API_KEY" in sysinfo["missing_credentials"]
    assert any(p["name"] == "local" and p["available"] for p in sysinfo["providers"])

    r = httpx.post(f"{live_server}/api/events", json={"type": "fact_observed",
                                                      "payload": {"key": "user.mood", "value": "focused"}})
    assert r.status_code == 200 and r.json()[0]["type"] == "fact_observed"
    w = httpx.get(f"{live_server}/api/world").json()
    assert w["facts"]["user.mood"] == "focused"

    # principal override: Regent complies and infers preferences from the choice
    alt = next(a for a in c["alternatives"] if a["key"] == "reduce-burn")
    r = httpx.post(f"{live_server}/api/missions/{scenario.ROOT_MISSION}/override", json={"route_id": alt["id"]})
    assert r.status_code == 200 and r.json()["constitution_updates"]
    db.expire_all()
    assert db.get(Mission, scenario.ROOT_MISSION).selected_route_id == alt["id"]
    assert db.scalar(select(ConstitutionItem).where(ConstitutionItem.id.like("inferred:%"))) is not None

    # state is reconstructable from the event log
    rb = httpx.post(f"{live_server}/api/world/rebuild").json()
    assert rb["replayed_events"] > 50
    assert not rb["diff_vs_before"].get("entities") and not rb["diff_vs_before"].get("facts")

    bad = httpx.post(f"{live_server}/api/global/facts", json={"subject": "x", "statement": "mail haruka@kinoshita.example"})
    assert bad.status_code == 422


def test_portal_requires_human(db, live_server):
    scenario.seed(db, live_server)
    html = httpx.get(f"{live_server}/sim/portal/kinoshita").text
    assert "captcha" in html.lower() and "budget-status" not in html
    httpx.post(f"{live_server}/sim/portal/kinoshita/verify", data={"code": "WRONG"})
    assert "captcha" in httpx.get(f"{live_server}/sim/portal/kinoshita").text.lower()
    httpx.post(f"{live_server}/sim/portal/kinoshita/verify", data={"code": scenario.PORTAL_CODE})
    assert 'id="budget-status">frozen' in httpx.get(f"{live_server}/sim/portal/kinoshita").text
