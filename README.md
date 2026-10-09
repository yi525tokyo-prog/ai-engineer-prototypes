# ai-engineer-prototypes — for Momentum Consulting Group (AI Engineer, Claude & Copilot) application

Three small, self-contained, actually-runnable prototypes mapped directly to
the job's "what you'll be involved in" list. No paid API keys required —
each one runs with `python3` and the standard library only, so anyone can
clone and verify them in under a minute.

| # | Prototype | Maps to |
|---|---|---|
| 1 | `01_agent_framework/` | "Designing and building AI agents using Claude Enterprise" + "reusable AI services and enterprise frameworks" |
| 2 | `02_prompt_governance/` | "Establishing best practice, governance and standards for enterprise AI" |
| 3 | `03_model_router/` | "Working with Microsoft Copilot to accelerate business capability" + integrating AI across enterprise applications |

## 1. Agent Framework

A minimal, reusable scaffold for a tool-using agent: a bounded think →
act → observe loop with every step written to a structured trace, plus
two example enterprise tools (internal policy lookup, IT/HR ticket
creation). Ships with a real Claude Messages API call (tool-use enabled)
for the production path, and a deterministic rule-based planner as a
zero-key fallback so the full loop — tool selection, execution, multi-turn
reasoning — is runnable and verifiable standalone.

```
cd 01_agent_framework
python3 demo.py
```

Sample trace (one of four):
```
> My laptop screen is broken, can you log a ticket for IT?
  answer: Logged ticket TCK-0001 (priority: medium). Someone will follow up.
  trace:
    - {'step': 'tool_call', 'tool': 'create_ticket', 'args': {...}}
    - {'step': 'tool_result', 'tool': 'create_ticket', 'result': {'id': 'TCK-0001', ...}}
    - {'step': 'final_answer', 'content': 'Logged ticket TCK-0001 ...'}
```

## 2. Prompt Governance

A versioned prompt registry (id, version, owner, last-reviewed) plus an
eval harness that runs fixed test cases against each template and checks
the output against declared policy rules — length limits, banned/required
terms, PII leakage — before anything ships. This is the automatable half
of an enterprise AI governance process: regression-test prompts the same
way you'd regression-test code.

```
cd 02_prompt_governance
python3 eval_harness.py
```

Sample output — the harness actually catches a real violation, it's not just a rubber stamp:
```
=== customer_support_reply (v1.2.0, owner: ai-coe@company.example) ===
  [FAIL] tc1: My order #4821 hasn't arrived after 2 weeks, I wan...
      x must_not_include: found banned terms: ['refund']
  [PASS] tc2: The app keeps crashing on Android 14, can you help...

3/4 test cases passed -- see eval_report.json
```

## 3. Model Router

Routes an incoming task to a Claude lane or a Copilot lane through a
common adapter interface, so calling code never depends on which provider
sits behind either one — swap or add a backend without touching the
router or its callers. The classifier is rule-based rather than a model
call, so routing is instant, free, and its reasoning is auditable; every
decision is logged for review.

```
cd 03_model_router
python3 demo_tasks.py
```

Sample output:
```
> Refactor this function to remove the nested loop and add a unit test.
  lane: copilot  (3 code-shaped signal(s) matched)
> Draft an email to a customer explaining a shipping delay.
  lane: claude  (2 conversational signal(s) matched)
> What's our policy on international travel expenses?
  lane: claude  (1 conversational signal(s) matched)
```

## 4. Regent — an operational principal

[`regent/`](regent/) is a full monorepo, not a single-file prototype. It keeps an event-sourced
model of your world (PostgreSQL), generates *competing* strategies and scores them with its own
framework. It executes permitted work through real tools (Playwright, pytest, a sandboxed
filesystem, mail, calendar and more) and asks the human only for bounded actions such as a
CAPTCHA. It then verifies the results and replans when the evidence changes. It comes with a
Next.js operational cockpit, a seeded case study and 39 tests. See
[regent/README.md](regent/README.md).

---

All three were built and run-verified for this application — happy to
walk through any of them, or build against a real Claude Enterprise /
Copilot sandbox if that's more useful to see.
