"""World Acquisition core: claims never overwrite each other, beliefs expire, identity
decisions are conservative and audited, and access policy (robots, blocks) is honoured."""

from datetime import timedelta

import httpx
from sqlalchemy import select

from regent.acquisition.claims import ClaimStore
from regent.acquisition.fetch import Fetcher
from regent.acquisition.housing.adapter import HousingAdapter
from regent.acquisition.housing.resolver import HousingEntityResolver
from regent.acquisition.resolution import EntityResolution
from regent.acquisition.tables import AcqClaim, AcqConflict, AcqDocument, AcqEntity, AcqLink, AcqMention, AcqSource
from regent.acquisition.types import ClaimIn
from regent.ids import new_id, utcnow


def _entity(db, etype="unit"):
    e = AcqEntity(id=new_id("un"), domain="housing", entity_type=etype, label="1K", beliefs={}, source_hosts=[],
                  features={})
    db.add(e)
    db.flush()
    return e


def _claim(store, e, attr, value, host, at, kind="portal", conf=0.9):
    return store.add(e.id, ClaimIn(attribute=attr, value=value, confidence=conf, evidence=f"{host}: {value}",
                                   observed_at=at, extractor="test"),
                     source_host=host, source_kind=kind, url=f"https://{host}/x")


def test_conflicting_claims_are_kept_with_sources_and_resolve_by_new_evidence(db):
    t0 = utcnow()
    store = ClaimStore(db, HousingAdapter().policy(), now=t0)
    e = _entity(db)
    _claim(store, e, "rent", 82000, "a.example", t0)
    _claim(store, e, "rent", 84000, "b.example", t0)
    _claim(store, e, "rent", 82000, "c.example", t0)
    db.flush()
    b = store.refresh(e)["rent"]
    assert b["value"] == 82000 and b["conflict"] and b["n_sources"] == 3
    hyps = {h["value"]: h for h in b["hypotheses"]}
    assert set(hyps) == {82000, 84000}
    assert hyps[82000]["hosts"] == ["a.example", "c.example"] and hyps[84000]["hosts"] == ["b.example"]
    assert hyps[82000]["share"] > hyps[84000]["share"] >= 0.2
    assert 0 < b["confidence"] < 1
    conflict = db.get(AcqConflict, f"{e.id}:rent")
    assert conflict.status == "open" and len(conflict.hypotheses) == 2

    # B re-observes 2h later and now agrees: its latest window supersedes its own earlier claim,
    # the conflict resolves -- but nothing is deleted.
    later = t0 + timedelta(hours=2)
    store = ClaimStore(db, HousingAdapter().policy(), now=later)
    _claim(store, e, "rent", 82000, "b.example", later)
    db.flush()
    b2 = store.refresh(e)["rent"]
    assert b2["value"] == 82000 and not b2["conflict"] and b2["superseded"] == 1
    assert db.get(AcqConflict, f"{e.id}:rent").status == "resolved"
    assert len(db.scalars(select(AcqClaim).where(AcqClaim.entity_id == e.id)).all()) == 4


def test_time_sensitive_claims_expire_on_their_own_ttl(db):
    t0 = utcnow()
    policy = HousingAdapter().policy()
    store = ClaimStore(db, policy, now=t0)
    e = _entity(db)
    _claim(store, e, "rent", 82000, "a.example", t0)
    _claim(store, e, "availability", True, "a.example", t0)
    _claim(store, e, "layout", "1K", "a.example", t0)
    db.flush()
    store.refresh(e)
    assert store.stale_attributes(e, ["rent", "availability", "layout"]) == []
    assert policy.ttl("availability") == 6 * 3600 and policy.ttl("rent") == 24 * 3600
    assert policy.ttl("structure") == 365 * 86400 and policy.ttl("built_year") == 365 * 86400

    store = ClaimStore(db, policy, now=t0 + timedelta(hours=7))
    store.refresh(e)
    assert store.stale_attributes(e, ["rent", "availability", "layout"]) == ["availability"]
    store = ClaimStore(db, policy, now=t0 + timedelta(hours=25))
    store.refresh(e)
    assert store.stale_attributes(e, ["rent", "availability", "layout"]) == ["rent", "availability"]
    # an expired belief still has its value -- it is flagged, not dropped -- with decayed confidence
    assert e.beliefs["rent"]["value"] == 82000 and e.beliefs["rent"]["confidence"] < 0.8


def _mention(db, etype, host="a.example"):
    doc = AcqDocument(id=new_id("doc"), url=f"https://{host}/p", host=host, status=200)
    m = AcqMention(id=new_id("men"), document_id=doc.id, entity_type=etype, host=host, features={}, links={})
    db.add_all([doc, m])
    db.flush()
    return m


BLDG = {"name_key": "メゾン弥生", "city": "中野区", "town": "弥生町", "chome": 3, "address": "東京都中野区弥生町3",
        "built_year": 1998, "floors_total": 4, "stations": [{"station": "中野富士見町", "walk_min": 6}]}


def test_resolution_merges_same_building_across_sites_and_records_every_decision(db):
    er = EntityResolution(db, "housing", HousingEntityResolver())
    b1, d1, _ = er.resolve(_mention(db, "building", "a.example"), "building", dict(BLDG))
    b2, d2, p = er.resolve(_mention(db, "building", "b.example"), "building",
                           {**BLDG, "name_key": "メゾン弥生", "stations": [{"station": "中野富士見町", "walk_min": 7}]})
    assert (d1, d2) == ("new", "merged") and b1.id == b2.id and p >= 0.85
    # a building in the same block that disagrees on chome and year stays separate
    b3, d3, _ = er.resolve(_mention(db, "building"), "building",
                           {**BLDG, "name_key": "弥生ハイツ", "chome": 1, "built_year": 2015})
    assert d3 == "new" and b3.id != b1.id
    links = db.scalars(select(AcqLink)).all()
    assert {lk.decision for lk in links} >= {"merged", "rejected"}
    assert all(lk.features for lk in links)


