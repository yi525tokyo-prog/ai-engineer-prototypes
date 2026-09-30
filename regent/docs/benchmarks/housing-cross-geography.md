# Housing benchmark — 「住居を安定させたい」

A fresh world received only the sentence above: no tags, no candidates, no places, no budget, no
country. Everything below was acquired by Regent from the public web during the run.

## Result: PASS — the world was not silently narrowed to JP

- [x] regions came from the candidate world, not a default
- [x] >= 2 non-home regions with >= 5 live units each
- [x] >= 2 different countries acquired outside the home country
- [x] strategies compete across borders (a relocation route exists)
- [x] right to stay abroad is an explicit uncertainty

## Acquisition requests (created by the loop's ACQUIRE phase)

| request | action | status | pages | mentions | claims |
|---|---|---|---|---|---|
| acq_68293363de74 | discover | done | 204 | 979 | 8322 |
| acq_693dadaaa292 | recheck | done | 1 | 2 | 21 |
| acq_9c0fe2d52489 | recheck | done | 2 | 2 | 21 |
| acq_7d7465d146e9 | enrich | done | 12 | 81 | 658 |
| acq_f183e0044a5e | enrich | done | 5 | 35 | 244 |
| acq_5441910f9361 | enrich | done | 3 | 27 | 183 |
| acq_75bf450bf826 | enrich | done | 1 | 9 | 59 |
| acq_3f42e859ea3d | enrich | done | 2 | 18 | 120 |
| acq_eaedfb1e5f5a | enrich | done | 1 | 9 | 60 |
| acq_f84abd83a619 | enrich | done | 0 | 0 | 0 |
| acq_55484fc245f1 | recheck | done | 9 | 18 | 188 |
| acq_82ccee79dc32 | enrich | done | 0 | 0 | 0 |
| acq_d35ce0789925 | recheck | done | 1 | 1 | 10 |
| acq_e9d511da452d | enrich | done | 1 | 1 | 24 |
| acq_67912f875b93 | recheck | done | 1 | 0 | 0 |
| acq_e9b461e92418 | enrich | done | 0 | 0 | 0 |
| acq_9ce2e9a3c42a | enrich | done | 0 | 0 | 0 |

## Geography: where could the principal live?

- principal evidence: home=JP (mission written in 'ja' (weak signal of current country)); citizenship=unknown
- reference currency: JPY (ECB rates: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml)
- method: 592 candidate cities from public directories; shortlist of 9 by supply, source coverage and diversity; live price signals converted to JPY with ECB rates; utility = affordability 0.40 + supply 0.20 + coverage 0.15 + stay prior 0.15 + relocation distance 0.10; one city per country
- assumption: geography — no place stated: regions compete, current country is only evidence
- assumption: citizenship — citizenship unknown: the right to live abroad is an uncertainty
- assumption: household — household size unknown: assuming one person
- assumption: max_rent — budget unknown: bounded by each region's live market
- assumption: work_location — work location unknown

| region | price signal (/month) | utility | P(may live there) | distance | units acquired |
|---|---|---|---|---|---|
| Osaka (JP) — home evidence | 51,000 JPY | 0.6876 | 0.77 | 397 km | 131 |
| Tokyo (JP) — home evidence | 73,000 JPY | 0.6587 | 0.77 | 0 km | 155 |
| Berlin (DE) | 197,143 JPY | 0.5121 | 0.3 | 8915 km | 173 |
| Madrid (ES) | 166,367 JPY | 0.4869 | 0.3 | 10762 km | 156 |
| Auckland (NZ) | 273,400 JPY | 0.1213 | 0.3 | 8841 km | 20 |

Considered and not acquired:

- Lisbon (PT): two regions in Europe already rank higher (diversity), 165,029 JPY
- Brussels (BE): two regions in Europe already rank higher (diversity), 169,489 JPY
- Rome (IT): two regions in Europe already rank higher (diversity), 205,171 JPY
- Washington (US): no live price signal acquired

### Per region: sources and discovery

**Osaka (JP)** — 131 units; areas: 堺市南区

**Tokyo (JP)** — 155 units; areas: 江戸川区

