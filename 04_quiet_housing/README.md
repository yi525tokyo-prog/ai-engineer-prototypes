# 04 · quiethousing — goodroom listings screened for a quiet working environment

Crawls real rental listings from [goodroom](https://www.goodrooms.jp/), applies
configurable housing constraints, resolves each building's coordinates,
measures the surroundings from OpenStreetMap and MLIT station-ridership data,
and rejects places likely to be noisy or too busy for focused reading and
computer work. Every decision lists the measurements and rules behind it.

```
goodroom search pages ─► listing stubs ─► list-level constraints ─► detail + map pages
      (100/page, cached)                       (rent, area, walk…)     (address, floor, built, pin)
                                                                              │
          GSI address geocoder (cross-check) ◄────────────── coordinates ◄────┘
                                                                  │
   Overpass/OSM tiles (2.2 km grid, cached) + MLIT S12 ridership ─► environment profile
                                                                  │   roads by class, rails + tracks,
                                                                  │   stations, junctions, POIs, landuse
                                                                  ▼
                                      configurable rules + scores ─► KEEP / REJECT + reasons ─► UI / CSV / JSON
```

## Quick start

```bash
pip install -r requirements.txt
python -m quiethousing --data data init-config     # writes data/config.json (edit constraints there or in the UI)
python -m quiethousing --data data run             # crawl → details → coords → OSM → evaluate → export
python -m quiethousing --data data serve           # http://127.0.0.1:8765
python -m quiethousing --data data report -n 20 --decision KEEP
python -m quiethousing --data data probe 35.6946 139.7031   # profile any point (here: Kabukichō)
python -m pytest -q tests
```

Re-runs are incremental: list pages are re-fetched after `list_ttl_hours`, detail
pages after `detail_ttl_hours`, OSM tiles and geocodes are cached permanently, and
environment profiles are cached per location (rooms in one building share one).
Changing constraints or evaluation thresholds needs no network at all — results
are recomputed from stored data (UI: *save & re-evaluate*).

## Data sources (and how they were chosen)

| Need | Source | Notes |
|---|---|---|
| Listings | goodroom search pages `/{region}/search/estate_list/?rent_count=100&p=N` | No public JSON API; server-rendered HTML with stable class names. `rent_count=100` cuts requests ~3×. `robots.txt` allows these paths. The site's rent filter params are ignored server-side, so all constraints are applied locally. Area filter `small_area_cd[]` works and is exposed as `acquisition.small_area_codes`. |
| Details | `/{region}/detail/{cd}/{id}/` | street address, all stations + walk, built year/month, structure, storeys, orientation, lease type, facilities. Floor is derived from the room number (`304号室` → 3F) and sanity-checked against building height. |
| Coordinates | `/{…}/map/` → `mapInitialize(lat, lon, zoom)` | the listing's own pin. |
| Geocode check | GSI 国土地理院 AddressSearch | house-number precision; if it disagrees with the pin by >150 m at house precision, GSI wins and the listing carries a warning. |
| Roads, rails, POIs, landuse | OpenStreetMap: regional extract (`kanto-latest.osm.pbf` from openstreetmap.fr, ~600 MB, downloaded once) → local tiles; Overpass as fallback | stored as fixed 0.02°×0.025° tiles per *layer*, identical format from both sources. Public Overpass instances turned out to be unreliable for bulk use (overpass-api.de unreachable from the test environment, the mail.ru mirror returning 504 under load), so the extract is the primary path and Overpass only fills tiles outside the extract. Each layer is versioned separately so new layers (parks, libraries…) don't invalidate cached ones. |
| Station scale | MLIT 国土数値情報 S12 (駅別乗降客数) | daily riders per station; records grouped into transfer complexes (Shinjuku ≈ 3.9 M/day). Downloaded once (6 MB). |

## What is measured (per location)

* **road** — nearest *surface* distance per class (motorway, trunk, primary, secondary, tertiary, unclassified, residential, living_street, service); `_link` ramps count as their parent; tunnels / covered / layer<0 are excluded (Tokyo's Yamate tunnel, underground ramps); elevated expressways flagged; metres of trunk/primary/motorway inside 100/250 m.
* **rail** — nearest surface railway (subways in tunnels excluded and reported separately; sidings/yards reported separately), number of parallel tracks at the nearest point (perpendicular probe), track length within 250/500 m, distinct lines within 500 m, elevated flag.
* **station** — nearest station and its ridership; largest complex within 300/500/800 m.
* **intersection** — nearest junction of the trunk/primary/secondary network involving ≥2 distinct roads and at least one trunk/primary; count within 250 m; traffic signals within 100/250 m.
* **poi** — counts within 100/250/500/1000 m for commercial, food, restaurant, bar, nightlife (bars, pubs, izakaya, clubs, karaoke, pachinko, love hotels…), karaoke, nightclub, convenience, retail, entertainment.
* **landuse** — share of the 250/500 m disc mapped as commercial, retail, industrial, residential.
* **data** — coverage indicators (roads in range, POIs in 1 km) so under-mapped areas raise a warning instead of looking quiet.

## Evaluation (all in `config.json → evaluation`)

* `hard_rules`: `{"metric": "road.primary_m", "op": "<", "value": 40, "label": "primary road {v}m away"}` — any hit ⇒ REJECT with that sentence.
* risk curves per source (`near` → full risk, `far` → zero), parallel-track and elevated multipliers, station-size × distance, density saturation counts.
* `quality = 100 − (max_blend·max(sub-risks) + (1−max_blend)·weighted mean)`; below `min_quality` ⇒ REJECT.
* Output per property: `road_noise_score`, `railway_noise_score`, `nightlife_score`, `commercial_activity_score`, `total_environment_score`, `decision`, `reasons`, `warnings`, plus the raw profile.

No LLM is used: every question here is answerable from geometry and counts.

## UI

`python -m quiethousing serve` — constraints form, run buttons with live log,
sortable table (quality, sub-scores, rent, distances…), detail pane with
reasons, raw measurements and a Leaflet map overlaying the roads/rails/POIs/
junctions that drove the score, plus a link to the original goodroom listing.

## Layout

```
quiethousing/
  goodroom/parse.py   pure HTML parsers (list, detail, map)
  http.py             throttled fetcher + page cache
  store.py            SQLite: listings, page cache, geocodes, profiles, runs
  constraints.py      property record + housing constraints
  geocode.py          goodroom pin vs GSI geocoder
  geo/overpass.py     tile grid + layered Overpass cache
  geo/features.py     OSM → classified roads/rails/POIs/landuse
  geo/stations.py     MLIT S12 ridership → station complexes
  geo/metrics.py      measurements (STR-trees, local projection)
  scoring.py          rules, curves, decision, reasons
  pipeline.py         incremental stages + export
  web/                stdlib HTTP server + single-page UI
```

Extending: add an Overpass layer in `geo/overpass.py:LAYERS`, classify it in
`geo/features.py`, add a `_xxx()` measurement in `FeatureIndex.measure`, and a
sub-score/rule in `scoring.py`. Bumping `METRICS_VERSION` recomputes profiles
from cached tiles without new downloads of existing layers.
