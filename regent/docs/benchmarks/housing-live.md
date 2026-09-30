# Housing benchmark — 「住居を安定させたい」

A fresh world received only the sentence above: no tags, no candidates, no areas, no budget.
Everything below was acquired by Regent from the public web during the run.

## Acquisition requests (created by the loop's ACQUIRE phase)

| request | action | status | pages | mentions | claims |
|---|---|---|---|---|---|
| acq_93e9bb42b5b9 | discover | done | 96 | 1148 | 13412 |
| acq_39da8d0ba268 | recheck | done | 2 | 2 | 34 |
| acq_09361e8cef73 | enrich | done | 8 | 38 | 466 |
| acq_71c6b4be0308 | enrich | done | 3 | 4 | 71 |
| acq_567ca2088811 | recheck | done | 9 | 11 | 144 |

### Plan and assumptions

- areas: 江戸川区, 葛飾区, 世田谷区, 中野区
- why: wards (dense rail network) preferred; 1K/1DK market rents from live data; two most affordable (江戸川区 73,000, 葛飾区 75,000) plus areas nearest the median (89,000); budget unknown
- assumption: region = 東京都 (principal.region unknown: using locale default)
- assumption: household = 1 (household size unknown: assuming one person)
- assumption: max_rent = None (budget unknown: bounded by live market rents per area)
- assumption: work_location = None (work location unknown: commute measured to major hubs)

### Funnel

1064 listing mentions → 954 units after identity resolution (615 buildings) → 275 pass basic filters → 25 filtered → 8 deep research

Rejection reasons: N mN below N mN ×9; N min walk to station ×8; monthly N above market-based cap N ×636; layout NK larger than needed for household=N ×9; layout NDK larger than needed for household=N ×53; layout NSK larger than needed for household=N ×2; layout NLDK larger than needed for household=N ×247; layout NSLDK larger than needed for household=N ×25

## Sources

| host | kind | pages ok/fetched | records | blocked | robots-disallowed | status |
|---|---|---|---|---|---|---|
| www.homes.co.jp | portal | 22/26 | 369 | 4 | 0 | blocked: challenge |
| suumo.jp | portal | 21/21 | 507 | 0 | 0 | ok static |
| www.chintai.net | portal | 20/20 | 161 | 0 | 0 | ok static |
| www.monthly-mansion.com | portal | 18/18 | 40 | 0 | 0 | ok browser |
| www.oakhouse.jp | operator | 17/17 | 119 | 0 | 0 | ok static |
| www.ur-net.go.jp | operator | 12/12 | 7 | 0 | 0 | ok browser |
| msearch.gsi.go.jp | public_data | 9/9 | 0 | 0 | 0 | ok static |
| www.athome.co.jp | portal | 0/4 | 0 | 4 | 0 | blocked: access_denied |
| nlftp.mlit.go.jp | public_data | 3/3 | 0 | 0 | 0 | ok download |

## World model

- documents: 141, claims: 14198
- identity decisions: {'pinned': 24, 'rejected': 2224, 'ambiguous': 448, 'merged': 629, 'new': 1669}
- units seen on ≥2 sites: 17 ([(('suumo.jp', 'www.chintai.net'), 9), (('regent', 'www.monthly-mansion.com'), 3), (('regent', 'suumo.jp', 'www.chintai.net'), 2), (('regent', 'www.homes.co.jp'), 2)])
- open conflicts (kept, not overwritten): 17 {'management_fee': 1, 'stations': 3, 'name': 2, 'deposit': 1, 'key_money': 1, 'rent': 2, 'area_m2': 3, 'building_type': 2, 'move_in': 2}
- enrichment jobs: {'recheck:done': 9, 'detail:done': 11, 'geocode:done': 9, 'rail_noise:done': 9, 'stations:done': 9, 'facilities:done': 9, 'hubs:done': 9, 'move_in:done': 8, 'move_in:skipped': 3, 'operator_page:skipped': 2, 'recheck:blocked': 2}

## Deep-research shortlist

