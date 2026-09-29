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
storage is the **claim**, not the page:

```
information need ─► plan (sources × areas from live market data) ─► navigate from entry pages
   ─► fetch (robots.txt, per-host rate limit, honest UA; browser only when static HTML has no records)
   ─► extract Mentions with Claims ─► entity resolution ─► beliefs + conflicts + freshness
   ─► funnel ─► deep research jobs ─► projection into the world model ─► competing strategies
```

**Generic core** (knows nothing about housing):

| Object | Where | Notes |
|---|---|---|
| AcquisitionRequest | `acq_requests` | goal, params and assumptions, plan, stats, stage log |
| Source | `acq_sources` | robots.txt, fetch/ok/blocked/disallowed counts, learned reliability Beta(agree, disagree) |
| Document | `acq_documents` | every fetch, including blocked and disallowed ones; the gzipped HTML is cached for provenance and replay |
| Mention | `acq_mentions` | one record found on one page, with its raw text |
| Claim | `acq_claims` | `{value, source, url, document, observed_at, ttl, confidence, extractor, evidence}`; append-only |
| Entity | `acq_entities` | Building → Unit (Listing = the claims about a unit from one source); beliefs are derived |
| Evidence link | `acq_links` | every identity decision (merged / ambiguous / rejected / new) with its features |
| Conflict | `acq_conflicts` | ≥2 hypotheses each holding ≥20 % of support; opened and resolved, never deleted |
| FreshnessPolicy | `AttrSpec.ttl_s` | availability 6 h, rent 24 h, structure/build year 1 y, … |
| EnrichmentJob | `acq_jobs` | detail page, operator verification, geocode, stations, noise, facilities, commute, move-in, recheck |

**Beliefs** (`claims.py`): a claim's weight is source reliability × extractor confidence ×
freshness decay. Per value, support is the noisy-OR over independent hosts. A host's own
later observation supersedes its earlier one, but never another host's. The belief is the top
hypothesis with `confidence = share × support`, and every other hypothesis is kept with its
sources.

Attribute semantics come from the domain:
- tolerant numbers (rent ±0.5 %, build year ±1)
- normalized text (names)
- hierarchical values: "上馬2" agrees with "上馬2丁目10-8"
- sets: sites list different stations, and only the same station's walk time differing by
  more than 3 minutes is a conflict

**Identity** (`resolution.py` plus the domain resolver):
- blocking by municipality and town
- log-odds pairwise scores
- merge at ≥0.85; 0.5–0.85 is recorded as ambiguous and **not** merged
- a matching child record (a unit) can lift an ambiguous parent (a building) through joint
  evidence

Housing rules:
- two rows of one page are never one unit
- the same site showing different terms means different listings
- across sites, a price difference is a claim conflict, not a new room

**Access policy** (`fetch.py`):
- robots.txt is obeyed; a server error on robots.txt counts as disallow
- a per-host delay applies (Crawl-delay honoured)
- HTTP 401/403/405/429/451 and CAPTCHA walls are recorded as `blocked` and never bypassed
- a browser is used only when the static page yields no extractable records; it runs through
  the environment proxy, trusting the environment CA set by SPKI pin

**Domain adapters** (`domain.py`) supply the domain parts:
- tags and keywords
- attribute specs
- information needs
- discovery planning
- extractor, resolver and enricher
- funnel
- projection into the world model
- strategies

Housing (`acquisition/housing/`) is the first. Jobs or universities plug into the same seam.

Housing discovery:
1. Read live 1K/1DK market rents per ward, then choose areas: the two most affordable plus the
   ones nearest the median, with the rationale logged.
2. Navigate each portal from its entry page by anchor text, using beam search with
   backtracking. There are no URL templates.
3. Paginate and extract.

Funnel: ~1,500 mentions → ~1,100–1,400 units → ~270 pass (market-based cap, layout for household,
area, walk) → 25 → 8. The 8 are deep-researched:
- detail page
- 掲載元 operator page
- GSI geocode
- nearest stations from MLIT N02
- rail distance
- libraries and universities (MLIT P27/P29)
- hub-commute estimate
- move-in feasibility

Past a TTL, a belief is stale. The ACQUIRE phase then creates a recheck, which re-observes the
source and turns a 404/410 into `availability = false`.

Strategies compete on this world, and not signing is one of them:
- A: lease (with backups)
- B: monthly apartment
- C: share house
- D: hotel/hostel (a labelled prior; no live source)
- E: existing base (only if the world knows one)
- F: defer until the work location is known

Whether to sign at all is part of the decision.

**Replay** (`replay.py`): `export_fixtures` copies recorded pages and robots.txt into a
directory, and `ReplayTransport` serves them to the same `Fetcher`. The tests in
`tests/test_acquisition_replay.py` run discovery, resolution, the funnel and the loop on 24 real
pages captured from the web.

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
- **Hostel/hotel prices** have no live source yet, so route D is an explicit prior.

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
