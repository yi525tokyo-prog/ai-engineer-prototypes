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
| acq_1356bd33bb79 | discover | done | 201 | 927 | 7695 |
| acq_41d8c47b4316 | recheck | done | 1 | 1 | 24 |
| acq_60d016526405 | recheck | done | 2 | 2 | 19 |
| acq_3424b91bf0b6 | enrich | done | 11 | 85 | 672 |
| acq_f9d69e15f731 | enrich | done | 5 | 40 | 263 |
| acq_0b38d484ab65 | enrich | done | 2 | 25 | 167 |
| acq_cb6c7d6e897f | enrich | done | 2 | 14 | 98 |
| acq_1bd9900ffffd | enrich | done | 1 | 8 | 54 |
| acq_f1664f15c2bd | enrich | done | 0 | 0 | 0 |
| acq_383360a1d834 | recheck | done | 16 | 144 | 1094 |
| acq_720624cf0448 | enrich | done | 1 | 0 | 0 |
| acq_bd365ec29c2a | recheck | done | 1 | 0 | 0 |
| acq_eb7408863415 | enrich | done | 0 | 0 | 0 |

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
| Osaka (JP) — home evidence | 51,000 JPY | 0.6876 | 0.77 | 397 km | 132 |
| Tokyo (JP) — home evidence | 73,000 JPY | 0.6587 | 0.77 | 0 km | 92 |
| Berlin (DE) | 140,943 JPY | 0.5943 | 0.3 | 8915 km | 182 |
| Madrid (ES) | 166,367 JPY | 0.4869 | 0.3 | 10762 km | 166 |
| Auckland (NZ) | 273,400 JPY | 0.1213 | 0.3 | 8841 km | 20 |

Considered and not acquired:

- Lisbon (PT): two regions in Europe already rank higher (diversity), 165,029 JPY
- Brussels (BE): two regions in Europe already rank higher (diversity), 156,108 JPY
- Rome (IT): two regions in Europe already rank higher (diversity), 205,171 JPY
- Washington (US): no live price signal acquired

### Per region: sources and discovery

**Osaka (JP)** — 132 units; areas: 堺市南区

**Tokyo (JP)** — 92 units; areas: 江戸川区

**Berlin (DE)** — 182 units
- source www.wg-gesucht.de (pack:DE): 59 records
- source www.kleinanzeigen.de (pack:DE): 27 records
- source www.immowelt.de (None): 0 records [blocked]
- source housinganywhere.com (global): 10 records
- source www.spotahome.com (global): 97 records
- source www.craigslist.org (None): 0 records [no_listings]
- source www.hostelworld.com (None): 0 records [no_listings]

**Madrid (ES)** — 166 units
- discovery via probe: www.pisos.es → blocked (disallowed by robots.txt)
- discovery via probe: www.pisos.com → rejected (no Madrid listings reachable by navigation (0 records))
- discovery via probe: www.fotocasa.es → verified, 30 records
- discovery via probe: alquiler.com → rejected (no Madrid listings reachable by navigation (0 records))
- discovery via probe: www.inmobiliaria.es → blocked (disallowed by robots.txt)
- discovery via probe: www.inmobiliaria.com → blocked (HTTP 403)
- discovery via probe: www.casas.es → rejected (no Madrid listings reachable by navigation (0 records))
- discovery via probe: www.casas.com → blocked (disallowed by robots.txt)
- source www.fotocasa.es (discovered:probe): 61 records
- source www.spotahome.com (global): 95 records
- source housinganywhere.com (global): 10 records
- source www.craigslist.org (None): 0 records [no_listings]
- source www.hostelworld.com (None): 0 records [no_listings]

**Auckland (NZ)** — 20 units
- discovery via snowball: agent.realestate.co.nz → rejected (no Auckland listings reachable by navigation (0 records))
- discovery via probe: www.rent.co.nz → blocked (disallowed by robots.txt)
- discovery via probe: www.hirepool.co.nz → rejected (no Auckland listings reachable by navigation (0 records))
- discovery via probe: www.lettings.co.nz → blocked (disallowed by robots.txt)
- discovery via probe: www.flats.co.nz → blocked (disallowed by robots.txt)
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

