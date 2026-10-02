# Software needs benchmark

Run 2026-10-02T12:28:27Z, 160 s wall clock, live public web, one fresh world. Regent received only the sentences below; no provider, metric, framework, database, UI, deployment method or plan.

## Result: PASS

### LindyBooks

- [x] the subject was found on the live web from its name alone: lindy-books.org
- [x] 5 competing routes were generated and scored
- [x] the conventional route (add an analytics script) was ruled out by the product's own public promise
- [x] the capability passed Regent's own acceptance suite (14/14 checks passed)
- [x] the capability is active: degraded, tool `cap_lindybooks_active_real_users`, coverage 0.15
- [x] no number is shown for a source Regent cannot read (0 metrics honestly blocked)
- [x] every number about people is a form its audited premises earn; anything else is labelled a proxy (the headline says Unknown when only proxies exist)
- [x] no credential requested: every platform source the principal could unlock is only a proxy for the question (open interrupts: 0)
- [x] active human time so far: 22.5 s
- [x] a later mission, worded differently, reused the capability without re-examining the world
- [x] Regent re-read the capability's sources on its own (1 -> 2 observations)

### Second need (materially different)

- [x] the place was resolved: Osaka (34.694, 135.501, Asia/Tokyo)
- [x] sources were proposed and admitted only by use: 1 APIs, 1 existing-service pages; 2 rejected
- [x] a source whose robots.txt disallows Regent was rejected, not worked around
- [x] using an existing service competed with building from data
- [x] the yes/no answer is a stated rule Regent parsed and evaluated live
- [x] the view and the message lead with the yes/no verdict
- [x] acceptance suite: 16/16 checks passed
- [x] mission status: completed
- [x] the morning message was delivered: Yes – hang laundry outside — Laundry index for today (Yahoo!): 90 — (Text wind direction and strength description 北の風　海上　では　北の風　やや強く; Verdict text for today's l
- [x] active human time: 22.5 s

## A. “I want to know, at a glance, how many real people are actually using LindyBooks.”

77.2 s, 4 ticks, reasoning-worker cost $0.117 (8 live calls). Final status: **monitoring**.

### What the sentence needs (need analysis, checked against the sentence)

- handled as: `software_capability`; analysed by claude-code
- subjects: LindyBooks (product)
- deliverable: {'why': "'At a glance' implies a view readable in seconds that stays current, showing one headline number of real active people.", 'form': 'glance_view', 'refresh': 'continuous', 'deliver_at_local': None, 'max_seconds_to_read': 5}
- **active_real_users** (core, count): How many distinct real human people have actually used LindyBooks recently? — quantity: Distinct individual humans who performed at least one meaningful use action (e.g. opened and engaged with the product beyond signing up or loading a page) in the window; each person counted once across devices/accounts where identifiable; excludes: bots, crawlers and automated traffic, internal staff, developers and test/demo accounts, duplicate accounts or devices of the same person where detectable, registered-but-inactive accounts, fake/spam signups, anonymous one-off page loads with no real engagement; windows: 24h, 7d, 30d
- **registered_vs_active** (supporting, count): How does the count of actively-using real people compare to total registered accounts? — quantity: Total registered accounts, for context against active real people; excludes: test/internal accounts; windows: all_time

### What Regent found

- LindyBooks: 52 hostnames probed; deployments: lindy-books.org (aliases ['lindy-books.pages.dev'], platforms ['cloudflare'], analytics none, promises ['No ads', 'no accounts', 'no tracking', 'free forever', '追跡なし', '広告なし', '永久に無料'], 13 readable endpoints, owner evidence ["'yi525tokyo' in hostname (variant of yi525tokyo-prog)"])
- proposed sources admitted: APIs [], pages []

Fields that bear on the need (9 of 15 classified; each reading backed by a verbatim quote Regent found in what it observed):

| source | field | relation | meaning | quote found |
|---|---|---|---|---|
| https://lindy-api.yi525tokyo.workers.dev/api/fund | paid.count | proxy | Number of paid payments (translation unlock/donations) recorded by the fund; each unit is  | True |
| https://lindy-api.yi525tokyo.workers.dev/api/fund | today.prose | proxy | Daily usage count against the prose translation cap (resets daily); each unit is a transla | True |
| connector:cloudflare_analytics | uniques_last_full_day | proxy | Distinct client IPs on last complete UTC day, including bots | True |
| connector:cloudflare_analytics | uniques_7d_daily_max | proxy | Highest daily distinct-IP count over 7 days | True |
| connector:cloudflare_analytics | page_views_last_full_day | proxy | HTML page views on last day | True |
| connector:cloudflare_analytics | page_views_7d | proxy | HTML page views over 7 days | True |
| connector:stripe_payments | paying_people | proxy | Distinct paying customers all time | True |
| connector:stripe_payments | successful_charges | proxy | Paid unrefunded charges | True |
| connector:google_search_console | clicks_28d | proxy | Google search clicks over 28 days | True |

Ruled out as unrelated: `recent.0.amount` (Amount of the most recent payment (minor currency units), ev); `paid.jpy` (Total yen paid); `total` (Catalogue size (books)); `books.0.dl` (Upstream Gutenberg download count for a book); `sealed` (Number of sealed books in catalogue); `impressions_28d` (Search impressions)
- commitment: No accounts, so registered-account counts do not exist and users can't be required to sign in — quote “No ads, no accounts, no tracking” found: True
- commitment: No tracking: no third-party analytics or client tracking code — quote “No ads, no accounts, no tracking” found: True
- commitment: No ads — quote “No ads, no accounts, no tracking” found: True

### Routes

| rank | route | status | score | P(success) | upside | authority | why not / note |
|---|---|---|---|---|---|---|---|
| 1 | Compose a live answer from what lindy-books.org already publishes | selected | 0.551 | 0.89 | 0.15 | 0.00 |  |
| 2 | Have a coding agent build a bespoke usage application on the same sources | alive | 0.388 | 0.60 | 0.15 | 0.40 |  |
| 3 | Change the product to count its own readers first-party | alive | 0.388 | 0.35 | 0.95 | 0.85 |  |
| 4 | Look it up yourself on the Cloudflare zone analytics dashboard | alive | 0.295 | 0.88 | 0.07 | 0.90 |  |
| 5 | Add a client-side analytics script to the product | invalidated | 0.652 | 0.70 | 0.85 | 0.70 | lindy-books.org publicly promises: "No ads, no accounts, no tracking" -- nothing may add tracking to it (route tagged 'third_party_tracking: |

Selected: **software-compose-public**

### Loop

- tick 1 (+74.3s, active): acquire ['acquire.analyze.1', 'acquire.discover.1', 'acquire.inventory.1']; select software-compose-public (route_selected); executed 1; verified [('acquire.analyze.1', 'pass'), ('acquire.discover.1', 'pass'), ('acquire.inventory.1', 'pass'), ('sw.compose', 'pass')]
- tick 2 (+76.9s, active): acquire []; select software-compose-public (plan_kept); executed 1; verified [('sw.verify', 'pass')]
- tick 3 (+77.2s, monitoring): acquire []; select software-compose-public (plan_kept); executed 1; verified [('sw.activate', 'pass')]
- tick 4 (+77.2s, monitoring): acquire []; select None (None); executed None; verified []

### Operations

| op | tool | authority | status | note |
|---|---|---|---|---|
| acquire.analyze.1 | acquire.analyze | AUTO | succeeded |  |
| acquire.discover.1 | acquire.discover | AUTO | succeeded |  |
| acquire.inventory.1 | acquire.inventory | AUTO | succeeded |  |
| sw.compose | software.compose | AUTO | succeeded |  |
| sw.verify | software.verify | AUTO | succeeded |  |
| sw.activate | software.activate | AUTO | succeeded |  |

### Capability `lindybooks-active-real-users` v1 (composed, degraded)

Tool `cap_lindybooks_active_real_users`; view `/software/lindybooks-active-real-users`; JSON `/api/software/capabilities/lindybooks-active-real-users`. Sources: lindy_api_fund (http_json, public)

![glance view](software-lindybooks-view.png)

Acceptance suite (Regent's own; nothing taken on a worker's word):

- [x] metrics are well-formed — 3 metrics
- [x] every number about the people asked about is a form its premises earn — bounds rest on audited premises; everything else is labelled a proxy
- [x] a headline exists — 
- [x] every core question is answered or marked unknowable-yet — 1 core question(s) accounted for
- [x] the capability answers at least one question with a number — 
- [x] source lindy_api_fund answers with every declared field — 2 fields
- [x] independent read of lindy_api_fund agrees — every field matches a direct read
- [x] no number is shown for a blocked source — 0 metric(s) honestly blocked
- [x] counts are non-negative — 
- [x] lower bounds do not exceed upper bounds — 
- [x] stored observations contain no identifying data — aggregates only
- [x] sources are read-only and add nothing to the product — every source is a read of something that already exists
- [x] the registered tool returns the computed answer — 3 metrics via cap_lindybooks_active_real_users.read
- [x] the glance view shows exactly the computed numbers — 3 numbers match (rendered in headless Chromium)

What it shows now:

| metric | shows | form | status | definition |
|---|---|---|---|---|
| How many distinct real human people have actually used LindyBooks rec… (headline) | Unknown | context | unknowable | Distinct individual humans who performed at least one meaningful use action (e.g. opened and engaged with the product beyond signing up or loading a page) in the window; each person counted once across devices/accounts w |
| Proxy: Number of paid payments | 2 | proxy | ok | Number of paid payments (translation unlock/donations) recorded by the fund; each unit is one payment. A signal that may move with the answer; no number of people follows from it. Missing: membership, distinctness, windo |
| Proxy: Daily usage count against the prose translation… (last 24h) | 0 | proxy | partial | Increase of the daily counter today.prose over the last 24 hours (resets counted): Daily usage count against the prose translation cap (resets daily); each unit is a translation request/item, not a person |

Not knowable yet:

- How many distinct real human people have actually used LindyBooks recently?: only proxies: signals that move with the answer but are not counts of the people asked about
- How does the count of actively-using real people compare to total registered accounts?: no source Regent can read answers this

### Human

- active human time spent: **22.5 s**; requested and still open: 0.0 s (statement typing at 40 wpm + interrupts (measured when reported, else Regent's estimate))

## D. “Every morning, tell me whether it's a good day to dry laundry outside in Osaka.”

62.0 s, 4 ticks, reasoning-worker cost $0.146 (10 live calls). Final status: **completed**.

### What the sentence needs (need analysis, checked against the sentence)

- handled as: `software_capability`; analysed by claude-code
- subjects: Osaka (place), outdoor laundry drying (other)
- deliverable: {'why': "'Every morning, tell me' implies a push of a short daily verdict; no time given, so a morning default of 07:00 is assumed.", 'form': 'alert', 'refresh': 'daily', 'deliver_at_local': '07:00', 'max_seconds_to_read': 10}
- **good_day_to_dry_laundry_outside** (core, yes_no): Is today a good day to dry laundry outside in Osaka? — quantity: Verdict (yes/no) for today's suitability for line-drying laundry outdoors, derived from forecast conditions during the drying window (daytime hours, local time): chance/amount of precipitation, humidity, wind, temperature and sunshine/cloud cover; excludes: yesterday's or past-day weather, days other than today, forecasts for other cities or regions, indoor drying conditions; windows: today daytime 09:00-17:00 JST
- **supporting_conditions** (supporting, list): What conditions underlie the verdict today? — quantity: Forecast precipitation probability (%), relative humidity (%), wind speed (m/s), temperature (°C), and sky/sunshine for today's drying window; excludes: non-Osaka locations; windows: today daytime 09:00-17:00 JST

### What Regent found

- Osaka: place {'name': 'Osaka', 'admin1': 'Osaka', 'country': 'Japan', 'latitude': 34.69379, 'timezone': 'Asia/Tokyo', 'elevation': 4.0, 'longitude': 135.50107, 'population': 2753862, 'country_code': 'JP'}
- outdoor laundry drying: a topic, not a named thing: it has no site of its own to find
- proposed sources admitted: APIs ['https://www.jma.go.jp/bosai/forecast/data/forecast/270000.json'], pages ['Yahoo! Japan Weather - Osaka city']
  - rejected https://api.open-meteo.com/v1/forecast?latitude=34.69379&longitude=135.50107&hourly=temperature_2m,relative_hu: did not answer: 0 {'type': 'robots', 'detail': 'disallowed by robots.txt'}
  - rejected https://tenki.jp/indexes/laundry/6/30/6200/27100/: page not readable: 404 

Fields that bear on the need (9 of 13 classified; each reading backed by a verbatim quote Regent found in what it observed):

| source | field | relation | meaning | quote found |
|---|---|---|---|---|
| jma.go.jp/bosai/forecast/data/forecast/270000.json | 0.timeSeries.0.areas.0.weathers.0 | context | Text weather forecast for Osaka Prefecture for the first period (today/tonight at report t | False |
| jma.go.jp/bosai/forecast/data/forecast/270000.json | 0.timeSeries.0.areas.0.weatherCodes.0 | context | JMA weather code for the day (100=sunny, 200=cloudy) | False |
| jma.go.jp/bosai/forecast/data/forecast/270000.json | 0.timeSeries.0.areas.0.winds.0 | context | Text wind direction and strength description (qualitative, not m/s) for the period | True |
| ttps://weather.yahoo.co.jp/weather/jp/27/6200.html | laundry_index_today | direct | Laundry index for today (first occurrence, 10/2), 0-100; higher is better for drying | True |
| ttps://weather.yahoo.co.jp/weather/jp/27/6200.html | laundry_comment_today | direct | Verdict text for today's laundry index | True |
| ttps://weather.yahoo.co.jp/weather/jp/27/6200.html | today_weather | context | Today's weather forecast for Osaka | True |
| ttps://weather.yahoo.co.jp/weather/jp/27/6200.html | today_high_temp | context | Today's forecast high temperature in °C | True |
| ttps://weather.yahoo.co.jp/weather/jp/27/6200.html | umbrella_index_today | context | Umbrella index today; 0 means no rain expected | True |
| ttps://weather.yahoo.co.jp/weather/jp/27/6200.html | tomorrow_precip_first_block | context | Tomorrow's 0-6h precipitation probability | True |

Ruled out as unrelated: `0.timeSeries.1.areas.0.pops.0` (JMA probability of precipitation (%) for the first 6-hour bl); `0.timeSeries.1.areas.0.pops.2` (Precipitation probability (%) for the 06:00-12:00 block of t); `0.timeSeries.2.areas.0.temps.1` (Forecast daytime maximum temperature (°C) for Osaka city (st); `1.timeSeries.0.areas.0.pops.1` (Weekly forecast daily precipitation probability (%) for the )
- decision rule `(latest("weather_27_6200_html:laundry_index_today") >= 60) or (latest("weather_27_6200_html:umbrella_index_today") == 0 and latest("weather_27_6200_html:today_high_temp") >= 18)` -> True now

### Routes

| rank | route | status | score | P(success) | upside | authority | why not / note |
|---|---|---|---|---|---|---|---|
| 1 | Build from public data and cross-check against Yahoo! Japan Weather - Osaka city | selected | 1.272 | 0.92 | 0.90 | 0.00 |  |
| 2 | Use what Yahoo! Japan Weather - Osaka city already shows people | alive | 1.092 | 0.83 | 0.90 | 0.00 |  |
| 3 | Have a coding agent build a bespoke usage application on the same sources | alive | 0.838 | 0.60 | 0.90 | 0.40 |  |
| 4 | Build the answer from public data with a stated rule | alive | 0.468 | 0.86 | 0.05 | 0.00 |  |

Selected: **software-compose-crosscheck**

### Loop

- tick 1 (+56.1s, active): acquire ['acquire.analyze.1', 'acquire.discover.1', 'acquire.inventory.1']; select software-compose-crosscheck (route_selected); executed 1; verified [('acquire.analyze.1', 'pass'), ('acquire.discover.1', 'pass'), ('acquire.inventory.1', 'pass'), ('sw.compose', 'pass')]
- tick 2 (+61.8s, active): acquire []; select software-compose-crosscheck (plan_kept); executed 1; verified [('sw.verify', 'pass')]
- tick 3 (+62.0s, completed): acquire []; select software-compose-crosscheck (plan_kept); executed 1; verified [('sw.activate', 'pass')]
- tick 4 (+62.0s, completed): acquire []; select None (None); executed None; verified []

### Operations

| op | tool | authority | status | note |
|---|---|---|---|---|
| acquire.analyze.1 | acquire.analyze | AUTO | succeeded |  |
| acquire.discover.1 | acquire.discover | AUTO | succeeded |  |
| acquire.inventory.1 | acquire.inventory | AUTO | succeeded |  |
| sw.compose | software.compose | AUTO | succeeded |  |
| sw.verify | software.verify | AUTO | succeeded |  |
| sw.activate | software.activate | AUTO | succeeded |  |

### Capability `osaka-outdoor-laundry-drying-good-day-to-dry-laundry-outside` v1 (composed, usable)

Tool `cap_osaka_outdoor_laundry_drying_good_day_to_dry_laundry_outside`; view `/software/osaka-outdoor-laundry-drying-good-day-to-dry-laundry-outside`; JSON `/api/software/capabilities/osaka-outdoor-laundry-drying-good-day-to-dry-laundry-outside`. Sources: jma_forecast_270000_json (http_json, public); weather_27_6200_html (html_page, public)

![glance view](software-laundry-view.png)

Acceptance suite (Regent's own; nothing taken on a worker's word):

- [x] metrics are well-formed — 8 metrics
- [x] every number about the people asked about is a form its premises earn — bounds rest on audited premises; everything else is labelled a proxy
- [x] a headline exists — 
- [x] every core question is answered or marked unknowable-yet — 1 core question(s) accounted for
- [x] the capability answers at least one question with a number — 
- [x] source jma_forecast_270000_json answers with every declared field — 1 fields
- [x] independent read of jma_forecast_270000_json agrees — every field matches a direct read
- [x] source weather_27_6200_html answers with every declared field — 6 fields
- [x] independent read of weather_27_6200_html agrees — every field matches a direct read
- [x] no number is shown for a blocked source — 0 metric(s) honestly blocked
- [x] counts are non-negative — 
- [x] lower bounds do not exceed upper bounds — 
- [x] stored observations contain no identifying data — aggregates only
- [x] sources are read-only and add nothing to the product — every source is a read of something that already exists
- [x] the registered tool returns the computed answer — 8 metrics via cap_osaka_outdoor_laundry_drying_good_day_to_dry_laundry_outside.read
- [x] the glance view shows exactly the computed numbers — 8 numbers match (rendered in headless Chromium)

What it shows now:

| metric | shows | form | status | definition |
|---|---|---|---|---|
| Text wind direction and strength description | 北の風　海上　では　北の風　やや強く | context | ok | Text wind direction and strength description (qualitative, not m/s) for the period |
| Laundry index for today (Yahoo!) (headline) | 90 | estimate | ok | Laundry index for today (first occurrence, 10/2), 0-100; higher is better for drying |
| Verdict text for today's laundry index (Yahoo!) | 絶好の洗濯日和。バスタオルも速乾 | estimate | ok | Verdict text for today's laundry index |
| Today's weather forecast for Osaka | 晴れ | context | ok | Today's weather forecast for Osaka |
| Today's forecast high temperature in °C | 27 | context | ok | Today's forecast high temperature in °C |
| Umbrella index today | 0 | context | ok | Umbrella index today; 0 means no rain expected |
| Tomorrow's 0-6h precipitation probability | 0 | context | ok | Tomorrow's 0-6h precipitation probability |
| Is today a good day to dry laundry outside in Osaka? (headline) | Yes – hang laundry outside | decision | ok | The laundry index is a direct service verdict for drying (0-100); 60 or more is conventionally 'good'. If it is missing, fall back on physics: an umbrella index of 0 means no rain is expected, so laundry stays dry, and a |

Not knowable yet:

- What conditions underlie the verdict today?: only proxies: signals that move with the answer but are not counts of the people asked about

### Human

- active human time spent: **22.5 s**; requested and still open: 0.0 s (statement typing at 40 wpm + interrupts (measured when reported, else Regent's estimate))

## B. Reuse: “How many people actually read LindyBooks these days?”

Status monitoring in 17.4 s. Need-analysis reuse match: [('lindybooks-active-real-users', 'claude-code', ['active_readers_recent'])]

| rank | route | status | score | P(success) | upside | authority | why not / note |
|---|---|---|---|---|---|---|---|
| 1 | Use the existing capability 'LindyBooks: How many distinct real human people have actually used LindyBooks recently?' | selected | 0.675 | 0.94 | 0.30 | 0.00 |  |

Operations: acquire.analyze.1 (succeeded), sw.reuse (succeeded)

## C. Regent keeps using what it built

After the refresh period, maintenance re-read the sources: ['lindybooks-active-real-users: done']; observations 1 -> 2 (the clock was advanced one hour for the refresh check only; the reads were live).

## E. Delivery

- 2026-10-02 via regent_inbox: Yes – hang laundry outside — Laundry index for today (Yahoo!): 90 — (Text wind direction and strength description 北の風　海上　では　北の風　やや強く; Verdict text for today's laundry index (Yahoo!) 絶好の洗濯日和。バスタオルも速乾; Today's weather forecast for Osaka 晴れ)

## Delegatable resources at run time

| resource | available | note |
|---|---|---|
| reasoner | True | Claude Code CLI, headless and tool-less; answers are claims Regent checks |
| coding_agent | False | the principal has not opted in to autonomous coding agents (set REGENT_CODING_AGENT=claude-code); each run also needs an approved operation |
| http | True | read-only, robots.txt respected, honest user agent |
| browser | True | renders pages; stops at CAPTCHAs and logins (human interrupt) |
| human | True | identity, credentials, payment approval, irreversible commitments only |
| connector:cloudflare_analytics | False | read-only, server-side: Cloudflare already records this as the product's host; nothing changes for the product or its readers |
| connector:stripe_payments | False | read-only, server-side; identities are hashed in memory and never stored |
| connector:google_search_console | False | read-only; the site is already verified with Google |
| connector:plausible | False | read-only; script already present |
| connector:client_side_analytics | False | adds third-party code to every page the product serves and sends each visit to a third party |
