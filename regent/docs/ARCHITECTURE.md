# Regent architecture

## Roles

The **principal** (the human) owns values, hard constraints, permissions, identity and final
overrides. **Regent** owns operational strategy: planning, route generation, resource
allocation, tool and model selection, execution, verification, replanning and capability
acquisition. The principal is also a *callable real-world interface*. They are asked only for
bounded actions software cannot perform, never for the next step.

## The loop (`regent/core/loop.py`)

`RegentLoop.tick(mission)` runs one pass. Each phase is a named block in the code and is
recorded in `mission.attrs.last_tick`, which the cockpit shows.

| Phase | What happens | Code |
|---|---|---|
| observe | New events since the mission last looked; resume conditions of open interrupts are checked (e.g. the page no longer shows a CAPTCHA) | `observe/events.py`, `human/interrupts.py` |
| model | `WorldView` is loaded from projections; a snapshot is taken if anything changed | `world/state.py`, `world/projector.py` |
| acquire | For missions a domain adapter is responsible for (by tag, or by the mission's own words): "what don't I know that blocks a decision?" Each information need (discover / enrich / recheck) becomes an AUTO `acquire` operation | `loop._acquisition_needs`, `acquisition/service.py` |
| generate | If the world's structural signature changed (or no route is alive), every available provider proposes routes; proposals are merged and criticized | `routes/generator.py` |
| evaluate | Effective estimates, blockers, hard constraints, score components, ranking, decision-relevant uncertainties | `evaluate/evaluator.py` |
| select | Hysteresis selection; a decision is recorded with its snapshot | `planner/planner.py`, `replan/replanner.py` |
| decompose | Operations for the selected route; value-of-information probes across live routes; capability-acquisition sub-missions for the top two routes | `planner/planner.py`, `capabilities/manager.py` |
| execute | Authority, affordability and learned-skill shortcuts, then concurrent tool calls with retries/fallbacks; blockers become interrupts | `executor/executor.py` |
| verify | Declared verification → evidence → facts (cited to the evidence) | `verify/verifier.py` |
| update_world | Consequences: message answered, capability registered, skill extracted | `replan/replanner.py` |
| replan | Full re-evaluation of *every* route against the updated world; a switch records which evidence moved which estimate | `replan/replanner.py` |

`run_all()` drives every active mission, including ones spawned during the pass, until
quiescent. The API runs it in a background worker (`BackgroundLoop`) and immediately after any
write (new event, interrupt response, override).

Mission status is derived rather than set by hand:

- `completed`: the success criteria hold.
- `active`: operations are runnable.
- `waiting_human`: only interrupts remain.
- `monitoring`: nothing to do until the world changes.

## World acquisition (`regent/acquisition/`)

Regent gets the world it needs from the public internet instead of being handed it. The unit of
storage is the **claim**, not the page. Geography is part of the search space: Regent does not
assume the principal should live where they are now.

```
housing need ─► where could the principal live?  (GeographyResolver: regions compete)
   ─► for each chosen region: which sources?     (source registry ← country pack seeds, global
                                                   aggregators, discovery on the web)
   ─► navigate from entry pages to that city's listings (robots.txt, rate limits, honest UA)
   ─► extract Mentions/Claims (Japanese extractor, or the site-agnostic generic extractor)
   ─► identity resolution ─► beliefs + conflicts + freshness ─► funnel per region
   ─► deep research ─► projection ─► strategies compete across regions (money in one currency)
```

### Layers

| Layer | Where | Geography-specific? |
|---|---|---|
| Claims, beliefs, conflicts, freshness, identity bookkeeping, fetch policy, replay | `claims.py`, `resolution.py`, `fetch.py`, `replay.py` | no |
| Source discovery + learned source registry | `discovery.py`, table `acq_source_recipes` | no (domain supplies vocabulary) |
| Housing ontology: Region → Building → Unit (listing claims) | `housing/ontology.py` | no |
| Geography resolver | `housing/geography.py` | no |
| Generic listing extractor (JSON-LD, embedded app state, DOM cards; any currency) | `housing/generic_extract.py`, `housing/locale.py` | no |
| Adapter: orchestration, funnel per region, projection, strategies | `housing/adapter.py` | no |
| **Japan pack**: SUUMO/HOME'S/CHINTAI/at home/UR/Oakhouse/monthly-mansion, Japanese extractor, GSI geocoder, MLIT rail/library/university data, Tokyo hubs, prefecture codes | `housing/packs/japan.py`, `housing/extractor.py`, `housing/text.py` | Japan only |
| **Generic pack**: any country — registry sources + global aggregators + discovery; UK postcodes.io / US Census geocoding where they exist; distance to centre | `housing/packs/generic.py` | no |
| Seeds: a few local entry points for GB/US/DE/NZ, official guidance pages, global aggregators | `housing/packs/seeds.py` | seed data only |

### Geography (`housing/geography.py`)

1. **Principal evidence.** World facts (`principal.country`, `principal.citizenship`,
   `principal.stay_in_country`, `principal.regions`) and weak signals such as the mission's language.
   Evidence about where the principal *is* changes the stay prior and moving costs. It never
   decides where they *should live*.
2. **Candidate world.** Cities come from public directories: HousingAnywhere and Spotahome city
   lists (with live listing counts) and the Craigslist worldwide site list. Packs contribute their
   own region lists as a fallback. Nothing is hand-picked. A city name that is ambiguous across
   countries (London GB vs London, Ontario) is not attached to either.
3. **Screening.** Countries first, by source coverage, supply and home evidence, with the best
   country of each continent included. Then cities within a country, by directory supply, how
   prominently the country's own portals link them, and GeoNames population (Open-Meteo geocoder).
4. **Price signal.** One live listing page per shortlisted city. It comes from the country's own
   portals first (apartments), with global aggregators as a fallback; rooms-only signals are
   labelled and earn no affordability credit. Prices are converted to the reference currency with
   ECB rates.
5. **Choice.** A transparent utility decides:
   `0.40·affordability + 0.20·supply + 0.15·coverage + 0.15·stay prior + 0.10·relocation distance`.
   Constraints: at most one city per foreign country (two in the home country), at most two
   foreign regions per continent, default 5 regions. Every candidate that was not chosen is
   recorded with its reason.

The right to live somewhere is a prior (`stay_rules.p_allowed`): 0.3 abroad with unknown
citizenship, higher where the principal appears to live. Every relocation route carries it as
success probability and as the uncertainty `principal.right_to_reside.<CC>`. The value-of-information
analysis can then show that asking the principal about citizenship flips the plan.

### Sources and discovery (`discovery.py`)

For a country with too few known local sources, `SourceDiscovery` assembles candidates from five
channels:
- the **registry** (earlier verifications, pack seeds);
- **search** (Brave API when `REGENT_SEARCH_API_KEY` is set);
- **authority** pages (official housing and visa guidance), whose outbound links are followed;
- **snowball** links from verified sources;
- **probe**: domains built from the local language's words for housing and the country TLD
  (`alquiler` + `.es`, `realestate` + `.co.nz`).

A candidate is **verified only by use**: navigation from its entry page must reach pages the
extractor turns into records located in the requested city. The check uses the city in the URL
or in at least 40 % of the records, so a nationwide page that merely mentions the city fails.
Sale, vacation and hotel paths are avoided. Blocked candidates (robots, 403/405/429, CAPTCHA,
bot challenges) are recorded and never retried aggressively: a host that refused three times and
never answered is skipped. A verified source stores the navigation trail per city, so the next
run goes straight to the listings.

### Claims and beliefs (`claims.py`)

A claim's weight is source reliability × extractor confidence × freshness decay. Per value,
support is the noisy-OR over independent hosts. A host's own later observation supersedes its
earlier one, but never another host's. The belief is the top hypothesis with
`confidence = share × support`, and every other hypothesis is kept with its sources.

Attribute semantics come from the ontology:
- tolerant numbers (rent ±0.5 %, build year ±1);
- normalized text;
- hierarchical addresses (Japanese prefecture|city|town|chōme, or a street key elsewhere);
- sets: stations, where only the same station's walk time differing by more than 3 minutes is a
  conflict.

Rents are claimed in the source's currency, with the period normalized to monthly (pcm, per
week, per night, Kaltmiete) and the raw text kept as evidence. Comparison across regions converts
at read time.

