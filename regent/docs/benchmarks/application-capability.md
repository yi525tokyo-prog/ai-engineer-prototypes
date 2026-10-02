# Application capability benchmark (coding worker run for real)

Started 2026-10-01T11:49:35Z; 638 s wall clock. Workspace `/home/user/ai-engineer-prototypes/regent/var/appbench-cold`.

Only sentences were given. Regent analysed each one, looked at the world, chose a route, and for the application routes delegated code to a file-only coding worker. Regent then built, tested, ran, browser-accepted, repaired, promoted and registered what came back, and later missions used that application. Every figure below was read back from Regent's own records ([`application-capability.json.gz`](application-capability.json.gz)); the code the worker delivered, with Regent's briefs and repair rounds, is in [`application-capability-app/`](application-capability-app).

## Result: PASS

- [x] software was found missing: 2 existing products competed and lost (Use Notion, Use Memos), building was selected
- [x] Regent designed the interface and its own acceptance scenarios before any code existed
- [x] construction was delegated to a coding worker: 2152 lines in 13 files, worker cost $0.66
- [x] Regent's checks failed the delivered software and it was repaired: 3 rounds before acceptance
- [x] Regent inspected, ran the worker's tests, started it, and accepted it only after its own API, browser (phone-sized), restart and negative scenarios passed
- [x] registered as a capability and a tool (`cap_lindy_reading_place`), live, the principal told where it is and where the passphrase is kept
- [x] later missions used it through its API with read-back checks (3 uses)
- [x] a need it could not meet extended it: v2 built on a copy of the real data, held to 8 regression scenarios of the version in use, promoted with a backup
- [x] no record of the version in use was lost (7 compared)
- [x] the shared link, opened by a stranger, shows that book's notes and nothing else
- [x] the live process was killed; Regent's maintenance pass brought it back on the same data

## M1 (build): “I read old books on LindyBooks on my phone and keep losing track of where I stopped and what I thought about them. I want my own private place, usable from any browser, where I can pick books from the LindyBooks catalogue and keep my place and my notes - not in some other company's app.”

- status **completed** after 4 ticks, 413.7 s
- need: software_capability / tool; requirements: `r1`(core), `r2`(core), `r3`(core, implied), `r4`(core), `r5`(core, implied), `r6`(supporting, implied)

| rank | route | status | score |
|---|---|---|---|
| 1 | Have the application built, then verify and run it | selected | 0.943 |
| 2 | Use Notion | alive | 0.646 |
| 3 | Use Memos | alive | 0.646 |
| 4 | Keep it by hand (a notes app) | alive | 0.53 |

Operations: `acquire.analyze` succeeded, `acquire.discover` succeeded, `acquire.inventory` succeeded, `software.design_app` succeeded, `software.build_app` succeeded

**software.build_app**

