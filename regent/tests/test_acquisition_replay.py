"""World Acquisition on *recorded real pages* (tests/fixtures/web, captured by Regent from
the public web and replayed through the same Fetcher via httpx). No candidate is
seeded: Regent starts from portal entry pages, reads live market rents, picks areas,
navigates to listings, extracts claims, resolves identities and runs the funnel."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from regent.acquisition import service
from regent.acquisition.fetch import html_to_text
from regent.acquisition.housing.extractor import RENT_MAX, RENT_MIN, HousingExtractor
from regent.acquisition.replay import ReplayTransport
from regent.acquisition.tables import AcqClaim, AcqDocument, AcqEntity, AcqRequest, AcqSource
from regent.acquisition.types import FetchedDocument

FIX = Path(__file__).parent / "fixtures" / "web"


@pytest.fixture()
def web(monkeypatch):
    t = ReplayTransport(FIX)
    monkeypatch.setattr(service, "TRANSPORT", t)
    monkeypatch.setattr(service, "ENGINE_KW", {"max_pages": 60, "min_interval_s": 0, "deadline_s": 600})
    return t


def _doc(url: str) -> FetchedDocument:
    import gzip

    t = ReplayTransport(FIX)
    html = gzip.decompress((FIX / t.pages[url]["file"]).read_bytes()).decode()
    _, text = html_to_text(html)
    return FetchedDocument(id="d", url=url, final_url=url, host=url.split("/")[2], status=200, html=html, text=text,
                           fetched_at=datetime(2026, 9, 29, tzinfo=timezone.utc), render="static")


def test_extractor_reads_real_listing_and_market_pages():
    x = HousingExtractor()
    for url, min_units in (("https://suumo.jp/chintai/tokyo/sc_edogawa/", 30),
                           ("https://www.homes.co.jp/chintai/tokyo/edogawa-city/list/", 40),
                           ("https://www.chintai.net/tokyo/area/13123/list/", 20)):
        units = [m for m in x.extract(_doc(url), "listing") if m.entity_type == "unit"]
        assert len(units) >= min_units, url
        for u in units:
            c = {k.attribute: k.value for k in u.claims}
            assert RENT_MIN <= c["rent"] <= RENT_MAX and c.get("layout") and c.get("area_m2")
            assert all(k.evidence for k in u.claims)           # provenance text for every claim
            assert u.parent is not None and (u.parent.value("address") or u.parent.value("name"))
    market = [m for m in x.extract(_doc("https://suumo.jp/chintai/soba/tokyo/"), "market") if m.entity_type == "area"]
    assert len(market) >= 20
    assert all(any(c.attribute.startswith("market_rent_") for c in m.claims) for m in market)
    # listing pages carry neighbour-area widgets: those are not market data
    listing = x.extract(_doc("https://www.homes.co.jp/chintai/tokyo/edogawa-city/list/"), "listing")
    assert not [m for m in listing if m.entity_type == "area"]


def test_discovery_from_entry_pages_without_any_candidate_list(db, web):
    out = service.run_action("discover", mission_id=None,
                             params={"region": "東京都", "household": 1, "max_areas": 1, "assumptions": []})
    assert out["status"] == "done"
    db.expire_all()
    req = db.get(AcqRequest, out["request_id"])
    # areas were chosen from live market data, not given
    assert req.plan["areas"] == ["江戸川区"] and "market rents from live data" in req.plan["area_rationale"]
    f = out["funnel"]
    assert f["units"] >= 150 and f["units"] > f["passed_filters"] >= f["filtered"] >= f["shortlisted"] >= 1
    assert f["filtered"] <= 25 and f["shortlisted"] <= 8
    # several independent sources were navigated to from their entry pages
    hosts = {h for (hs,) in db.execute(select(AcqEntity.source_hosts).where(AcqEntity.entity_type == "unit"))
             for h in hs or []}
    assert {"suumo.jp", "www.homes.co.jp", "www.chintai.net", "www.monthly-mansion.com"} <= hosts
    assert any(u.startswith("https://suumo.jp/chintai/tokyo/sc_edogawa/") for u in web.requests)
    # the same room found on two portals became one unit with claims from both
    multi = [hs for (hs,) in db.execute(select(AcqEntity.source_hosts).where(AcqEntity.entity_type == "unit"))
             if len(hs or []) >= 2]
    assert multi
    # access control is respected and recorded, not bypassed
    athome = db.get(AcqSource, "www.athome.co.jp")
    assert athome.blocked >= 1 and athome.ok == 0
    assert any("blocked" in e["message"] for e in req.log if "athome" in e["message"])
    # every claim is traceable to the page that asserted it
    orphan = db.scalar(select(func.count()).select_from(AcqClaim).where(
        (AcqClaim.url == "") | AcqClaim.document_id.is_(None) | (AcqClaim.evidence == "")))
    assert orphan == 0
    docs = {d.id for d in db.scalars(select(AcqDocument))}
    assert all(c.document_id in docs for c in db.scalars(select(AcqClaim).limit(500)))
    # shortlisted candidates carry evidence-backed beliefs
    short = db.scalars(select(AcqEntity).where(AcqEntity.stage == "shortlisted")).all()
    assert short and all(e.beliefs["rent"]["hypotheses"][0]["sources"] for e in short)
    assert "## 1." in out["brief_markdown"]


def test_loop_acquires_the_world_from_a_mission_sentence(db, services, web):
    """Only the mission text. The loop's ACQUIRE phase notices it knows no housing options,
    runs discovery on the (recorded) web, projects candidates into the world and competes
    housing strategies -- including not signing anything yet."""
    from regent.core.goals.missions import MissionGraph
    from regent.core.loop import RegentLoop
    from regent.db import Operation, Route

    m = MissionGraph(db).create(title="住居を安定させたい", objective="住居を安定させたい")
    db.commit()
    reps = RegentLoop(db, services).run(m.id, max_ticks=2)
    first = reps[0]
    acq = next(p for p in first.phases if p["phase"] == "acquire")
    assert acq["operations"] and acq["needs"][0]["action"] == "discover"
    # strategies are not compared on priors: the blocking acquisition runs before planning,
    # so the very first generation already sees live candidates (the lease route exists)
    assert acq["executed_before_planning"]
    gen = next(p for p in first.phases if p["phase"] == "generate")
    assert "housing-lease" in gen["created"]
    op = db.scalar(select(Operation).where(Operation.mission_id == m.id, Operation.tool == "acquire",
                                           Operation.action == "discover"))
    assert op.status == "succeeded" and op.outputs["funnel"]["shortlisted"] >= 1
    from regent.core.world.state import WorldView

    world = WorldView.load(db)
    units = [e for e in world.entities.values() if e.kind == "unit"]
    assert any(e.attrs.get("stage") == "shortlisted" for e in units)
    keys = {r.key for r in db.scalars(select(Route).where(Route.mission_id == m.id))}
    assert {"housing-lease", "housing-monthly", "housing-share", "housing-defer"} <= keys
    lease = db.scalar(select(Route).where(Route.mission_id == m.id, Route.key == "housing-lease"))
    assert "Best evidenced candidate" in lease.thesis


def test_acquisition_api_exposes_hypotheses_with_provenance(db, web, live_server):
    import httpx

    service.run_action("discover", mission_id=None, params={"region": "東京都", "household": 1, "max_areas": 1})
    o = httpx.get(f"{live_server}/api/acquisition/overview").json()
    assert o["funnel"]["shortlisted"] >= 1 and o["candidates"][0]["stage"] == "shortlisted"
    src = {s["host"]: s for s in o["sources"]}
    assert src["www.athome.co.jp"]["blocked"] >= 1 and src["suumo.jp"]["records"] > 0
    cid = o["candidates"][0]["id"]
    d = httpx.get(f"{live_server}/api/acquisition/entities/{cid}").json()
    rent = d["attributes"]["rent"]
    assert rent["claims"] and all(c["url"].startswith("https://") and c["source_host"] for c in rent["claims"])
    assert rent["belief"]["ttl_s"] == 24 * 3600 and d["parent"] is not None
    assert d["resolution_links"], "identity decisions are exposed"
    assert httpx.get(f"{live_server}/api/acquisition/entities/nope").status_code == 404