670 listing mentions → 592 units after identity resolution (358 buildings) → 285 pass → 50 filtered → 15 deep research

| region | units | pass | shortlisted | market median /month |
|---|---|---|---|---|
| Osaka, JP | 132 | 30 | 3 | 83,000 JPY |
| Tokyo, JP | 92 | 46 | 3 | 100,000 JPY |
| Berlin, DE | 182 | 93 | 3 | 1,168 EUR |
| Madrid, ES | 166 | 103 | 3 | 1,332 EUR |
| Auckland, NZ | 20 | 13 | 3 | 3,152 NZD |

Rejection reasons: N mN below N mN ×6; N min walk to station ×22; monthly N above regional market cap N ×191; layout NK larger than needed for household=N ×4; layout NDK larger than needed for household=N ×19; N bedrooms: larger than needed for household=N ×30; layout NLDK larger than needed for household=N ×94

## Sources

| host | kind | pages ok/fetched | records | blocked | robots-disallowed | status |
|---|---|---|---|---|---|---|
| geocoding-api.open-meteo.com | public_data | 28/28 | 0 | 0 | 0 | ok static |
| www.spotahome.com | portal | 25/25 | 458 | 0 | 0 | ok static |
| suumo.jp | portal | 22/22 | 313 | 0 | 0 | ok static |
| www.zillow.com | portal | 18/19 | 0 | 1 | 0 | ok static |
| www.pisos.com | portal | 17/17 | 0 | 0 | 0 | ok static |
| housinganywhere.com | portal | 16/16 | 20 | 0 | 0 | ok static |
| www.ur-net.go.jp | operator | 11/13 | 33 | 0 | 0 | ok browser |
| www.realestate.co.nz | portal | 13/13 | 44 | 0 | 0 | ok static |
| www.wg-gesucht.de | portal | 10/10 | 75 | 0 | 0 | ok static |
| www.craigslist.org | portal | 10/10 | 0 | 0 | 0 | ok static |
| www.homes.co.jp | portal | 7/9 | 63 | 2 | 0 | blocked: challenge |
| www.chintai.net | portal | 9/9 | 85 | 0 | 0 | ok static |
| www.oakhouse.jp | operator | 9/9 | 48 | 0 | 0 | ok browser |
| www.kleinanzeigen.de | portal | 8/9 | 27 | 1 | 0 | ok static |
| www.monthly-mansion.com | portal | 8/8 | 19 | 0 | 0 | ok static |
| msearch.gsi.go.jp | public_data | 7/7 | 0 | 0 | 0 | ok static |
| www.hirepool.co.nz | portal | 7/7 | 0 | 0 | 0 | ok static |
| www.hostelworld.com | portal | 6/6 | 0 | 0 | 0 | ok static |
| www.rent.com | portal | 6/6 | 0 | 0 | 0 | ok static |
| nlftp.mlit.go.jp | public_data | 5/5 | 0 | 0 | 0 | ok download |
| www.fotocasa.es | portal | 5/5 | 61 | 0 | 0 | ok static |
| alquiler.com | portal | 3/3 | 0 | 0 | 0 | ok static |
| homes.co.nz | portal | 2/2 | 0 | 0 | 0 | ok static |
| www.athome.co.jp | portal | 0/2 | 0 | 2 | 0 | blocked: access_denied |
| www.immowelt.de | portal | 0/2 | 0 | 2 | 0 | blocked: access_denied |
| www.casas.es | portal | 2/2 | 0 | 0 | 0 | ok static |
| agentpro.co.nz | portal | 1/1 | 0 | 0 | 0 | ok static |
| www.exteriores.gob.es | public_data | 0/1 | 0 | 0 | 0 | error 404  |
| www.rooms.co.nz | portal | 0/1 | 0 | 1 | 0 | blocked: captcha |
| www.homes.co.nz | portal | 1/1 | 0 | 0 | 0 | ok static |
| www.apartments.co.nz | portal | 0/1 | 0 | 1 | 0 | blocked: challenge |
| www.alquiler.es | portal | 1/1 | 0 | 0 | 0 | ok static |
| www.alquiler.com | portal | 1/1 | 0 | 0 | 0 | ok static |
| www.inmobiliaria.com | portal | 0/1 | 0 | 1 | 0 | blocked: access_denied |
| www.immigration.govt.nz | public_data | 1/1 | 0 | 0 | 0 | ok static |
| www.ecb.europa.eu | public_data | 1/1 | 0 | 0 | 0 | ok static |
| agent.realestate.co.nz | portal | 1/1 | 0 | 0 | 0 | ok static |
| www.rentals.co.nz | portal | 1/1 | 0 | 0 | 0 | ok static |
| www.inmobiliaria.es | portal | 0/0 | 0 | 0 | 1 | robots_disallow |
| www.lettings.co.nz | portal | 0/0 | 0 | 0 | 1 | robots_disallow |
| www.property.co.nz | portal | 0/0 | 0 | 0 | 1 | robots_disallow |
| identity.zillow.com | portal | 0/0 | 0 | 0 | 0 |  |
| im.forsale | portal | 0/0 | 0 | 0 | 0 |  |
| www.rent.co.nz | portal | 0/0 | 0 | 0 | 1 | robots_disallow |
| www.pisos.es | portal | 0/0 | 0 | 0 | 1 | robots_disallow |
| www.casas.com | portal | 0/0 | 0 | 0 | 1 | robots_disallow |
| www.flats.co.nz | portal | 0/0 | 0 | 0 | 1 | robots_disallow |