### Identity (`resolution.py`, `housing/resolver.py`)

- **Common bookkeeping:** blocking, log-odds pairwise scores, merge at ≥0.85, 0.5–0.85 recorded
  as ambiguous and not merged, and every decision (including "new") logged in `acq_links`.
- **Japan pack rules:** layout, area, floor and room number; the same site showing different
  terms means a different listing.
- **Generic rules:** the same listing URL is the same unit; another URL on the same site is
  another listing; across sites, bedrooms, area, rent and title must agree. Buildings match on
  street address, postcode or coordinates.

### Access policy (`fetch.py`)

- robots.txt is obeyed; a server error on robots.txt counts as disallow;
- a per-host delay applies, and Crawl-delay is honoured;
- the crawler is stateless: cookies are cleared before every request, so one navigation cannot
  steer the next;
- HTTP 401/403/405/429/451, CAPTCHA walls and empty-202 bot challenges are recorded as `blocked`
  and never bypassed;
- a browser is used only when a static page yields no records;
- rechecks and verifications never answer from cache.

### Funnel, strategies, freshness

- **Funnel:** one per region, against that region's own live market (median ×1.15 cap, household
  fit by bedrooms or Japanese layout, minimum area, walk time where known). Three candidates per
  region go to deep research.
- **Strategies** compete across regions:
  - lease in each region (money in the reference currency, deposit a labelled 1-month prior if
    unstated, relocation a labelled prior of 150 USD + 0.08 USD/km);
  - furnished mid-term and room options, from the cheapest live evidence;
  - hostel days (live Hostelworld prices where acquired);
  - existing base;
  - defer until the work location and right to stay are known.