### 1. 東京都世田谷区奥沢5丁目9-6 — 1K 22.0m² (score 0.692; sources: regent, www.monthly-mansion.com)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | ¥10,600 | 0.66 | www.monthly-mansion.com | yes |  |
| availability | yes | 0.56 | www.monthly-mansion.com | yes |  |
| move_in | 1999-03-01 | 0.68 | www.monthly-mansion.com | yes |  |
| earliest_move_in_est | 2026-10-13 | 0.36 | regent | yes |  |
| address | 東京都世田谷区奥沢5丁目9-6 | 0.72 | www.monthly-mansion.com | yes |  |
| stations | 奥沢 5min, 九品仏 9min | 0.72 | www.monthly-mansion.com | yes |  |
| built_year | ¥1,999 | 0.68 | www.monthly-mansion.com | yes |  |
| coords | 35.603703, 139.668182 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 奥沢 Nonemin, 自由が丘 Nonemin, 九品仏 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| walk_check | 奥沢 Nonemin, 九品仏 Nonemin | 0.42 | regent | yes |  |
| rail_distance_m | 41 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| universities_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 34, '新宿': 43, '東京': 49, '池袋': 54, '渋谷': 34} | 0.27 | regent | yes |  |

### 2. 東京都世田谷区祖師谷1丁目3-8 — 1K 25.0m² (score 0.549; sources: regent, www.monthly-mansion.com)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | ¥60,000 | 0.76 | www.monthly-mansion.com | yes |  |
| availability | yes | 0.56 | www.monthly-mansion.com | yes |  |
| move_in | 1984-11-01 | 0.68 | www.monthly-mansion.com | yes |  |
| earliest_move_in_est | 2026-10-13 | 0.36 | regent | yes |  |
| address | 東京都世田谷区祖師谷1丁目3-8 | 0.72 | www.monthly-mansion.com | yes |  |
| stations | 祖師ケ谷大蔵 4min, 千歳船橋 16min | 0.72 | www.monthly-mansion.com | yes |  |
| built_year | ¥1,984 | 0.68 | www.monthly-mansion.com | yes |  |
| coords | 35.644432, 139.611435 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 祖師ヶ谷大蔵 Nonemin, 成城学園前 Nonemin, 千歳船橋 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| walk_check | 千歳船橋 Nonemin | 0.42 | regent | yes |  |
| rail_distance_m | 71 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| universities_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 47, '新宿': 41, '東京': 55, '池袋': 51, '渋谷': 38} | 0.27 | regent | yes |  |

### 3. 基本情報 — 1R 13.91m² 1F #102 (score 0.544; sources: regent, www.homes.co.jp)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | ¥62,000 | 0.76 | www.homes.co.jp | yes |  |
| management_fee | ¥3,000 | 0.70 | www.homes.co.jp | yes |  |
| deposit | 0 | 0.70 | www.homes.co.jp | yes |  |
| key_money | 0 | 0.70 | www.homes.co.jp | yes |  |
| availability | yes | 0.56 | www.homes.co.jp | yes |  |
| move_in | 2019-01-01 | 0.68 | www.homes.co.jp | yes |  |
| earliest_move_in_est | 2026-10-13 | 0.36 | regent | yes |  |
| address | 東京都中野区鷺宮3丁目15-15 | 0.72 | www.homes.co.jp | yes |  |
| stations | 鷺ノ宮 2min, 都立家政 8min, 野方 18min | 0.72 | www.homes.co.jp | yes |  |
| built_year | ¥2,018 | 0.68 | www.homes.co.jp | yes |  |
| coords | 35.722832, 139.639771 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 鷺ノ宮 Nonemin, 都立家政 Nonemin, 野方 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| walk_check | 鷺ノ宮 Nonemin, 都立家政 Nonemin, 野方 Nonemin | 0.42 | regent | yes |  |
| rail_distance_m | 23 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| universities_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 53, '新宿': 33, '東京': 49, '池袋': 33, '渋谷': 40} | 0.27 | regent | yes |  |

### 4. 古谷マンション — 1DK 24.0m² 1F (score 0.533; sources: regent, www.chintai.net)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | ¥66,000 | 0.91 | www.chintai.net | yes |  |
| management_fee | ¥3,000 | 0.89 | www.chintai.net | yes |  |
| deposit | ¥66,000 | 0.86 | www.chintai.net | yes |  |
| key_money | 0 | 0.86 | www.chintai.net | yes |  |
| availability | yes | 0.77 | www.chintai.net | yes |  |
| move_in | immediate | 0.86 | www.chintai.net | yes |  |
| info_updated_at | 2026-09-29 | 0.91 | www.chintai.net | yes |  |
| earliest_move_in_est | 2026-10-13 | 0.36 | regent | yes |  |
| address | 東京都世田谷区世田谷3丁目 | 0.91 | www.chintai.net | yes |  |
| stations | 世田谷 3min, 上町 5min, 松陰神社前 7min | 0.86 | www.chintai.net | yes |  |
| built_year | ¥1,987 | 0.82 | www.chintai.net | yes |  |
| structure | 鉄筋コンクリート造 | 0.86 | www.chintai.net | yes |  |
| coords | 35.645332, 139.649536 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 世田谷 Nonemin, 上町 Nonemin, 宮の坂 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| walk_check | 世田谷 Nonemin, 上町 Nonemin | 0.42 | regent | yes |  |
| rail_distance_m | 215 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| universities_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 38, '新宿': 34, '東京': 46, '池袋': 45, '渋谷': 23} | 0.27 | regent | yes |  |