**Berlin (DE)** — 173 units
- source www.wg-gesucht.de (pack:DE): 59 records
- source www.kleinanzeigen.de (pack:DE): 27 records
- source www.immowelt.de (None): 0 records [blocked]
- source housinganywhere.com (global): 10 records
- source www.spotahome.com (global): 88 records
- source www.craigslist.org (None): 0 records [no_listings]
- source www.hostelworld.com (None): 0 records [no_listings]

**Madrid (ES)** — 156 units
- discovery via probe: www.pisos.es → blocked (disallowed by robots.txt)
- discovery via probe: www.pisos.com → rejected (no Madrid listings reachable by navigation (0 records))
- discovery via probe: www.fotocasa.es → verified, 30 records
- discovery via probe: alquiler.com → rejected (no Madrid listings reachable by navigation (0 records))
- discovery via probe: www.inmobiliaria.es → blocked (disallowed by robots.txt)
- discovery via probe: www.inmobiliaria.com → blocked (HTTP 403)
- discovery via probe: www.casas.es → rejected (no Madrid listings reachable by navigation (0 records))
- discovery via probe: www.casas.com → blocked (disallowed by robots.txt)
- source www.fotocasa.es (discovered:probe): 61 records
- source www.spotahome.com (global): 92 records
- source housinganywhere.com (global): 10 records
- source www.craigslist.org (None): 0 records [no_listings]
- source www.hostelworld.com (None): 0 records [no_listings]

