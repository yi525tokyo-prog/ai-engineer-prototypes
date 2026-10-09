"""Geography-independent housing ontology: Region -> Building -> Unit (-> listing claims).

Amounts are claimed in the currency the source used (``rent`` is monthly, ``currency``
says which); comparison across regions converts through FX claims at read time, so a
rent is never silently re-denominated. Country-specific attributes (Japanese layouts,
key money, station walk times) are part of the same ontology -- a pack that does not
know them simply never claims them.
"""

from __future__ import annotations

import re

from regent.acquisition.housing import text as JT
from regent.acquisition.types import AttrSpec

_CJK = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


def address_key(v) -> str:
    """Japanese addresses: hierarchical prefecture|city|town|chome key; others: street-level key."""
    s = str(v or "")
    if _CJK.search(s):
        return JT.address_key(s)
    from regent.acquisition.housing.generic_extract import address_key as generic_key

    return generic_key(s)


def name_key(v) -> str:
    return JT.name_key(str(v or ""))

H, D, Y = 3600.0, 86400.0, 365 * 86400.0

ATTRS: dict[str, AttrSpec] = {a.name: a for a in [
    # ---- unit / listing: time-sensitive
    AttrSpec("availability", "unit", "bool", ttl_s=6 * H),
    AttrSpec("rent", "unit", "number", "local currency / month", ttl_s=24 * H, rel_tol=0.005),
    AttrSpec("currency", "unit", "text", ttl_s=Y),
    AttrSpec("rent_period", "unit", "text", ttl_s=30 * D),
    AttrSpec("management_fee", "unit", "number", ttl_s=24 * H, abs_tol=100),
    AttrSpec("deposit", "unit", "number", ttl_s=24 * H, rel_tol=0.01, abs_tol=100),
    AttrSpec("key_money", "unit", "number", ttl_s=24 * H, rel_tol=0.01, abs_tol=100),
    AttrSpec("move_in", "unit", "text", ttl_s=24 * H),
    AttrSpec("info_updated_at", "unit", "date", ttl_s=24 * H),
    AttrSpec("next_update_at", "unit", "date", ttl_s=24 * H),
    AttrSpec("earliest_move_in_est", "unit", "date", ttl_s=24 * H),
    AttrSpec("move_in_feasible", "unit", "bool", ttl_s=24 * H),
    AttrSpec("conditions", "unit", "text", ttl_s=7 * D), AttrSpec("lease_term", "unit", "text", ttl_s=30 * D),
    AttrSpec("guarantor", "unit", "text", ttl_s=30 * D), AttrSpec("other_initial_cost", "unit", "text", ttl_s=7 * D),
    AttrSpec("renewal_fee", "unit", "text", ttl_s=30 * D), AttrSpec("transaction_type", "unit", "text", ttl_s=7 * D),
    AttrSpec("internet", "unit", "text", ttl_s=90 * D), AttrSpec("nearby_pois", "unit", "json", ttl_s=90 * D),
    AttrSpec("housing_type", "unit", "text", ttl_s=30 * D),
    AttrSpec("unit_kind", "unit", "text", ttl_s=30 * D),          # studio|apartment|room|house|hostel_bed
    AttrSpec("title", "unit", "text", ttl_s=30 * D),
    AttrSpec("listing_agent", "unit", "text", ttl_s=30 * D), AttrSpec("agent_domain", "unit", "text", ttl_s=30 * D),
    AttrSpec("operator_verified", "unit", "bool", ttl_s=24 * H),
    # ---- unit: stable
    AttrSpec("bedrooms", "unit", "number", ttl_s=Y),
    AttrSpec("rooms", "unit", "number", ttl_s=Y, abs_tol=0.5),
    AttrSpec("bathrooms", "unit", "number", ttl_s=Y),
    AttrSpec("layout", "unit", "text", ttl_s=180 * D),            # Japanese floor plan code (1K, 1LDK, ...)
    AttrSpec("area_m2", "unit", "number", "m2", ttl_s=Y, abs_tol=0.15),
    AttrSpec("floor", "unit", "number", ttl_s=Y), AttrSpec("room_number", "unit", "text", ttl_s=Y),
    # ---- building
    AttrSpec("name", "building", "text", ttl_s=180 * D, key=name_key),
    AttrSpec("address", "building", "hier", ttl_s=Y, key=address_key),
    AttrSpec("locality", "building", "text", ttl_s=Y), AttrSpec("postcode", "building", "text", ttl_s=Y),
    AttrSpec("stations", "building", "set", ttl_s=180 * D, item_key="station", item_value="walk_min", abs_tol=3),
    AttrSpec("built_year", "building", "number", ttl_s=Y, abs_tol=1),
    AttrSpec("floors_total", "building", "number", ttl_s=Y), AttrSpec("structure", "building", "text", ttl_s=Y),
    AttrSpec("building_type", "building", "text", ttl_s=30 * D), AttrSpec("coords", "building", "json", ttl_s=Y),
    AttrSpec("nearest_stations_public", "building", "json", ttl_s=180 * D),
    AttrSpec("walk_check", "building", "json", ttl_s=180 * D),
    AttrSpec("rail_distance_m", "building", "number", ttl_s=Y, abs_tol=5),
    AttrSpec("libraries_nearby", "building", "json", ttl_s=90 * D),
    AttrSpec("universities_nearby", "building", "json", ttl_s=90 * D),
    AttrSpec("hub_minutes_est", "building", "json", ttl_s=180 * D),
    AttrSpec("centre_km", "building", "number", ttl_s=Y, abs_tol=0.3),
    AttrSpec("review", "building", "text", ttl_s=30 * D),
    AttrSpec("vacancies", "building", "number", ttl_s=24 * H),
    # ---- area (market data inside a region)
    AttrSpec("listing_count", "area", "number", ttl_s=7 * D, rel_tol=0.2),
    AttrSpec("market_rent_low", "area", "number", ttl_s=30 * D, rel_tol=0.05),
    AttrSpec("market_rent_high", "area", "number", ttl_s=30 * D, rel_tol=0.05),
    AttrSpec("market_currency", "area", "text", ttl_s=Y),
    # ---- region (a city the principal could live in)
    AttrSpec("country", "region", "text", ttl_s=Y), AttrSpec("region_coords", "region", "json", ttl_s=Y),
    AttrSpec("population", "region", "number", ttl_s=Y, rel_tol=0.1),
    AttrSpec("region_currency", "region", "text", ttl_s=Y),
    AttrSpec("fx_per_ref", "region", "number", ttl_s=2 * D, rel_tol=0.01),   # units of local currency per 1 ref
    AttrSpec("price_signal_monthly", "region", "number", ttl_s=7 * D, rel_tol=0.1),
    AttrSpec("sources_known", "region", "number", ttl_s=7 * D),
    AttrSpec("stay_rules", "region", "json", ttl_s=30 * D),
    AttrSpec("languages", "region", "json", ttl_s=Y),
]}
MARKET = AttrSpec("market_rent", "area", "number", ttl_s=30 * D, rel_tol=0.01)


def spec(attr: str) -> AttrSpec | None:
    if attr.startswith("market_rent_") and attr not in ATTRS:
        return MARKET
    return ATTRS.get(attr)