### 5. 東京都中野区新井5丁目20-2 — 1K 17.0m² (score 0.521; sources: regent, www.monthly-mansion.com)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | ¥59,000 | 0.76 | www.monthly-mansion.com | yes |  |
| availability | yes | 0.56 | www.monthly-mansion.com | yes |  |
| move_in | 1987-05-01 | 0.68 | www.monthly-mansion.com | yes |  |
| earliest_move_in_est | 2026-10-13 | 0.36 | regent | yes |  |
| address | 東京都中野区新井5丁目20-2 | 0.72 | www.monthly-mansion.com | yes |  |
| stations | 新井薬師前 4min | 0.72 | www.monthly-mansion.com | yes |  |
| built_year | ¥1,987 | 0.68 | www.monthly-mansion.com | yes |  |
| coords | 35.715893, 139.668594 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 新井薬師前 Nonemin, 沼袋 Nonemin, 中野 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| walk_check | 新井薬師前 Nonemin | 0.42 | regent | yes |  |
| rail_distance_m | 175 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| universities_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 47, '新宿': 21, '東京': 41, '池袋': 21, '渋谷': 35} | 0.27 | regent | yes |  |

### 6. グリーンハイム — 1K 23.0m² 1F #103 (score 0.512; sources: regent, www.homes.co.jp)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | ¥72,000 | 0.76 | www.homes.co.jp | yes |  |
| management_fee | 0 | 0.74 | www.homes.co.jp | yes |  |
| deposit | ¥72,000 | 0.72 | www.homes.co.jp | yes |  |
| availability | yes | 0.64 | www.homes.co.jp | yes |  |
| move_in | immediate | 0.72 | www.homes.co.jp | yes |  |
| info_updated_at | 2026-09-27 | 0.76 | www.homes.co.jp | yes |  |
| earliest_move_in_est | 2026-10-13 | 0.36 | regent | yes |  |
| address | 東京都中野区江原町 | 0.76 | www.homes.co.jp | yes |  |
| stations | 新江古田 1min, 江古田 8min, 新桜台 16min | 0.72 | www.homes.co.jp | yes |  |
| built_year | ¥1,977 | 0.74 | www.homes.co.jp | yes |  |
| structure | (新耐震基準適合)、駐輪場あり | 0.72 | www.homes.co.jp | yes |  |
| coords | 35.72908, 139.674194 | 0.57 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 新江古田 Nonemin, 東長崎 Nonemin, 江古田 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| walk_check | 新江古田 Nonemin, 江古田 Nonemin | 0.42 | regent | yes |  |
| rail_distance_m | 190 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| universities_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 50, '新宿': 23, '東京': 42, '池袋': 19, '渋谷': 38} | 0.27 | regent | yes |  |

### 7. 基本情報 — 1R 14.66m² 3F #303 (score 0.498; sources: www.homes.co.jp)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | ¥67,000 | 0.76 | www.homes.co.jp | yes |  |
| management_fee | ¥3,000 | 0.70 | www.homes.co.jp | yes |  |
| deposit | 0 | 0.70 | www.homes.co.jp | yes |  |
| key_money | 0 | 0.70 | www.homes.co.jp | yes |  |
| availability | yes | 0.56 | www.homes.co.jp | yes |  |
| move_in | immediate | 0.68 | www.homes.co.jp | yes |  |
| address | 東京都中野区鷺宮3丁目15-15 | 0.72 | www.homes.co.jp | yes |  |
| stations | 鷺ノ宮 2min, 都立家政 8min, 野方 18min | 0.72 | www.homes.co.jp | yes |  |
| built_year | ¥2,018 | 0.68 | www.homes.co.jp | yes |  |
| coords | 35.722832, 139.639771 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 鷺ノ宮 Nonemin, 都立家政 Nonemin, 野方 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| walk_check | 鷺ノ宮 Nonemin, 都立家政 Nonemin, 野方 Nonemin | 0.42 | regent | yes |  |
| rail_distance_m | 23 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| universities_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 53, '新宿': 33, '東京': 49, '池袋': 33, '渋谷': 40} | 0.27 | regent | yes |  |

### 8. クレシア青戸 — 1K 21.25m² 3F (score 0.485; sources: regent, suumo.jp, www.chintai.net)