**Auckland (NZ)** — 20 units
- discovery via snowball: agent.realestate.co.nz → rejected (no Auckland listings reachable by navigation (0 records))
- discovery via probe: www.rent.co.nz → blocked (disallowed by robots.txt)
- discovery via probe: www.hirepool.co.nz → rejected (no Auckland listings reachable by navigation (0 records))
- discovery via probe: www.lettings.co.nz → blocked (disallowed by robots.txt)
- discovery via probe: www.flats.co.nz → blocked (HTTP 403)
- discovery via probe: www.rooms.co.nz → blocked (CAPTCHA detected (name="recaptcha))
- discovery via probe: www.property.co.nz → blocked (disallowed by robots.txt)
- discovery via probe: homes.co.nz → rejected (no Auckland listings reachable by navigation (0 records))
- discovery via probe: www.apartments.co.nz → blocked (HTTP 202 bot challenge)
- source www.realestate.co.nz (pack:NZ): 20 records
- source www.spotahome.com (None): 0 records [no_listings]
- source housinganywhere.com (None): 0 records [no_listings]
- source www.craigslist.org (None): 0 records [no_listings]
- source www.hostelworld.com (None): 0 records [no_listings]

### Funnel

722 listing mentions → 635 units after identity resolution (402 buildings) → 289 pass → 50 filtered → 15 deep research

| region | units | pass | shortlisted | market median /month |
|---|---|---|---|---|
| Osaka, JP | 131 | 28 | 3 | 83,000 JPY |
| Tokyo, JP | 155 | 59 | 3 | 130,000 JPY |
| Berlin, DE | 173 | 88 | 3 | 1,098 EUR |
| Madrid, ES | 156 | 101 | 3 | 1,250 EUR |
| Auckland, NZ | 20 | 13 | 3 | 3,152 NZD |

Rejection reasons: N mN below N mN ×8; N min walk to station ×23; monthly N above regional market cap N ×210; layout NK larger than needed for household=N ×8; layout NDK larger than needed for household=N ×26; N bedrooms: larger than needed for household=N ×28; layout NLDK larger than needed for household=N ×130; layout NSDK larger than needed for household=N ×1; layout NSLDK larger than needed for household=N ×2

## Sources

| host | kind | pages ok/fetched | records | blocked | robots-disallowed | status |
|---|---|---|---|---|---|---|
| geocoding-api.open-meteo.com | public_data | 28/28 | 0 | 0 | 0 | ok static |
| www.spotahome.com | portal | 23/23 | 338 | 0 | 0 | ok static |
| suumo.jp | portal | 22/22 | 313 | 0 | 0 | ok static |
| www.zillow.com | portal | 7/17 | 0 | 10 | 0 | blocked: robots |
| www.pisos.com | portal | 17/17 | 0 | 0 | 0 | ok static |
| www.ur-net.go.jp | operator | 16/16 | 39 | 0 | 0 | ok static |
| housinganywhere.com | portal | 15/15 | 20 | 0 | 0 | ok static |
| www.kleinanzeigen.de | portal | 14/14 | 27 | 0 | 0 | ok static |
| www.homes.co.jp | portal | 12/12 | 128 | 0 | 0 | ok browser |
| www.craigslist.org | portal | 10/10 | 0 | 0 | 0 | ok static |
| www.wg-gesucht.de | portal | 10/10 | 77 | 0 | 0 | ok static |
| www.realestate.co.nz | portal | 10/10 | 29 | 0 | 0 | ok static |
| www.chintai.net | portal | 9/9 | 85 | 0 | 0 | ok static |
| msearch.gsi.go.jp | public_data | 9/9 | 0 | 0 | 0 | ok static |
| www.oakhouse.jp | operator | 9/9 | 48 | 0 | 0 | ok browser |
| www.hirepool.co.nz | portal | 7/7 | 0 | 0 | 0 | ok static |
| www.hostelworld.com | portal | 6/6 | 0 | 0 | 0 | ok static |
| www.monthly-mansion.com | portal | 6/6 | 17 | 0 | 0 | ok static |
| www.rent.com | portal | 6/6 | 0 | 0 | 0 | ok static |
| nlftp.mlit.go.jp | public_data | 5/5 | 0 | 0 | 0 | ok download |
| www.fotocasa.es | portal | 5/5 | 61 | 0 | 0 | ok static |
| alquiler.com | portal | 3/3 | 0 | 0 | 0 | ok static |
| www.athome.co.jp | portal | 0/2 | 0 | 2 | 0 | blocked: access_denied |
| homes.co.nz | portal | 2/2 | 0 | 0 | 0 | ok static |
| www.casas.es | portal | 2/2 | 0 | 0 | 0 | ok static |
| agent.realestate.co.nz | portal | 1/1 | 0 | 0 | 0 | ok static |
| www.immowelt.de | portal | 0/1 | 0 | 1 | 0 | blocked: access_denied |
| agentpro.co.nz | portal | 1/1 | 0 | 0 | 0 | ok static |
| www.rentals.co.nz | portal | 1/1 | 0 | 0 | 0 | ok static |
| www.inmobiliaria.com | portal | 0/1 | 0 | 1 | 0 | blocked: access_denied |
| www.rooms.co.nz | portal | 0/1 | 0 | 1 | 0 | blocked: captcha |
| www.homes.co.nz | portal | 1/1 | 0 | 0 | 0 | ok static |
| www.apartments.co.nz | portal | 0/1 | 0 | 1 | 0 | blocked: challenge |
| www.alquiler.com | portal | 1/1 | 0 | 0 | 0 | ok static |
| www.immigration.govt.nz | public_data | 1/1 | 0 | 0 | 0 | ok static |
| www.alquiler.es | portal | 1/1 | 0 | 0 | 0 | ok static |
| www.ecb.europa.eu | public_data | 1/1 | 0 | 0 | 0 | ok static |
| www.exteriores.gob.es | public_data | 0/1 | 0 | 0 | 0 | error 404  |
| www.flats.co.nz | portal | 0/1 | 0 | 1 | 0 | blocked: access_denied |
| www.lettings.co.nz | portal | 0/0 | 0 | 0 | 1 | robots_disallow |
| www.inmobiliaria.es | portal | 0/0 | 0 | 0 | 1 | robots_disallow |
| www.pisos.es | portal | 0/0 | 0 | 0 | 1 | robots_disallow |
| identity.zillow.com | portal | 0/0 | 0 | 0 | 0 |  |
| www.casas.com | portal | 0/0 | 0 | 0 | 1 | robots_disallow |
| im.forsale | portal | 0/0 | 0 | 0 | 0 |  |
| www.rent.co.nz | portal | 0/0 | 0 | 0 | 1 | robots_disallow |
| www.property.co.nz | portal | 0/0 | 0 | 0 | 1 | robots_disallow |

### Source registry (what Regent now knows how to use)

| scope | host | origin | status | records | note |
|---|---|---|---|---|---|
| * | www.craigslist.org | global | candidate | 0 | Auckland: no listings reached (ok static) |
| * | www.hostelworld.com | global | candidate | 0 | Auckland: no listings reached (ok static) |
| * | www.spotahome.com | global | verified | 180 | Auckland: no listings reached (ok static) |
| * | housinganywhere.com | global | verified | 20 | Auckland: no listings reached (ok static) |
| DE | www.immowelt.de | pack:DE | blocked | 0 | Berlin: no listings reached (blocked: access_denied) |
| DE | www.wg-gesucht.de | pack:DE | verified | 59 |  |
| DE | www.kleinanzeigen.de | pack:DE | verified | 27 |  |
| ES | www.inmobiliaria.com | discovered:probe | blocked | 0 | HTTP 403 |
| ES | www.inmobiliaria.es | discovered:probe | blocked | 0 | disallowed by robots.txt |
| ES | www.pisos.es | discovered:probe | blocked | 0 | disallowed by robots.txt |
| ES | www.casas.com | discovered:probe | blocked | 0 | disallowed by robots.txt |
| ES | www.pisos.com | discovered:probe | rejected | 0 | no Madrid listings reachable by navigation (0 records) |
| ES | alquiler.com | discovered:probe | rejected | 0 | no Madrid listings reachable by navigation (0 records) |
| ES | www.casas.es | discovered:probe | rejected | 0 | no Madrid listings reachable by navigation (0 records) |
| ES | www.fotocasa.es | discovered:probe | verified | 91 |  |
| NZ | www.lettings.co.nz | discovered:probe | blocked | 0 | disallowed by robots.txt |
| NZ | www.rent.co.nz | discovered:probe | blocked | 0 | disallowed by robots.txt |
| NZ | www.flats.co.nz | discovered:probe | blocked | 0 | HTTP 403 |
| NZ | www.rooms.co.nz | discovered:probe | blocked | 0 | CAPTCHA detected (name="recaptcha) |
| NZ | www.property.co.nz | discovered:probe | blocked | 0 | disallowed by robots.txt |
| NZ | www.apartments.co.nz | discovered:probe | blocked | 0 | HTTP 202 bot challenge |
| NZ | agent.realestate.co.nz | discovered:snowball | rejected | 0 | no Auckland listings reachable by navigation (0 records) |
| NZ | www.hirepool.co.nz | discovered:probe | rejected | 0 | no Auckland listings reachable by navigation (0 records) |
| NZ | homes.co.nz | discovered:probe | rejected | 0 | no Auckland listings reachable by navigation (0 records) |
| NZ | www.realestate.co.nz | pack:NZ | verified | 20 |  |
| US | www.rent.com | pack:US | candidate | 0 | price signal: no Washington listings reached |
| US | www.zillow.com | pack:US | candidate | 0 | price signal: no Washington listings reached |

## World model

- documents: 303, claims: 10013, regions: 5
- identity decisions: {'pinned': 51, 'rejected': 1306, 'ambiguous': 214, 'merged': 490, 'new': 1315}
- units seen on ≥2 sites: 21
- open conflicts (kept, not overwritten): 47 {'stations': 5, 'rent': 7, 'management_fee': 1, 'key_money': 2, 'title': 6, 'address': 8, 'rent_period': 4, 'name': 5, 'floors_total': 1, 'unit_kind': 3, 'internet': 1, 'info_updated_at': 1, 'conditions': 1, 'lease_term': 1, 'structure': 1}
- enrichment jobs: {'recheck:done': 14, 'detail:done': 25, 'geocode:done': 9, 'stations:done': 9, 'geocode:skipped': 8, 'rail_noise:done': 9, 'centre:skipped': 8, 'facilities:done': 9, 'hubs:done': 9, 'move_in:skipped': 86, 'operator_page:skipped': 4, 'move_in:done': 4}

## Deep-research shortlist (per region)

### 1. [Auckland, NZ] Hyperfibre 2000 — apartment (score 0.585; sources: www.realestate.co.nz)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 149 NZD | 0.72 | www.realestate.co.nz | yes |  |
| rent_period | month | 0.72 | www.realestate.co.nz | yes |  |
| unit_kind | apartment | 0.56 | www.realestate.co.nz | yes |  |
| availability | yes | 0.56 | www.realestate.co.nz | yes |  |

### 2. [Auckland, NZ] Hyperfibre 4000 — apartment (score 0.582; sources: www.realestate.co.nz)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 179 NZD | 0.72 | www.realestate.co.nz | yes |  |
| rent_period | month | 0.72 | www.realestate.co.nz | yes |  |
| unit_kind | apartment | 0.56 | www.realestate.co.nz | yes |  |
| availability | yes | 0.56 | www.realestate.co.nz | yes |  |

### 3. [Auckland, NZ] Hyperfibre 8000 — apartment (score 0.574; sources: www.realestate.co.nz)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 260 NZD | 0.72 | www.realestate.co.nz | yes |  |
| rent_period | month | 0.72 | www.realestate.co.nz | yes |  |
| unit_kind | apartment | 0.56 | www.realestate.co.nz | yes |  |
| availability | yes | 0.56 | www.realestate.co.nz | yes |  |

### 4. [Osaka, JP] お電話の方はこちら — 1DK 35.0m² 6F #601 (score 0.522; sources: www.ur-net.go.jp)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 39,700 JPY | 0.74 | www.ur-net.go.jp | yes |  |
| management_fee | 3,400 JPY | 0.68 | www.ur-net.go.jp | yes |  |
| availability | yes | 0.63 | www.ur-net.go.jp | yes |  |
| address | 堺市南区茶山台二丁3 | 0.72 | www.ur-net.go.jp | yes |  |
| coords | 34.49403, 135.516907 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 泉ケ丘 Nonemin, 栂・美木多 Nonemin, 狭山 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| rail_distance_m | 592 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 1099, '新宿': 1096, '東京': 1110, '池袋': 1102, '渋谷': 1093} | 0.27 | regent | yes |  |

### 5. [Osaka, JP] お電話の方はこちら — 1DK 35.0m² 9F #903 (score 0.485; sources: www.ur-net.go.jp)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 39,000 JPY | 0.74 | www.ur-net.go.jp | yes |  |
| management_fee | 3,400 JPY | 0.68 | www.ur-net.go.jp | yes |  |
| deposit | 78,000 JPY | 0.60 | www.ur-net.go.jp | yes |  |
| key_money | 0 JPY | 0.60 | www.ur-net.go.jp | yes |  |
| availability | yes | 0.63 | www.ur-net.go.jp | yes |  |
| address | 堺市南区茶山台二丁3 | 0.72 | www.ur-net.go.jp | yes |  |
| coords | 34.49403, 135.516907 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 泉ケ丘 Nonemin, 栂・美木多 Nonemin, 狭山 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| rail_distance_m | 592 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 1099, '新宿': 1096, '東京': 1110, '池袋': 1102, '渋谷': 1093} | 0.27 | regent | yes |  |

### 6. [Osaka, JP] Bella Casa — 1LDK 41.95m² 1F (score 0.474; sources: regent, suumo.jp, www.homes.co.jp)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 74,000 JPY | 0.98 | suumo.jp, www.homes.co.jp | yes |  |
| management_fee | 5,000 JPY | 0.97 | suumo.jp, www.homes.co.jp | yes |  |
| deposit | 0 JPY | 0.96 | suumo.jp, www.homes.co.jp | yes |  |
| key_money | 150,000 JPY | 0.96 | suumo.jp, www.homes.co.jp | yes |  |
| availability | yes | 0.90 | suumo.jp, www.homes.co.jp | yes |  |
| move_in | immediate | 0.98 | suumo.jp, www.homes.co.jp | yes |  |
| info_updated_at | 2026-09-29 | 0.92 | suumo.jp | yes |  |
| earliest_move_in_est | 2026-10-14 | 0.36 | regent | yes |  |
| address | 大阪府堺市南区土佐屋台 | 0.99 | suumo.jp, www.homes.co.jp | yes |  |
| stations | 泉ケ丘 15min, 深井 40min, 栂・美木多 50min | 0.98 | suumo.jp, www.homes.co.jp | yes |  |
| built_year | 2,009 | 0.91 | suumo.jp | yes |  |
| coords | 34.503811, 135.508942 | 0.57 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 泉ケ丘 Nonemin, 栂・美木多 Nonemin, 深井 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| rail_distance_m | 268 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 1100, '新宿': 1096, '東京': 1111, '池袋': 1103, '渋谷': 1094} | 0.27 | regent | yes |  |

