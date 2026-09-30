"""Geography-neutral housing acquisition: locale parsing, generic identity, cross-region strategies."""

from regent.acquisition.housing import locale as L
from regent.acquisition.housing.adapter import housing_strategies
from regent.acquisition.housing.geography import GeographyResolver, language_of, stay_prior
from regent.acquisition.housing.resolver import HousingResolver


def test_money_formats_and_periods_from_many_places():
    cases = [("£2,850 per month", "GBP", "GBP", 2850, "month"), ("$850 per week", "NZD", "NZD", 850, "week"),
             ("1.899 € Kaltmiete", "EUR", "EUR", 1899, "month"), ("£875 pcm", "GBP", "GBP", 875, "month"),
             ("US$20.34", "EUR", "USD", 20.34, None), ("1.650 € /mes", "EUR", "EUR", 1650, "month")]
    for text, default, cur, amount, period in cases:
        m = L.money(text, default)[0]
        assert (m.currency, round(m.amount, 2), m.period) == (cur, amount, period), text
    assert round(L.money("$850 per week", "NZD")[0].monthly()) == 3683         # weekly rents normalized to months
    assert L.money("$3,400", "USD")[0].monthly() == 3400


def test_rooms_area_and_unit_kinds_across_languages():
    assert L.bedrooms("2 bed flat to rent") == 2 and L.bedrooms("Studio flat") == 0
    assert L.bedrooms("3 habs. 2 baños") == 3 and L.rooms("2-Zimmer-Wohnung") == 2
    assert L.area_m2("650 sq ft") == 60.4 and L.area_m2("63 m2") == 63
    assert L.unit_kind("WG-Zimmer in Friedrichshain") == "room"
    assert L.unit_kind("Industriepalast Hostel Berlin") == "hostel_bed"
    assert L.available_from("Available from 01/11/2026") == "2026-11-01"
    assert L.available_from("Available to rent, fully furnished") is None
    assert L.postcode_place("Nachmieter gesucht 12099 Tempelhof", "DE") == ("12099", "Tempelhof")


def test_principal_evidence_is_not_a_decision():
    g = GeographyResolver(adapter=None)
    p = g.principal({"mission_text": "住居を安定させたい"})
    assert p["home"] == "JP" and p["home_confidence"] < 0.9 and "weak signal" in p["home_evidence"]
    assert language_of("I need stable housing") == "en" and g.principal({"mission_text": "I need housing"})["home"] is None
    # unknown citizenship: living abroad is possible but uncertain; the likely home country is likelier
    assert stay_prior("DE", p) < 0.5 < stay_prior("JP", p) < 0.95
    assert stay_prior("DE", {"citizenship": "DE"}) == 0.95


def test_generic_identity_rules():
    r = HousingResolver()
    unit = {"listing_url": "rightmove.co.uk/properties/1", "country": "GB", "bedrooms": 1, "area_m2": 45.0,
            "rent": 2000, "currency": "GBP", "kind": "apartment", "title_key": "1bedflatcamden", "_host": "rightmove"}
    e = r.merge("unit", {}, unit)
    assert r.score("unit", dict(unit), e)[0] > 0.99                       # same listing URL
    other = {**unit, "listing_url": "rightmove.co.uk/properties/2"}
    assert r.score("unit", other, e)[0] < 0.05                             # same site, other listing
    xsite = {**unit, "listing_url": "openrent.co.uk/p/9", "_host": "openrent", "rent": 2020}
    assert r.score("unit", xsite, e)[0] > 0.85                             # same flat on another portal
    jp = {"layout": "1K", "area_m2": 20.5, "floor": 2, "rent": 82000}
    assert r.score("unit", jp, e)[0] == 0.0                                 # never across geography models


def _world(stay_de=0.3):
    regions = [{"id": "rg_jp", "kind": "region", "name": "Osaka, JP",
                "attrs": {"country": "JP", "home": True, "stay_p": 0.77, "currency": "JPY", "distance_km": 0}},
               {"id": "rg_de", "kind": "region", "name": "Berlin, DE",
                "attrs": {"country": "DE", "home": False, "stay_p": stay_de, "currency": "EUR", "distance_km": 8915,
                          "stay_basis": "citizenship unknown"}}]

    def unit(i, rid, monthly_ref, rent):
        return {"id": f"u{i}", "kind": "unit", "name": f"Unit {i}", "attrs": {
            "stage": "shortlisted", "region_id": rid, "monthly": rent, "rent": rent, "monthly_ref": monthly_ref,
            "availability_confidence": 0.8, "score": 0.5 - i / 100, "kind": "apartment", "housing_type": "rent",
            "acq_entity": f"u{i}", "fresh": True}}
    units = [unit(1, "rg_jp", 51000, 51000), unit(2, "rg_jp", 55000, 55000),
             unit(3, "rg_de", 180000, 1000), unit(4, "rg_de", 190000, 1060)]
    facts = {"housing.ref_currency": "JPY", "housing.ref_per_usd": 150.0,
             "housing.market_by_region_ref": {"Berlin, DE": {"room": 110000, "furnished": 170000, "hostel_bed": 95000,
                                                             "apartment": 180000}}}
    return {"facts": facts, "entities": regions + units}


def test_strategies_compete_across_borders_with_the_right_to_stay_as_uncertainty():
    routes = {r.key: r for r in housing_strategies({"id": "m1"}, _world())}
    home, abroad = routes["housing-lease-jp-osaka"], routes["housing-lease-de-berlin"]
    assert "relocation" in abroad.tags and "relocation" not in home.tags
    assert any(u.fact_key == "principal.right_to_reside.DE" for u in abroad.uncertainty)
    assert abroad.estimates.success_probability < home.estimates.success_probability
    assert abroad.estimates.money_cost > 1000 * 150 * 0.08          # relocation prior included, in JPY
    # "no right to live there" collapses the route; "yes" restores availability-only odds
    no = next(s for s in abroad.sensitivities if s.fact == "principal.right_to_reside.DE" and s.value is False)
    yes = next(s for s in abroad.sensitivities if s.fact == "principal.right_to_reside.DE" and s.value is True)
    assert no.effects["success_probability"].set == 0.01
    assert yes.effects["success_probability"].set > abroad.estimates.success_probability
    # live room / hostel evidence feeds the non-lease strategies; not signing is always an option
    assert "Berlin" in routes["housing-share"].title and "Berlin" in routes["housing-hostel"].title
    assert "housing-defer" in routes