| attribute | belief | confidence | sources | fresh | conflict |
|---|---|---|---|---|---|
| rent | ¥73,000 | 0.99 | suumo.jp, www.chintai.net | yes |  |
| management_fee | ¥5,000 | 0.98 | suumo.jp, www.chintai.net | yes |  |
| deposit | 0 | 0.98 | suumo.jp, www.chintai.net | yes |  |
| key_money | 0 | 0.98 | suumo.jp, www.chintai.net | yes |  |
| availability | yes | 0.92 | suumo.jp, www.chintai.net | yes |  |
| move_in | 2026-11-15 | 0.43 | suumo.jp, www.chintai.net | yes | 2026-11-15 [suumo.jp] vs 2020-02-01 [www.chintai.net] |
| info_updated_at | 2026-09-29 | 0.89 | suumo.jp | yes |  |
| earliest_move_in_est | 2026-11-15 | 0.36 | regent | yes |  |
| address | 東京都葛飾区青戸6 | 0.98 | suumo.jp, www.chintai.net | yes |  |
| stations | 青砥 9min, 京成高砂 15min, 柴又 28min | 0.98 | suumo.jp, www.chintai.net | yes |  |
| built_year | ¥2,020 | 0.98 | suumo.jp, www.chintai.net | yes |  |
| structure | 木造 | 0.84 | suumo.jp | yes |  |
| coords | 35.750206, 139.858231 | 0.81 | msearch.gsi.go.jp | yes |  |
| nearest_stations_public | 青砥 Nonemin, 京成高砂 Nonemin, 京成立石 Nonemin | 0.76 | nlftp.mlit.go.jp | yes |  |
| walk_check | 青砥 Nonemin, 京成高砂 Nonemin, 京成立石 Nonemin | 0.42 | regent | yes |  |
| rail_distance_m | 202 | 0.67 | nlftp.mlit.go.jp | yes |  |
| libraries_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| universities_nearby |  | 0.76 | nlftp.mlit.go.jp | yes |  |
| hub_minutes_est | {'品川': 63, '新宿': 58, '東京': 46, '池袋': 52, '渋谷': 63} | 0.27 | regent | yes |  |

## Strategies (routes) competing on this world

| rank | score | route | status |
|---|---|---|---|
| 1 | 3.990 | Lease now: 基本情報 1R 13.91m² 1F #102 (+2 backups) | selected |
| 2 | 2.345 | Share house room | alive |
| 3 | 1.076 | Monthly apartment while deciding | alive |
| 4 | 0.856 | Don't commit until the work location is known | alive |
| 5 | 0.538 | Stay in hotels/hostels | alive |

Selected: **Lease now: 基本情報 1R 13.91m² 1F #102 (+2 backups)**

Last decision (route_selected): initial selection: 'Lease now: 東京都中野区江原町 1K 23.0m² 1F #103 (+1 backups)' ranks first (2.783)

### Operations

| op | tool.action | status | note |
|---|---|---|---|
| acquire.discover.1 | acquire.discover | succeeded |  |
| lease.recheck | acquire.recheck | succeeded |  |
| lease.brief | fs.write | succeeded |  |
| lease.contact | human.perform | waiting_human |  |
| acquire.enrich.2 | acquire.enrich | succeeded |  |
| acquire.enrich.3 | acquire.enrich | succeeded |  |
| acquire.recheck.4 | acquire.recheck | succeeded |  |

## Loop ticks

- tick 1 (+433s): status active; acquire ['acquire.discover.1: no live housing options are known: cannot compare strategies without them']; ran before planning ['op_6037c50c688b']; generated ['housing-lease', 'housing-monthly', 'housing-share', 'housing-hostel', 'housing-defer']; selected housing-lease (route_selected)
- tick 2 (+490s): status active; acquire ['acquire.enrich.2: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-lease (plan_kept)
- tick 3 (+500s): status waiting_human; acquire ['acquire.enrich.3: shortlisted candidates lack verification, location and move-in facts needed to evaluate them']; ran before planning []; generated []; selected housing-lease (plan_kept)
- tick 4 (+500s): status waiting_human; acquire []; ran before planning []; generated []; selected None (None)
- tick 4 (+26s) [acquisition clock +7.0h, simulated]: status waiting_human; acquire ['acquire.recheck.4: time-sensitive claims (availability/rent) are past their TTL']; ran before planning []; generated []; selected housing-lease (plan_kept)
- tick 5 (+27s) [acquisition clock +7.0h, simulated]: status waiting_human; acquire []; ran before planning []; generated []; selected None (None)