### 7. [Berlin, DE] 5.10-12.10 Großes WG Zimmer Prenzlberg — room 27.0m² (score 0.545; sources: www.wg-gesucht.de)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 400 EUR | 0.64 | www.wg-gesucht.de | yes |  |
| rent_period | month (assumed) | 0.42 | www.wg-gesucht.de | yes |  |
| unit_kind | room | 0.59 | www.wg-gesucht.de | yes |  |
| availability | yes | 0.59 | www.wg-gesucht.de | yes |  |

### 8. [Berlin, DE] 5.10-12.10 Großes WG Zimmer Prenzlberg — room 27.0m² (score 0.545; sources: www.wg-gesucht.de)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 400 EUR | 0.64 | www.wg-gesucht.de | yes |  |
| rent_period | month (assumed) | 0.42 | www.wg-gesucht.de | yes |  |
| unit_kind | room | 0.59 | www.wg-gesucht.de | yes |  |
| availability | yes | 0.59 | www.wg-gesucht.de | yes |  |

### 9. [Berlin, DE] 5.10-12.10 Großes WG Zimmer Prenzlberg — room 27.0m² (score 0.545; sources: www.wg-gesucht.de)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 400 EUR | 0.64 | www.wg-gesucht.de | yes |  |
| rent_period | month (assumed) | 0.42 | www.wg-gesucht.de | yes |  |
| unit_kind | room | 0.59 | www.wg-gesucht.de | yes |  |
| availability | yes | 0.59 | www.wg-gesucht.de | yes |  |