### Source registry (what Regent now knows how to use)

| scope | host | origin | status | records | note |
|---|---|---|---|---|---|
| * | www.hostelworld.com | global | candidate | 0 | Auckland: no listings reached (ok static) |
| * | www.craigslist.org | global | candidate | 0 | Auckland: no listings reached (ok static) |
| * | www.spotahome.com | global | verified | 192 | Auckland: no listings reached (ok static) |
| * | housinganywhere.com | global | verified | 20 | Auckland: no listings reached (ok static) |
| DE | www.immowelt.de | pack:DE | blocked | 0 | Berlin: no listings reached (blocked: access_denied) |
| DE | www.wg-gesucht.de | pack:DE | verified | 59 |  |
| DE | www.kleinanzeigen.de | pack:DE | verified | 27 | price signal: no Berlin listings reached |
| ES | www.casas.com | discovered:probe | blocked | 0 | disallowed by robots.txt |
| ES | www.inmobiliaria.es | discovered:probe | blocked | 0 | disallowed by robots.txt |
| ES | www.inmobiliaria.com | discovered:probe | blocked | 0 | HTTP 403 |
| ES | www.pisos.es | discovered:probe | blocked | 0 | disallowed by robots.txt |
| ES | www.pisos.com | discovered:probe | rejected | 0 | no Madrid listings reachable by navigation (0 records) |
| ES | alquiler.com | discovered:probe | rejected | 0 | no Madrid listings reachable by navigation (0 records) |
| ES | www.casas.es | discovered:probe | rejected | 0 | no Madrid listings reachable by navigation (0 records) |
| ES | www.fotocasa.es | discovered:probe | verified | 91 |  |
| NZ | www.apartments.co.nz | discovered:probe | blocked | 0 | HTTP 202 bot challenge |
| NZ | www.flats.co.nz | discovered:probe | blocked | 0 | disallowed by robots.txt |
| NZ | www.rooms.co.nz | discovered:probe | blocked | 0 | CAPTCHA detected (name="recaptcha) |
| NZ | www.property.co.nz | discovered:probe | blocked | 0 | disallowed by robots.txt |
| NZ | www.lettings.co.nz | discovered:probe | blocked | 0 | disallowed by robots.txt |
| NZ | www.rent.co.nz | discovered:probe | blocked | 0 | disallowed by robots.txt |
| NZ | www.hirepool.co.nz | discovered:probe | rejected | 0 | no Auckland listings reachable by navigation (0 records) |
| NZ | homes.co.nz | discovered:probe | rejected | 0 | no Auckland listings reachable by navigation (0 records) |
| NZ | agent.realestate.co.nz | discovered:snowball | rejected | 0 | no Auckland listings reachable by navigation (0 records) |
| NZ | www.realestate.co.nz | pack:NZ | verified | 20 |  |
| US | www.zillow.com | pack:US | candidate | 0 | price signal: no Washington listings reached |
| US | www.rent.com | pack:US | candidate | 0 | price signal: no Washington listings reached |

## World model

- documents: 296, claims: 10175, regions: 5
- identity decisions: {'pinned': 38, 'rejected': 1419, 'ambiguous': 211, 'merged': 545, 'new': 1304}
- units seen on ≥2 sites: 20
- open conflicts (kept, not overwritten): 54 {'stations': 6, 'deposit': 1, 'key_money': 2, 'rent': 5, 'management_fee': 1, 'rent_period': 5, 'address': 8, 'floors_total': 2, 'move_in': 1, 'title': 14, 'unit_kind': 3, 'name': 1, 'structure': 1, 'info_updated_at': 1, 'conditions': 1, 'lease_term': 1, 'internet': 1}
- enrichment jobs: {'recheck:done': 20, 'detail:failed': 1, 'geocode:done': 7, 'stations:done': 7, 'geocode:skipped': 8, 'rail_noise:done': 7, 'facilities:done': 7, 'centre:skipped': 8, 'hubs:done': 7, 'move_in:skipped': 33, 'detail:done': 21, 'operator_page:skipped': 3, 'move_in:done': 3}

## Deep-research shortlist (per region)

### 1. [Auckland, NZ] Hyperfibre 2000 — apartment (score 0.580; sources: www.realestate.co.nz)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 149 NZD | 0.72 | www.realestate.co.nz | yes |  |
| rent_period | month | 0.72 | www.realestate.co.nz | yes |  |
| unit_kind | apartment | 0.56 | www.realestate.co.nz | yes |  |
| availability | yes | 0.56 | www.realestate.co.nz | yes |  |

### 2. [Auckland, NZ] Hyperfibre 2000 — apartment (score 0.580; sources: www.realestate.co.nz)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 149 NZD | 0.72 | www.realestate.co.nz | yes |  |
| rent_period | month | 0.72 | www.realestate.co.nz | yes |  |
| unit_kind | apartment | 0.56 | www.realestate.co.nz | yes |  |
| availability | yes | 0.56 | www.realestate.co.nz | yes |  |

### 3. [Auckland, NZ] Hyperfibre 2000 — apartment (score 0.580; sources: www.realestate.co.nz)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 149 NZD | 0.72 | www.realestate.co.nz | yes |  |
| rent_period | month | 0.72 | www.realestate.co.nz | yes |  |
| unit_kind | apartment | 0.56 | www.realestate.co.nz | yes |  |
| availability | yes | 0.56 | www.realestate.co.nz | yes |  |

### 4. [Osaka, JP] 泉北茶山台二丁 お気に入り — 1DK 35.0m² 9F #908 (score 0.465; sources: www.ur-net.go.jp)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 39,000 JPY | 0.74 | www.ur-net.go.jp | yes |  |
| management_fee | 3,400 JPY | 0.68 | www.ur-net.go.jp | yes |  |
| availability | yes | 0.63 | www.ur-net.go.jp | yes |  |
| address | 大阪府堺市南区茶山台二丁3 | 0.43 | www.ur-net.go.jp | yes | 大阪府堺市南区茶山台二丁3 [www.ur-net.go.jp] vs 堺市南区茶山台二丁3 [www.ur-net.go.jp] |
| coords | 34.49403, 135.516907 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 泉ケ丘 Nonemin, 栂・美木多 Nonemin, 狭山 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| rail_distance_m | 592 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 1099, '新宿': 1096, '東京': 1110, '池袋': 1102, '渋谷': 1093} | 0.27 | regent | yes |  |