- **Freshness:** availability 6 h, rent 24 h, structure 1 y. Stale beliefs make the ACQUIRE
  phase create a recheck, which re-observes the source.

**Replay** (`replay.py`): `export_fixtures` copies recorded pages and robots.txt into a
directory, and `ReplayTransport` serves them to the same `Fetcher`. The tests run the real
geography, discovery, extraction, resolution, funnel and loop on pages Regent captured from the web.

## Data model (PostgreSQL; SQLite fallback)

- **Event store** (`events`): append-only, with a sequence number, type, payload, source,
  mission and domain. All meaningful changes are events: world facts, messages, resource
  changes, grants, constitution updates, capability changes, skill publications, and loop
  activity (`route_selected`, `plan_changed`, `tool_failed`, …).
- **Projections**: `entities` (21 kinds, from person to human_interrupt), `relations`
  (`member_of`, `owns`, `depends_on`, `blocked_by`, …), `facts` (key/value, confidence, evidence
  id), `resources`, `ledger`, `constitution`, `authority_grants`, `capabilities`, `skills`,
  `global_facts`. `projector.rebuild()` wipes and replays them, optionally from a `snapshot`.
  Tests assert that a rebuilt world equals the live one.
- **Operational state**: `missions`, `routes`, `route_scores` (score history per tick),
  `operations`, `human_interrupts`, `evidence`, `decisions`, `model_calls`, `built_tools`. These
  rows are mutated in place, but every mutation also emits an event and, for strategy, a
  `decision`. History is therefore complete, but only the *world* is replayable. Replaying
  operational state would re-execute side effects, which is deliberately not done.
- **Memory** (`memory`): pgvector `vector(256)` embeddings on PostgreSQL. The default embedder is
  a deterministic feature hasher; a provider embedding model can replace it without a schema
  change.

A graph database was not introduced. Relations are first-class rows, and the queries needed
(neighbours, typed edges) are cheap in SQL.

## Routes

A `RouteProposal` (`regent/schemas.py`, exported to `packages/schemas`) carries:

- thesis
- estimates: upside, P(success), time, money, information gain, reversibility, optionality,
  risk, authority cost
- rationale for the estimates
- sensitivities
- blockers
- required capabilities
- dependencies
- uncertainties
- concrete operations

A **sensitivity** is how a route states its dependence on an unknown fact:
`{fact, op, value, effects: {field: {mul|add|set|from_fact}}, rationale}`. It is the bridge
between evidence and strategy. It makes replanning deterministic and explainable, and it is
what the value-of-information calculation perturbs.

### Scoring (`evaluate/evaluator.py`)

```
score =  w_ev · p · U(upside / value_scale)          U concave above 1 ("enough")
       + w_info · info + w_opt · optionality + w_rev · reversibility
       − w_time · min(hours/40, 1.5) − w_money · min(cost / free_cash, 1.5)
       − w_risk · risk − w_auth · authority_cost + Σ constitution tag bonuses
```

- `free_cash` is the cash balance minus commitments due within max(horizon, 30 days).
- Weights are the base weights × constitution multipliers (items can be scoped to mission tags)
  × scarcity. Scarcity comes from the treasury: under one month of runway, money pressure is 1.0.
- Hard constraints invalidate a route. Capability blockers make it unselectable until the
  capability is acquired.
- Selection margin is `SWITCH_MARGIN = 0.03`.

### Multiple providers

`ProviderRegistry.all_available()` generates and criticizes routes. Each provider's estimates are
stored under `route.estimate_sources[provider]`. The combined estimate is a weighted mean, and
spread above 25% becomes an explicit uncertainty. Critique adjustments are recorded as a
low-weight source (0.3). They are never applied as truth. Evidence (via sensitivities) always
outranks model opinion.