### 10. [Madrid, ES] Information about our rentals in Madrid — apartment (score 0.535; sources: www.spotahome.com)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 249 EUR | 0.60 | www.spotahome.com | yes |  |
| rent_period | month (assumed) | 0.40 | www.spotahome.com | yes |  |
| unit_kind | apartment | 0.56 | www.spotahome.com | yes |  |
| availability | yes | 0.56 | www.spotahome.com | yes |  |

### 11. [Madrid, ES] Room in shared 5-bedroom apt, Entrevías, Madrid — apartment (score 0.500; sources: www.spotahome.com)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 450 EUR | 0.72 | www.spotahome.com | yes |  |
| rent_period | month | 0.72 | www.spotahome.com | yes |  |
| unit_kind | apartment | 0.56 | www.spotahome.com | yes |  |
| availability | yes | 0.56 | www.spotahome.com | yes |  |

### 12. [Madrid, ES] Room in shared flat for rent in Palacio, Madrid — room (score 0.494; sources: www.spotahome.com)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 475 EUR | 0.72 | www.spotahome.com | yes |  |
| rent_period | month | 0.72 | www.spotahome.com | yes |  |
| unit_kind | room | 0.56 | www.spotahome.com | yes |  |
| availability | yes | 0.56 | www.spotahome.com | yes |  |
| address | Room in shared flat for rent in Palacio, Madrid | 0.64 | www.spotahome.com | yes |  |