### 5. [Osaka, JP] 泉北茶山台二丁 お気に入り — 1DK 35.0m² 6F #601 (score 0.462; sources: www.ur-net.go.jp)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 39,700 JPY | 0.74 | www.ur-net.go.jp | yes |  |
| management_fee | 3,400 JPY | 0.68 | www.ur-net.go.jp | yes |  |
| availability | yes | 0.63 | www.ur-net.go.jp | yes |  |
| address | 大阪府堺市南区茶山台二丁3 | 0.43 | www.ur-net.go.jp | yes | 大阪府堺市南区茶山台二丁3 [www.ur-net.go.jp] vs 堺市南区茶山台二丁3 [www.ur-net.go.jp] |
| coords | 34.49403, 135.516907 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 泉ケ丘 Nonemin, 栂・美木多 Nonemin, 狭山 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| rail_distance_m | 592 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 1099, '新宿': 1096, '東京': 1110, '池袋': 1102, '渋谷': 1093} | 0.27 | regent | yes |  |

### 6. [Osaka, JP] 泉北原山台一丁 お気に入り — 1LDK 43.0m² 8F #804 (score 0.453; sources: www.ur-net.go.jp)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 60,400 JPY | 0.74 | www.ur-net.go.jp | yes |  |
| management_fee | 3,600 JPY | 0.68 | www.ur-net.go.jp | yes |  |
| availability | yes | 0.63 | www.ur-net.go.jp | yes |  |
| address | 大阪府堺市南区原山台一丁5 | 0.81 | www.ur-net.go.jp | yes |  |
| coords | 34.485184, 135.492416 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 栂・美木多 Nonemin, 光明池 Nonemin, 泉ケ丘 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| rail_distance_m | 119 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 1106, '新宿': 1102, '東京': 1117, '池袋': 1108, '渋谷': 1099} | 0.27 | regent | yes |  |