def test_ambiguous_identity_is_not_merged(db):
    er = EntityResolution(db, "housing", HousingEntityResolver())
    b1, _, _ = er.resolve(_mention(db, "building"), "building", dict(BLDG))
    # similar name, same block, but no corroborating year/floors/stations: plausible, not proven
    thin = {"name_key": "メゾン弥生II", "city": "中野区", "town": "弥生町"}
    b2, decision, p = er.resolve(_mention(db, "building", "b.example"), "building", thin)
    assert decision == "ambiguous" and 0.5 <= p < 0.85 and b2.id != b1.id
    assert db.scalar(select(AcqLink).where(AcqLink.entity_id == b1.id, AcqLink.decision == "ambiguous"))


def test_same_site_price_difference_means_a_different_room_cross_site_means_a_conflict(db):
    er = EntityResolution(db, "housing", HousingEntityResolver())
    b, _, _ = er.resolve(_mention(db, "building"), "building", dict(BLDG))
    unit = {"layout": "1K", "area_m2": 20.5, "floor": 2, "rent": 82000}
    u1, _, _ = er.resolve(_mention(db, "unit"), "unit", {**unit, "_doc": "d1", "_host": "a.example"}, parent_id=b.id)
    # same site, another page, identical-looking 1K at a different price -> another room
    u2, d2, _ = er.resolve(_mention(db, "unit"), "unit", {**unit, "rent": 86000, "_doc": "d2", "_host": "a.example"},
                           parent_id=b.id)
    assert d2 != "merged" and u2.id != u1.id
    # another site, 2% different price -> the same room, and the price difference becomes a claim conflict
    u3, d3, _ = er.resolve(_mention(db, "unit"), "unit", {**unit, "rent": 83500, "_doc": "d3", "_host": "b.example"},
                           parent_id=b.id)
    assert d3 == "merged" and u3.id == u1.id
    # two rows of the same page are never one unit
    u4, d4, _ = er.resolve(_mention(db, "unit"), "unit", {**unit, "floor": 3, "_doc": "d1", "_host": "a.example"},
                           parent_id=b.id)
    assert d4 != "merged"


def _fetcher(db, handler):
    return Fetcher(db, transport=httpx.MockTransport(handler), min_interval_s=0)


def test_robots_disallow_is_respected_without_requesting_the_page(db):
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, text="<html><body>ok</body></html>")

    f = _fetcher(db, handler)
    doc = f.fetch("https://portal.example/private/list", purpose="listing")
    assert not doc.ok and doc.blocked["type"] == "robots"
    assert "/private/list" not in seen
    ok = f.fetch("https://portal.example/public/list", purpose="listing")
    assert ok.ok
    src = db.get(AcqSource, "portal.example")
    assert src.disallowed == 1 and src.ok == 1
    row = db.scalar(select(AcqDocument).where(AcqDocument.url == "https://portal.example/private/list"))
    assert row.robots_allowed is False


def test_access_denied_and_captcha_walls_are_recorded_not_bypassed(db):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path == "/denied":
            return httpx.Response(405, text="Not allowed")
        return httpx.Response(200, text='<html><body><div class="g-recaptcha"></div>Verify you are human</body></html>')

    f = _fetcher(db, handler)
    d1 = f.fetch("https://wall.example/denied", purpose="listing")
    d2 = f.fetch("https://wall.example/check", purpose="listing")
    assert d1.blocked["type"] == "access_denied" and d2.blocked["type"] == "captcha"
    assert db.get(AcqSource, "wall.example").blocked == 2
    # nothing from a blocked page is cached as content
    assert all(not r.cache_path for r in db.scalars(select(AcqDocument).where(AcqDocument.host == "wall.example")))


def test_address_granularity_and_station_subsets_are_not_conflicts(db):
    t0 = utcnow()
    store = ClaimStore(db, HousingAdapter().policy(), now=t0)
    b = _entity(db, "building")
    _claim(store, b, "address", "東京都世田谷区上馬2", "a.example", t0)
    _claim(store, b, "address", "東京都世田谷区上馬2丁目10-8", "b.example", t0)
    _claim(store, b, "name", "カーザ・エッチェルサ世田谷", "a.example", t0)
    _claim(store, b, "name", "カ-ザ・エッチェルサ世田谷", "b.example", t0)
    _claim(store, b, "stations", [{"station": "三軒茶屋", "walk_min": 9}, {"station": "駒沢大学", "walk_min": 14}],
           "a.example", t0)
    _claim(store, b, "stations", [{"station": "三軒茶屋", "walk_min": 10}], "b.example", t0)
    db.flush()
    bel = store.refresh(b)
    assert not bel["address"]["conflict"] and bel["address"]["value"] == "東京都世田谷区上馬2丁目10-8"
    assert not bel["name"]["conflict"]
    st = bel["stations"]
    assert not st["conflict"] and {s["station"] for s in st["value"]} == {"三軒茶屋", "駒沢大学"}
    # a genuinely different street number, or a walk time 6 minutes apart, *is* a conflict
    _claim(store, b, "address", "東京都世田谷区上馬2丁目3-1", "c.example", t0)
    _claim(store, b, "stations", [{"station": "三軒茶屋", "walk_min": 16}], "c.example", t0)
    db.flush()
    bel = store.refresh(b)
    assert bel["address"]["conflict"] and bel["stations"]["conflict"]
    assert bel["stations"]["disputes"][0]["item"] == "三軒茶屋"