### 13. [Tokyo, JP] Beeα — 1K 23.6m² 1F (score 0.605; sources: regent, suumo.jp)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 68,000 JPY | 0.92 | suumo.jp | yes |  |
| management_fee | 2,000 JPY | 0.91 | suumo.jp | yes |  |
| deposit | 0 JPY | 0.88 | suumo.jp | yes |  |
| key_money | 0 JPY | 0.88 | suumo.jp | yes |  |
| availability | yes | 0.78 | suumo.jp | yes |  |
| move_in | 2026-11-05 | 0.88 | suumo.jp | yes |  |
| info_updated_at | 2026-09-29 | 0.92 | suumo.jp | yes |  |
| earliest_move_in_est | 2026-11-05 | 0.36 | regent | yes |  |
| address | 東京都江戸川区船堀2 | 0.92 | suumo.jp | yes |  |
| stations | 船堀 8min, 東大島 26min, 西葛西 30min | 0.88 | suumo.jp | yes |  |
| built_year | 2,008 | 0.91 | suumo.jp | yes |  |
| coords | 35.68375, 139.857239 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 船堀 Nonemin, 東大島 Nonemin, 西葛西 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| rail_distance_m | 187 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 49, '新宿': 54, '東京': 38, '池袋': 54, '渋谷': 55} | 0.27 | regent | yes |  |