### 7. [Tokyo, JP] エール小岩 『小岩駅』徒歩7分『京成小岩駅』徒歩7分<インターネット無料>の物件詳細 — 1K 24.0m² (score 0.565; sources: www.monthly-mansion.com)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 48,600 JPY | 0.76 | www.monthly-mansion.com | yes |  |
| availability | yes | 0.64 | www.monthly-mansion.com | yes |  |
| move_in | 2名 | 0.72 | www.monthly-mansion.com | yes |  |
| address | 東京都江戸川区北小岩2丁目1-8 | 0.76 | www.monthly-mansion.com | yes |  |
| stations | 小岩 7min, 京成小岩 7min, 江戸川 14min | 0.72 | www.monthly-mansion.com | yes |  |
| built_year | 2,000 | 0.68 | www.monthly-mansion.com | yes |  |
| coords | 35.737148, 139.884674 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 小岩 Nonemin, 京成小岩 Nonemin, 江戸川 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| rail_distance_m | 212 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 64, '新宿': 63, '東京': 49, '池袋': 58, '渋谷': 66} | 0.27 | regent | yes |  |

### 8. [Tokyo, JP] Beeα — 1K 23.6m² 1F (score 0.563; sources: regent, suumo.jp)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 68,000 JPY | 0.93 | suumo.jp | yes |  |
| management_fee | 2,000 JPY | 0.91 | suumo.jp | yes |  |
| deposit | 0 JPY | 0.88 | suumo.jp | yes |  |
| key_money | 0 JPY | 0.88 | suumo.jp | yes |  |
| availability | yes | 0.78 | suumo.jp | yes |  |
| move_in | 2026-11-05 | 0.88 | suumo.jp | yes |  |
| info_updated_at | 2026-09-29 | 0.93 | suumo.jp | yes |  |
| earliest_move_in_est | 2026-11-05 | 0.36 | regent | yes |  |
| address | 東京都江戸川区船堀2 | 0.93 | suumo.jp | yes |  |
| stations | 船堀 8min, 東大島 26min, 西葛西 30min | 0.88 | suumo.jp | yes |  |
| built_year | 2,008 | 0.91 | suumo.jp | yes |  |
| coords | 35.68375, 139.857239 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 船堀 Nonemin, 東大島 Nonemin, 西葛西 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| rail_distance_m | 187 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 49, '新宿': 54, '東京': 38, '池袋': 54, '渋谷': 55} | 0.27 | regent | yes |  |

### 9. [Tokyo, JP] ハーモニーテラス北小岩XIV — 1K 16.63m² 1F (score 0.538; sources: suumo.jp)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 67,000 JPY | 0.93 | suumo.jp | yes |  |
| management_fee | 4,000 JPY | 0.91 | suumo.jp | yes |  |
| deposit | 0 JPY | 0.88 | suumo.jp | yes |  |
| key_money | 0 JPY | 0.88 | suumo.jp | yes |  |
| availability | yes | 0.78 | suumo.jp | yes |  |
| move_in | negotiable | 0.88 | suumo.jp | yes |  |
| info_updated_at | 2026-09-29 | 0.93 | suumo.jp | yes |  |
| address | 東京都江戸川区北小岩6 | 0.93 | suumo.jp | yes |  |
| stations | 京成小岩 5min, 新柴又 14min, 江戸川 21min | 0.88 | suumo.jp | yes |  |
| built_year | 2,021 | 0.91 | suumo.jp | yes |  |