The local strategist (`models/local.py`, `models/playbooks.py`) generates routes from entity
attributes: contracts, marketplaces, monetizable projects, negotiable commitments and
workplaces. Generic archetypes (direct, information-first, delegate, defer) guarantee at least
three routes for any objective. It is weaker than a frontier model, but it makes the system run
with zero credentials and makes the tests deterministic.

## Authority

| Level | Examples | Behaviour |
|---|---|---|
| AUTO | research, analysis, drafts, local files, tests, private calendar holds | Run |
| COMMIT | send, invite, purchase, POST, browser click/type/upload | Run if a standing grant matches (`fnmatch` scope + constraints); else a bounded approve/deny interrupt ("Always allow" creates a grant) |
| IDENTITY | CAPTCHA, login, biometric, signature, payment entry, physical action | Always a human interrupt |

Operation specs may *raise* their authority level but never lower it. Unknown tool actions are
treated as COMMIT.

## Human interrupts

`{kind, reason, required_action, estimated_time_seconds, blocking_operation, resume_condition,
response_schema, context}`.

Resume conditions:

- `response`: wait for the principal's answer.
- `fact`: a world fact appears.
- `page_state`: the blocker is gone from the page. Checked by HTTP, throttled to once per 5 s.

On resolution, one of three things happens:

- A blocked browser operation is re-run.
- An authorization marks the operation approved once.
- A human-routed operation is verified from the principal's response.

The principal's time is spent from the `attention` resource.

## Capability acquisition

`CapabilityManager.open_acquisition()` creates a child mission tagged `capability_acquisition`.
Its routes are built from the nine acquisition strategies:

1. existing tool
2. alternative service
3. better model
4. activate or build a connector (a bounded credential action)
5. browser automation
6. generate code
7. manual human action
8. purchase
9. avoid the dependency

The normal evaluator picks among them. The `generate_code` path (`code.build_tool`) asks a
provider for a tool (the local strategist has vetted templates), writes it to the workspace,
runs its tests, and registers it in the tool registry. It is persisted in `built_tools` and
reloaded on restart.

## Global brain and skills

Every row has a `domain`: private, shared or global. `LocalGlobalBrain.publish_fact` and
`publish_skill` pass through `PrivacyFilter`:

- Publishing is refused for emails, phone numbers, amounts, and names of private entities.
- Skills are generalized by replacing those values with placeholders.

`RemoteGlobalBrain` is the synchronization interface (`REGENT_GLOBAL_BRAIN_URL`); it sends only
the global domain.

`SkillExtractor` turns attempt → failure → reroute → success into a skill. The skill holds the
pattern, preconditions, procedure (including the human step), failure modes, verification,
confidence and provenance. Before running an operation, the executor applies a learned reroute
when the same failure mode still holds.

## Current limits

- **No web-search engine.** Every HTML search engine reachable from this environment disallows
  bots in robots.txt or serves a bot challenge. Discovery therefore navigates from known portal
  entry pages. `REGENT_SEARCH_API_KEY` (Brave Search API) is the interface for open-web search
  and was not exercised.
- **Blocked sources stay blocked.** at home answers the honest crawler with HTTP 405. It is
  recorded and skipped.
- **Search-less discovery.** Without a search API, local portals are found by authority links,
  snowballing and language-vocabulary domain probes. That works for some countries (Spain:
  `alquiler.es` was found and verified) and not for others (Portugal: every probe was blocked or
  had no listings, so only global aggregators were used).
- **Blocked ecosystems.** Australia's, Singapore's and Thailand's major portals, and several in
  France and the Netherlands, block honest crawlers (Kasada/Cloudflare/403). Those regions can
  only be judged from global aggregators, and the report says so.
- **Wikipedia/Wikidata** answer 403 from this network, and Numbeo rate-limits it, so neither is
  used for candidate generation.
- **Relocation cost and the right to stay** are labelled priors until the principal answers the
  citizenship question.

- **Local strategist.** Without API keys, route generation is playbook-based. The remote
  providers are implemented against the same schema but were not exercised here (no keys in
  this environment).
- **Local connector backends.** Mail, calendar, maps and commerce persist locally; the real
  backends are credential-gated interfaces.
- **Loop worker.** The background loop is in-process and single-worker, which is enough locally.
  A multi-worker deployment would need a job queue with row-level locking on missions.
- **Route merging** is by key. Semantically equivalent routes from different providers with
  different keys are not yet clustered.
- **Uncertainty modelling** uses branch enumeration over the declared sensitivity values, not
  full probability distributions.