### 14. [Tokyo, JP] 東京都江戸川区東葛西6丁目 — 1K 24.3m² 1F (score 0.586; sources: regent, www.homes.co.jp)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 78,000 JPY | 0.91 | www.homes.co.jp | yes |  |
| management_fee | 0 JPY | 0.84 | www.homes.co.jp | yes |  |
| deposit | 78,000 JPY | 0.84 | www.homes.co.jp | yes |  |
| key_money | 0 JPY | 0.84 | www.homes.co.jp | yes |  |
| availability | yes | 0.67 | www.homes.co.jp | yes |  |
| move_in | immediate | 0.81 | www.homes.co.jp | yes |  |
| earliest_move_in_est | 2026-10-14 | 0.36 | regent | yes |  |
| address | 東京都江戸川区東葛西6丁目 | 0.86 | www.homes.co.jp | yes |  |
| stations | 葛西 3min, 西葛西 22min, 浦安 27min | 0.86 | www.homes.co.jp | yes |  |
| built_year | 1,998 | 0.81 | www.homes.co.jp | yes |  |
| coords | 35.661366, 139.877365 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 葛西 Nonemin, 浦安 Nonemin, 西葛西 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| rail_distance_m | 221 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 51, '新宿': 60, '東京': 43, '池袋': 61, '渋谷': 59} | 0.27 | regent | yes |  |

### 15. [Tokyo, JP] ハーモニーテラス北小岩XIV — 1K 16.63m² 1F (score 0.580; sources: suumo.jp)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 67,000 JPY | 0.92 | suumo.jp | yes |  |
| management_fee | 4,000 JPY | 0.91 | suumo.jp | yes |  |
| deposit | 0 JPY | 0.88 | suumo.jp | yes |  |
| key_money | 0 JPY | 0.88 | suumo.jp | yes |  |
| availability | yes | 0.78 | suumo.jp | yes |  |
| move_in | negotiable | 0.88 | suumo.jp | yes |  |
| info_updated_at | 2026-09-29 | 0.92 | suumo.jp | yes |  |
| address | 東京都江戸川区北小岩6 | 0.92 | suumo.jp | yes |  |
| stations | 京成小岩 5min, 新柴又 14min, 江戸川 21min | 0.88 | suumo.jp | yes |  |
| built_year | 2,021 | 0.91 | suumo.jp | yes |  |
| coords | 35.74419, 139.885376 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 京成小岩 Nonemin, 新柴又 Nonemin, 江戸川 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| rail_distance_m | 269 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 66, '新宿': 64, '東京': 50, '池袋': 58, '渋谷': 68} | 0.27 | regent | yes |  |

## Strategies competing on this world

| rank | score | route | status |
|---|---|---|---|
| 1 | 2.500 | Room in a shared house (cheapest evidence: Tokyo, JP) | selected |
| 2 | 1.840 | Lease now in Osaka: お電話の方はこちら 1DK 35.0m² 6F #601 (+1 backups) | alive |
| 3 | 1.756 | Lease now in Tokyo: 東京都江戸川区東葛西6丁目 1K 24.3m² 1F | alive |
| 4 | 1.449 | Furnished mid-term rental while deciding (cheapest evidence: Tokyo, JP) | alive |
| 5 | 0.856 | Don't commit until work location and right to stay are known | alive |
| 6 | 0.309 | Stay in hostels/hotels day by day (live prices: Berlin, DE) | alive |
| 7 | 0.177 | Move to Berlin (DE) and lease: apartment (Berlin) | alive |
| 8 | 0.159 | Move to Madrid (ES) and lease: Pricing apartment | alive |

Selected: **Room in a shared house (cheapest evidence: Tokyo, JP)**

> Private room in a shared/managed house: low upfront cost, month-to-month. Live offers in Tokyo, JP: median 78,500 JPY/month.