### 10. [Madrid, ES] Information about our rentals in Madrid — apartment (score 0.537; sources: www.spotahome.com)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 249 EUR | 0.60 | www.spotahome.com | yes |  |
| rent_period | month (assumed) | 0.40 | www.spotahome.com | yes |  |
| unit_kind | apartment | 0.56 | www.spotahome.com | yes |  |
| availability | yes | 0.56 | www.spotahome.com | yes |  |

### 11. [Madrid, ES] Information about our rentals in Madrid — apartment (score 0.537; sources: www.spotahome.com)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 249 EUR | 0.60 | www.spotahome.com | yes |  |
| rent_period | month (assumed) | 0.40 | www.spotahome.com | yes |  |
| unit_kind | apartment | 0.56 | www.spotahome.com | yes |  |
| availability | yes | 0.56 | www.spotahome.com | yes |  |

### 12. [Madrid, ES] Furnished room in shared apartment in Puerta del Sol, Madrid — room 260.0m² (score 0.500; sources: www.spotahome.com)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 750 EUR | 0.72 | www.spotahome.com | yes |  |
| rent_period | month | 0.72 | www.spotahome.com | yes |  |
| unit_kind | room | 0.56 | www.spotahome.com | yes |  |
| availability | yes | 0.56 | www.spotahome.com | yes |  |
| address | Furnished room in shared apartment in Puerta del Sol, Madrid | 0.64 | www.spotahome.com | yes |  |

### 13. [Berlin, DE] apartment — apartment (score 0.535; sources: www.spotahome.com)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 250 EUR | 0.60 | www.spotahome.com | yes |  |
| rent_period | month (assumed) | 0.40 | www.spotahome.com | yes |  |
| unit_kind | apartment | 0.56 | www.spotahome.com | yes |  |
| availability | yes | 0.56 | www.spotahome.com | yes |  |

### 14. [Berlin, DE] apartment — apartment (score 0.535; sources: www.spotahome.com)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 250 EUR | 0.60 | www.spotahome.com | yes |  |
| rent_period | month (assumed) | 0.40 | www.spotahome.com | yes |  |
| unit_kind | apartment | 0.56 | www.spotahome.com | yes |  |
| availability | yes | 0.56 | www.spotahome.com | yes |  |

### 15. [Berlin, DE] ROOM IN MITTE /Women — apartment 41.0m² (score 0.527; sources: www.wg-gesucht.de)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | 600 EUR | 0.67 | www.wg-gesucht.de | yes |  |
| rent_period | month (assumed) | 0.45 | www.wg-gesucht.de | yes |  |
| unit_kind | apartment | 0.63 | www.wg-gesucht.de | yes |  |
| availability | yes | 0.63 | www.wg-gesucht.de | yes |  |

## Strategies competing on this world

| rank | score | route | status |
|---|---|---|---|
| 1 | 3.262 | Lease now in Osaka: 泉北茶山台二丁 お気に入り 1DK 35.0m² 9F #903 (+2 backups) | selected |
| 2 | 2.381 | Room in a shared house (cheapest evidence: Tokyo, JP) | alive |
| 3 | 2.032 | Lease now in Tokyo: ハーモニーテラス北小岩XIV 1K 16.63m² 1F | alive |
| 4 | 1.231 | Furnished mid-term rental while deciding (cheapest evidence: Tokyo, JP) | alive |
| 5 | 0.856 | Don't commit until work location and right to stay are known | alive |
| 6 | 0.687 | Stay in hostels/hotels day by day | alive |
| 7 | 0.298 | Move to Berlin (DE) and lease: apartment (Berlin) (+1 backups) | alive |
| 8 | 0.025 | Move to Auckland (NZ) and lease: Hyperfibre 2000 apartment | alive |
| 9 | -0.180 | Move to Madrid (ES) and lease: apartment (Madrid) | alive |