- worker sessions: 3 (initial + repairs); worker cost $0.66
- delivered: {"files": 13, "lines": 2152, "tests": ["test/app.test.js"], "languages": {".js": 3, ".md": 3, ".css": 1, ".html": 1, ".json": 5}}
- accepted: True; requirement coverage 1.0; live at `http://127.0.0.1:49247`
- round 0 failed Regent's checks (2):
    - tests: your tests failed (`node --test test/`, exit 1)
    - acceptance: scenario s4 (r4, negative) failed at step 2: AssertionError: shelf_list: HTTP 200, expected 401: {"books":[{"id":1,"catalogue_id":2680,"title":"Meditations","au
- round 1 failed Regent's checks (1):
    - acceptance: scenario s6 (r1 r2 r3 r5, browser) failed at step 21: TimeoutError: Page.wait_for_selector: Timeout 8000ms exceeded.
Call log:
  - waiting for locator("[data-te
- round 2: every check passed
- promotion: live=True port=49247 backup=None records carried over=None

- principal approved `software.build_app` (authorization interrupt: Approve or deny: Delegate, inspect, build, test, run, accept, repair, promote (software.build_app))
- interrupt [authorization, resolved]: Approve or deny: Delegate, inspect, build, test, run, accept, repair, promote (software.build_app)
- principal's active time: 104.0 s (open requests: 0.0 s)
- told the principal: lindy-reading-place v1 is running at http://127.0.0.1:49247 (on this machine only; reaching it from another device needs hosting you approve). Sign in with the passphrase Regent generated, kept in Regent's secret store as APP_LINDY_READING_PLACE_PASSPHRASE.
- afterwards: `lindy-reading-place` v1 usable, coverage 1.0, 13 endpoints, uses 0, data endpoints returned 0 item(s)

## M2 (use): “Put The Odyssey on my reading list - I'm on book 3 - and note that the Butler translation reads well.”

- status **completed** after 2 ticks, 23.7 s
- need: software_capability / action; requirements: `add_entry`(core), `record_progress`(core), `attach_note`(core), `confirm`(supporting, implied)
- existing capability judged **can_do**: lindy-reading-place (gaps: Confirmation message is not an endpoint but the client can compose it from the API responses; Adding requires the LindyBooks catalogue_id (found via search); the Odyssey must exist in the catalogue, and duplicates are prevented by catalogue_id uniqueness; Progress is free-text position (e.g. 'Book 3'), with no structured book 3/24 or in-progress status)

| rank | route | status | score |
|---|---|---|---|
| 1 | Do it with 'Lindy reading place' (already running) | selected | 1.206 |

Operations: `acquire.analyze` succeeded, `software.use_app` succeeded

**software.use_app**: 3 API calls, 2 read-back checks, passed=True

```
0: call shelf_add ok HTTP 201
1: call position_set ok HTTP 200
2: call note_add ok HTTP 201
3: call shelf_get ok HTTP 200
4: call note_list ok HTTP 200
```

- principal's active time: 31.5 s (open requests: 0.0 s)
- told the principal: The Odyssey (Homer) is now on your shelf, with your position set to "Book 3" and the note "The Butler translation reads well." attached. The shelf was empty, so nothing was duplicated. I used catalogue entry 3160, whose title is exactly "The Odyssey". The catalogue also lists entry 1727, a prose edition that is probably Butler's. If you want that edition on the shelf instead, tell me and I'll swap it.
- afterwards: `lindy-reading-place` v1 usable, coverage 1.0, 13 endpoints, uses 1, data endpoints returned 2 item(s)

## M2b (use): “Add Moby-Dick too: I'm at chapter 12, and the whale-anatomy chapters drag.”

- status **completed** after 2 ticks, 20.6 s
- need: software_capability / action; requirements: `add_book`(core), `record_progress`(core), `record_note`(supporting)
- existing capability judged **can_do**: lindy-reading-place (gaps: none)

| rank | route | status | score |
|---|---|---|---|
| 1 | Do it with 'Lindy reading place' (already running) | selected | 1.206 |

Operations: `acquire.analyze` succeeded, `software.use_app` succeeded

**software.use_app**: 3 API calls, 3 read-back checks, passed=True

```
0: call shelf_add ok HTTP 201
1: call position_set ok HTTP 200
2: call note_add ok HTTP 201
3: call shelf_get ok HTTP 200
4: call note_list ok HTTP 200
5: call shelf_list ok HTTP 200
```

- principal's active time: 18.0 s (open requests: 0.0 s)
- told the principal: Added "Moby-Dick; or, The Whale" (Melville) to your shelf alongside The Odyssey, which is unchanged. Position is set to Chapter 12. I attached a note that the whale-anatomy chapters drag. Resume link: https://www.gutenberg.org/ebooks/15.txt.utf-8
- afterwards: `lindy-reading-place` v1 usable, coverage 1.0, 13 endpoints, uses 2, data endpoints returned 4 item(s)

## M3 (extend): “I want to send a friend a link to my notes on one book, without them seeing anything else.”

- status **completed** after 4 ticks, 151.9 s
- need: software_capability / action; requirements: `r1`(core), `r2`(core), `r3`(core), `r4`(supporting), `r5`(supporting, implied)
- existing capability judged **extend**: lindy-reading-place (gaps: No endpoint to create a share link/token for a single book's notes; No public, unauthenticated read-only view; all current endpoints require passphrase login/session cookie; No scoped share-token resolution that returns only that book's notes and blocks access to the shelf list, other books, export or other links; No unguessable token generation; shelf ids are server-assigned and the notes endpoint needs auth; No revoke/list share links endpoint; Copyable URL for the share link is not provided (UI/response with full URL needed))

| rank | route | status | score |
|---|---|---|---|
| 1 | Extend 'Lindy reading place' (new version, same data) | selected | 0.82 |
| 2 | Build a separate application for this | alive | 0.547 |

Operations: `acquire.analyze` succeeded, `software.design_app` succeeded, `software.extend_app` succeeded

**software.extend_app**

- worker sessions: 1 (initial + repairs); worker cost $0.28
- delivered: {"files": 11, "lines": 2664, "tests": ["test/app.test.js"], "languages": {".js": 4, ".md": 1, ".css": 1, ".html": 2, ".json": 3}}
- accepted: True; requirement coverage 1.0; live at `http://127.0.0.1:49247`
- round 0: every check passed
- promotion: live=True port=49247 backup=/home/user/ai-engineer-prototypes/regent/var/appbench-cold/apps/lindy-reading-place/data/backups/live-before-v2-1790855987 records carried over=7

- principal approved `software.extend_app` (authorization interrupt: Approve or deny: Build, test on real data, promote or roll back (software.extend_app))
- interrupt [authorization, resolved]: Approve or deny: Build, test on real data, promote or roll back (software.extend_app)
- principal's active time: 48.5 s (open requests: 0.0 s)
- told the principal: lindy-reading-place v2 is running at http://127.0.0.1:49247 (on this machine only; reaching it from another device needs hosting you approve). Sign in with the passphrase Regent generated, kept in Regent's secret store as APP_LINDY_READING_PLACE_PASSPHRASE.
- afterwards: `lindy-reading-place` v2 usable, coverage 1.0, 17 endpoints, uses 2, data endpoints returned 4 item(s)

## M4 (use v2): “Make me a link I can send my friend to my notes on The Odyssey.”

- status **completed** after 2 ticks, 21.9 s
- need: software_capability / action; requirements: `r1`(core), `r2`(core), `r3`(core), `r4`(supporting)
- existing capability judged **can_do**: lindy-reading-place (gaps: Nothing blocking: find the Odyssey via shelf_list (or search + shelf_add if not yet shelved), check notes with note_list, then share_create and give the link.; Unknown whether the share response returns a full browser-viewable URL or only a token. shared_get is an /api JSON path, so a human-readable page for the friend is unconfirmed.; Share scope is one book, read-only and public by token. The docs don't say whether expiry exists, so the principal should be told it is non-expiring and revocable via share_revoke.; Notes are shared per book, not per note. Sharing only selected notes is not supported.)

| rank | route | status | score |
|---|---|---|---|
| 1 | Do it with 'Lindy reading place' (already running) | selected | 1.206 |

Operations: `acquire.analyze` succeeded, `software.use_app` succeeded

**software.use_app**: 1 API calls, 1 read-back checks, passed=True

```
0: call share_create ok HTTP 201
1: call shared_get ok HTTP 200
```

- principal's active time: 22.5 s (open requests: 0.0 s)
- told the principal: Here is your share link for your notes on The Odyssey (Homer): http://127.0.0.1:49247/shared/GPAHv5owh8V5OhCL4tsqM6mWBpSiq3ja

It shares the one note you have on that book: "The Butler translation reads well." (Book 3). Nothing from Moby-Dick or any other book is included.

Anyone who has the link can view it without logging in. It is read-only, so they can't edit anything. The page shows only the book's title, author and notes. I haven't seen any expiry on the link. It stays valid until you revoke it, and I can revoke it if you want.

I assumed "my notes on The Odyssey" means the notes on the shelf copy of The Odyssey, since it's the only Odyssey on your shelf.
- afterwards: `lindy-reading-place` v2 usable, coverage 1.0, 17 endpoints, uses 3, data endpoints returned 4 item(s)

## The shared link, opened by a stranger

- link: `http://127.0.0.1:49247/shared/GPAHv5owh8V5OhCL4tsqM6mWBpSiq3ja`
- shows the shared notes (Butler): **True**
- leaks the other book (Moby-Dick): **False**

## Lifecycle: the live process is killed

- killed: ['lindy-reading-place']; maintenance pass: ['lindy-reading-place: restarted']
- afterwards running: lindy-reading-place v2

## Totals

- principal's active time across missions: 224 s (typing five sentences at 40 wpm, plus two 20-second approvals of the coding agent)
- coding-worker spend: $0.94

## What this does not show

- **Hosting.** "Usable from any browser" on the principal's phone needs public hosting: a domain, TLS and a host account. That is an identity and payment decision, and it was not taken. The application runs on this machine, and Regent told the principal so.
- **One browser engine.** Browser acceptance ran in Chromium at a phone-sized viewport only. The requirement coverage figure counts the scenarios that ran; it does not count engines that were never tried.
- **Edition choice.** For "the Butler translation", Regent chose the catalogue entry titled exactly "The Odyssey" and said it could not confirm the translator. It offered to swap in entry 1727, which is in fact Butler's. It was honest about the uncertainty, but it did not resolve it.
- **Approvals are simulated.** The benchmark resolves the two authorization interrupts on the principal's behalf, following the instruction to run the coding worker for real. They are counted at 20 seconds each.