- money_cost: first month + contract fee
- expected_upside: months of reasonably stable housing (shared living discounted)

Last decision (plan_kept): Ranking changed; incumbent still best

### Operations

| op | tool.action | status | note |
|---|---|---|---|
| acquire.discover.1 | acquire.discover | succeeded |  |
| lease.recheck | acquire.recheck | succeeded |  |
| lease.brief | fs.write | cancelled | route deselected: 'Room in a shared house (cheapest evidence: Tokyo, JP)' (2.313) now beats incumben |
| lease.contact | human.perform | cancelled | route deselected: 'Room in a shared house (cheapest evidence: Tokyo, JP)' (2.313) now beats incumben |
| lease.recheck | acquire.recheck | succeeded |  |
| acquire.enrich.2 | acquire.enrich | succeeded |  |
| acquire.enrich.3 | acquire.enrich | succeeded |  |
| acquire.enrich.4 | acquire.enrich | succeeded |  |
| acquire.enrich.5 | acquire.enrich | succeeded |  |
| acquire.enrich.6 | acquire.enrich | succeeded |  |
| acquire.enrich.7 | acquire.enrich | succeeded |  |
| acquire.enrich.8 | acquire.enrich | succeeded |  |
| acquire.enrich.9 | acquire.enrich | succeeded |  |
| acquire.recheck.9 | acquire.recheck | succeeded |  |
| acquire.enrich.10 | acquire.enrich | succeeded |  |
| acquire.recheck.10 | acquire.recheck | succeeded |  |
| acquire.enrich.11 | acquire.enrich | succeeded |  |
| acquire.recheck.11 | acquire.recheck | succeeded |  |
| acquire.enrich.12 | acquire.enrich | succeeded |  |

## Loop ticks

- tick 1 (+653s): status active; acquire ['acquire.discover.1: no live housing options are known anywhere: cannot compare strategies']; ran before planning ['op_a570bec9c316']; generated ['housing-lease-jp-osaka', 'housing-lease-jp-tokyo', 'housing-lease-de-berlin', 'housing-monthly', 'housing-share', 'housing-hostel', 'housing-defer']; selected housing-lease-jp-osaka (route_selected)
- tick 2 (+741s): status monitoring; acquire ['acquire.enrich.2: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-share (plan_changed)
- tick 3 (+787s): status monitoring; acquire ['acquire.enrich.3: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-share (plan_kept)
- tick 4 (+793s): status monitoring; acquire ['acquire.enrich.4: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-share (plan_kept)
- tick 5 (+797s): status monitoring; acquire ['acquire.enrich.5: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated ['housing-lease-es-madrid']; selected housing-share (plan_kept)
- tick 6 (+802s): status monitoring; acquire ['acquire.enrich.6: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-share (plan_kept)
- tick 7 (+805s): status monitoring; acquire ['acquire.enrich.7: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-share (plan_kept)
- tick 8 (+807s): status monitoring; acquire ['acquire.enrich.8: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-share (plan_kept)
- tick 9 (+53s) [acquisition clock +7.0h, simulated]: status monitoring; acquire ['acquire.enrich.9: shortlisted candidates lack verification, location and move-in facts needed to evaluate them', 'acquire.recheck.9: time-sensitive claims (availability/rent) are past their TTL']; ran before planning []; generated []; selected housing-share (plan_kept)
- tick 10 (+78s) [acquisition clock +7.0h, simulated]: status monitoring; acquire ['acquire.enrich.10: shortlisted candidates lack verification, location and move-in facts needed to evaluate them', 'acquire.recheck.10: time-sensitive claims (availability/rent) are past their TTL']; ran before planning []; generated []; selected housing-share (plan_kept)
- tick 11 (+90s) [acquisition clock +7.0h, simulated]: status monitoring; acquire ['acquire.enrich.11: shortlisted candidates lack verification, location and move-in facts needed to evaluate them', 'acquire.recheck.11: time-sensitive claims (availability/rent) are past their TTL']; ran before planning []; generated []; selected housing-share (plan_kept)
- tick 12 (+93s) [acquisition clock +7.0h, simulated]: status monitoring; acquire ['acquire.enrich.12: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-share (plan_kept)