Selected: **Lease now in Osaka: 泉北茶山台二丁 お気に入り 1DK 35.0m² 9F #903 (+2 backups)**

> Sign a standard lease in Osaka. Best evidenced candidate: 泉北茶山台二丁 お気に入り 1DK 35.0m² 9F #903 at 42,400 JPY/month. Median of top 3: 42,800 JPY/month, ~81,400 JPY upfront.

- money_cost: median of deposit (1 month if not stated) + key money + first month, in JPY
- expected_upside: 24 months of stable housing, discounted by wrong-place risk -- work location unknown and no mobility evidence yet (prior 50 %)
- success_probability: availability over top candidates (0.91) x right to stay (0.77)

Last decision (plan_kept): Ranking changed; incumbent still best

### Operations

| op | tool.action | status | note |
|---|---|---|---|
| acquire.discover.1 | acquire.discover | succeeded |  |
| lease.recheck | acquire.recheck | succeeded |  |
| lease.brief | fs.write | succeeded |  |
| lease.contact | human.perform | waiting_human |  |
| lease.recheck | acquire.recheck | succeeded |  |
| acquire.enrich.2 | acquire.enrich | succeeded |  |
| acquire.enrich.3 | acquire.enrich | succeeded |  |
| acquire.enrich.4 | acquire.enrich | succeeded |  |
| acquire.enrich.5 | acquire.enrich | succeeded |  |
| acquire.enrich.6 | acquire.enrich | succeeded |  |
| acquire.enrich.7 | acquire.enrich | succeeded |  |
| acquire.recheck.8 | acquire.recheck | succeeded |  |
| acquire.enrich.9 | acquire.enrich | succeeded |  |
| acquire.enrich.10 | acquire.enrich | succeeded |  |
| acquire.recheck.10 | acquire.recheck | succeeded |  |

## Loop ticks

- tick 1 (+665s): status active; acquire ['acquire.discover.1: no live housing options are known anywhere: cannot compare strategies']; ran before planning ['op_ef789aba7b0a']; generated ['housing-lease-jp-tokyo', 'housing-lease-jp-osaka', 'housing-lease-de-berlin', 'housing-lease-nz-auckland', 'housing-monthly', 'housing-share', 'housing-hostel', 'housing-defer']; selected housing-lease-jp-osaka (route_selected)
- tick 2 (+724s): status active; acquire ['acquire.enrich.2: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-lease-jp-osaka (plan_kept)
- tick 3 (+765s): status waiting_human; acquire ['acquire.enrich.3: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated ['housing-lease-es-madrid']; selected housing-lease-jp-osaka (plan_kept)
- tick 4 (+770s): status waiting_human; acquire ['acquire.enrich.4: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-lease-jp-osaka (plan_kept)
- tick 5 (+775s): status waiting_human; acquire ['acquire.enrich.5: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-lease-jp-osaka (plan_kept)
- tick 6 (+779s): status waiting_human; acquire ['acquire.enrich.6: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-lease-jp-osaka (plan_kept)
- tick 7 (+781s): status waiting_human; acquire ['acquire.enrich.7: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-lease-jp-osaka (plan_kept)
- tick 8 (+781s): status waiting_human; acquire []; ran before planning []; generated []; selected None (None)
- tick 8 (+54s) [acquisition clock +7.0h, simulated]: status waiting_human; acquire ['acquire.recheck.8: time-sensitive claims (availability/rent) are past their TTL']; ran before planning []; generated []; selected housing-lease-jp-osaka (plan_kept)
- tick 9 (+80s) [acquisition clock +7.0h, simulated]: status waiting_human; acquire ['acquire.enrich.9: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-lease-jp-osaka (plan_kept)
- tick 10 (+105s) [acquisition clock +7.0h, simulated]: status waiting_human; acquire ['acquire.enrich.10: shortlisted candidates lack verification, location and move-in facts needed to evaluate them', 'acquire.recheck.10: time-sensitive claims (availability/rent) are past their TTL']; ran before planning []; generated []; selected housing-lease-jp-osaka (plan_kept)
- tick 11 (+105s) [acquisition clock +7.0h, simulated]: status waiting_human; acquire []; ran before planning []; generated []; selected None (None)
