"""Seeded case study: "the freelancer's week".

The principal has limited money (under one month of runway), one upcoming
client appointment, one software project (with genuinely failing tests),
several unanswered messages, an uncertain working location (home ISP outage)
and several possible strategic routes. Everything is ingested as ordinary
events; nothing in the core loop knows about this scenario.

A simulated client portal (served by the API) guards the meeting confirmation
with a CAPTCHA. Regent's browser detects it and raises a bounded human
interrupt; when the principal clears it, Regent resumes, reads the portal and
learns the client's budget is frozen -- evidence that changes the plan.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from regent.config import settings
from regent.connectors import store
from regent.connectors.services import LocalSearchIndex
from regent.core.authority.manager import AuthorityManager
from regent.core.constitution.model import ConstitutionModel
from regent.core.goals.missions import MissionGraph
from regent.core.observe.events import EventStore
from regent.core.treasury.treasury import Treasury
from regent.sim.ledgerline_src import FILES

JST = timezone(timedelta(hours=9))
PORTAL_CODE = "7KQ2"
ROOT_MISSION = "mis_runway"
WORK_MISSION = "mis_workplace"


def _at(days: int, hour: int, minute: int = 0) -> str:
    d = datetime.now(JST) + timedelta(days=days)
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


def write_project(workspace: Path) -> Path:
    root = workspace / "ledgerline"
    for rel, src in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root, check=False)
        subprocess.run(["git", "-c", "user.email=regent@local", "-c", "user.name=regent", "add", "-A"], cwd=root,
                       check=False)
        subprocess.run(["git", "-c", "user.email=regent@local", "-c", "user.name=regent", "commit", "-qm",
                        "ledgerline 0.3.0"], cwd=root, check=False)
    return root


def seed_connectors() -> None:
    places = [
        ("place_home", "Home (Koenji)", 35.7054, 139.6497),
        ("place_kinoshita_office", "Kinoshita Design Studio (Shibuya)", 35.6595, 139.7005),
        ("place_cowork", "Shibuya Cowork Hub", 35.6581, 139.7017),
        ("place_friend_office", "Aoi's office (Nakameguro)", 35.6440, 139.6989),
    ]
    for pid, name, lat, lng in places:
        store.put("maps", "place", {"place_id": pid, "name": name, "lat": lat, "lng": lng}, rid=f"map_{pid}")
    idx = LocalSearchIndex()
    gigs = [
        ("Python contract: bank CSV importer (4 weeks, remote)", "Fixed-scope python contract, remote, short engagement."),
        ("Remote Python/FastAPI developer, 6-week contract", "Short contract, python fastapi, remote friendly."),
        ("Freelance data pipeline cleanup (python, 3 weeks)", "Freelance python contract, remote, pandas."),
        ("Short remote contract: Django admin fixes", "python django freelance contract short remote."),
        ("Python scraping tool, fixed price", "freelance python contract remote fixed price short."),
        ("Contract: internal CLI tooling (python)", "short python contract remote CLI tooling freelance."),
        ("Remote short contract: test suite hardening", "python pytest contract freelance remote short."),
    ]
    for i, (t, x) in enumerate(gigs):
        idx.index(f"https://codemarket.example/gigs/{i}", t, x, ["gig", "codemarket"])
    comps = [
        ("hledger - plain text accounting", "Open-source plain text accounting, reconciliation, CLI."),
        ("Beancount + Fava", "Plain text accounting ledger with web UI, importers."),
        ("Ledger CLI", "Command line double-entry accounting, plain text ledger."),
        ("Freee (JP) bookkeeping SaaS", "Japanese bookkeeping, bank reconciliation, pricing plans for freelancers."),
    ]
    for i, (t, x) in enumerate(comps):
        idx.index(f"https://example.org/ledger-tools/{i}", t, x, ["ledger", "alternatives", "pricing", "accounting"])
    store.put("sim_portal", "portal", {
        "slug": "kinoshita", "captcha_solved": False, "slot": _at(3, 14), "slot_status": "pending",
        "budget_status": "frozen", "meeting_format": "remote", "revised_value": 150000,
        "client_note": ("Thanks for confirming. Honest heads-up: our Q4 budget was frozen last week. "
                        "We can still meet (remote is easier), but realistically we can only fund a "
                        "small pilot (~JPY 150,000) starting in January."),
    }, rid="portal_kinoshita")


def seed_world(db: Session, api_url: str) -> None:
    ev = EventStore(db)
    E = lambda **p: ev.append("entity_upserted", p, source="seed")  # noqa: E731

    E(id="user", kind="person", name="You", attrs={"role": "principal", "occupation": "freelance developer"})
    for pid, name, extra in [
        ("place_home", "Home (Koenji)", {"is_home": True, "workplace": True, "cost_per_day": 0, "reliability": 0.9,
                                         "productive_hours": 30, "thesis": "Free and zero commute, if the internet works."}),
        ("place_cowork", "Shibuya Cowork Hub", {"workplace": True, "cost_per_day": 2200, "reliability": 0.95,
                                                "productive_hours": 30, "commute_hours": 0.8,
                                                "thesis": "Reliable and next to the client, but costs cash."}),
        ("place_friend_office", "Aoi's office (Nakameguro)", {"workplace": True, "cost_per_day": 0,
                                                              "reliability": 0.9, "productive_hours": 30,
                                                              "commute_hours": 0.7, "offer_message": "msg_aoi",
                                                              "reply_points": ["Could I come Tuesday to Friday?"],
                                                              "thesis": "Free desk offered by a friend; availability unconfirmed."}),
        ("place_kinoshita_office", "Kinoshita Design Studio (Shibuya)", {}),
    ]:
        E(id=pid, kind="place", name=name, attrs=extra)
    ev.append("user_moved", {"place_id": "place_home"}, source="seed")

    E(id="org_kinoshita", kind="organization", name="Kinoshita Design Studio", attrs={"industry": "design"},
      relations=[{"rel": "located_at", "dst": "place_kinoshita_office"}])
    E(id="person_kinoshita", kind="person", name="Haruka Kinoshita",
      attrs={"email": "haruka@kinoshita.example", "known_contact": True},
      relations=[{"rel": "works_for", "dst": "org_kinoshita"}])
    E(id="org_northbridge", kind="organization", name="Northbridge Talent", attrs={"industry": "recruiting"})
    E(id="person_ortiz", kind="person", name="Daniel Ortiz", attrs={"email": "d.ortiz@northbridge.example",
                                                                    "known_contact": True},
      relations=[{"rel": "works_for", "dst": "org_northbridge"}])
    E(id="person_aoi", kind="person", name="Aoi Tanaka", attrs={"email": "aoi@example.jp", "known_contact": True,
                                                                "relationship": "friend"})
    E(id="person_sato", kind="person", name="Mr. Sato (landlord)", attrs={"email": "sato.estate@example.jp",
                                                                          "known_contact": True})
    E(id="service_codemarket", kind="service", name="CodeMarket",
      attrs={"category": "marketplace", "typical_monthly": 150000, "base_probability": 0.5, "hours": 60,
             "currency": "JPY", "search_query": "freelance python contract remote short",
             "skills": ["Python", "FastAPI", "data pipelines", "testing"]})
    E(id="account_bank", kind="account", name="Checking account", attrs={"institution": "Local bank"},
      relations=[{"src": "user", "rel": "owns", "dst": "account_bank"}])
    E(id="project_ledgerline", kind="project", name="ledgerline",
      attrs={"path": "ledgerline", "monetizable": True, "potential_value": 2000000, "base_probability": 0.12,
             "launch_hours": 80, "launch_cost": 8000, "beta_price": 1500,
             "market_query": "ledger accounting alternatives pricing",
             "pitch": "Bank-CSV import and reconciliation for Japanese freelancers."},
      relations=[{"src": "user", "rel": "owns", "dst": "project_ledgerline"}])

    ev.append("calendar_event_changed", {
        "id": "event_kinoshita_meeting", "title": "Kinoshita Design: scope meeting + ledgerline demo",
        "start": _at(3, 14), "end": _at(3, 15), "location_id": "place_kinoshita_office",
        "attendees": ["user", "person_kinoshita"]}, source="seed")

    ev.append("email_received", {
        "id": "msg_kinoshita", "from": {"id": "person_kinoshita", "name": "Haruka Kinoshita"},
        "subject": "Thursday meeting + ledgerline demo?", "related_to": ["contract_kinoshita", "event_kinoshita_meeting"],
        "body": ("Hi! Looking forward to Thursday. Could you confirm the slot in our client portal and let me know "
                 "if you can show the ledgerline reconciliation demo? - Haruka")}, source="seed")
    ev.append("email_received", {
        "id": "msg_northbridge", "from": {"id": "person_ortiz", "name": "Daniel Ortiz"},
        "subject": "3-month Python contract - interview slot?", "deadline": _at(4, 18),
        "related_to": ["contract_northbridge"],
        "body": ("A fintech client needs a Python engineer for 3 months, full time (JPY 380k/month). You'd need to "
                 "pause side projects. Can you do a technical interview this week? I need an answer by Friday.")},
        source="seed")
    ev.append("email_received", {
        "id": "msg_sato", "from": {"id": "person_sato", "name": "Mr. Sato (landlord)"},
        "subject": "Rent and renewal", "related_to": ["commitment_rent"],
        "body": "Reminder: next month's rent (JPY 95,000) is due on the 25th, and the renewal paperwork is ready."},
        source="seed")
    ev.append("email_received", {
        "id": "msg_aoi", "from": {"id": "person_aoi", "name": "Aoi Tanaka"}, "subject": "Spare desk this week",
        "related_to": ["place_friend_office"],
        "body": "Heard your internet is down - there's a spare desk at our office this week if you want it!"},
        source="seed")

    E(id="contract_kinoshita", kind="contract", name="Kinoshita Design: ledger tooling project",
      attrs={"status": "prospective", "value": 600000, "currency": "JPY", "base_probability": 0.55,
             "prep_hours": 10, "counterparty": "org_kinoshita", "contact": "person_kinoshita",
             "message": "msg_kinoshita", "appointment": "event_kinoshita_meeting",
             "requires_demo_of": "project_ledgerline", "portal_url": f"{api_url}/sim/portal/kinoshita",
             "reversibility": 0.6, "optionality": 0.6, "risk": 0.35,
             "reply_points": ["Confirmed for Thursday 14:00.", "I'll demo ledgerline's import + reconciliation flow."],
             "thesis_note": "Uses and funds ledgerline directly."},
      relations=[{"rel": "related_to", "dst": "project_ledgerline"}, {"rel": "related_to", "dst": "org_kinoshita"}])
    E(id="contract_northbridge", kind="contract", name="Northbridge: 3-month fintech contract",
      attrs={"status": "offered", "monthly_value": 380000, "duration_months": 3, "currency": "JPY",
             "base_probability": 0.5, "exclusive": True, "pauses_project": "project_ledgerline", "prep_hours": 4,
             "counterparty": "org_northbridge", "contact": "person_ortiz", "message": "msg_northbridge",
             "requires_demo_of": None, "deadline": _at(4, 18), "reversibility": 0.2, "optionality": 0.15,
             "risk": 0.3, "reply_points": ["Yes - I'm available for a technical interview this week.",
                                           "Wednesday or Thursday morning works best."]},
      relations=[{"rel": "related_to", "dst": "org_northbridge"}])
    E(id="commitment_rent", kind="commitment", name="Monthly rent",
      attrs={"amount": 95000, "due": _at(26, 9), "negotiable": True, "deferrable_amount": 95000,
             "counterparty": "person_sato", "message": "msg_sato",
             "ask_points": ["Could I pay half on the 25th and the rest on the 10th?"]},
      relations=[{"src": "user", "rel": "pays", "dst": "person_sato"}])

    tr = Treasury(db)
    tr.set_resource("res_cash", "money", "Cash (JPY)", "JPY", 180000, attrs={"monthly_burn": 210000}, source="seed")
    tr.set_resource("res_api", "api_spend", "Model/API budget", "USD", 20.0, limit=20.0, source="seed")
    tr.set_resource("res_attention", "attention", "Principal attention (min/day)", "min", 30, limit=30, source="seed")
    tr.set_resource("res_compute", "compute", "Local compute", "cpu-min", 600, limit=600, source="seed")

    ev.append("fact_observed", {"key": "place.place_home.internet_ok", "value": False, "confidence": 0.9,
                                "source": "ISP status page: outage in Suginami, ETA unknown"}, source="isp_status")

    cm = ConstitutionModel(db)
    cm.upsert(id="pref_keep_project", type="strong_preference", statement="Keep ledgerline alive",
              dimension="tag:keeps_project_alive", direction=1.0, confidence=0.8,
              source={"kind": "explicit", "note": "stated during setup"})
    cm.upsert(id="pref_no_pause", type="strong_preference", statement="Avoid pausing ledgerline",
              dimension="tag:pauses_project", direction=-1.0, confidence=0.8,
              source={"kind": "inferred", "note": "rejected two full-time offers last quarter"})
    cm.upsert(id="pref_optionality", type="strong_preference", statement="Prefer high optionality",
              dimension="optionality", direction=1.0, confidence=0.82,
              source={"kind": "inferred", "note": "repeatedly chose flexible engagements"})
    cm.upsert(id="priority_runway", type="priority", statement="Cash runway is this quarter's priority",
              dimension="expected_upside", direction=1.0, confidence=0.75, rule={"scope_tags": ["income"]},
              source={"kind": "explicit"})
    cm.upsert(id="hard_no_debt", type="hard_constraint", statement="Take on no new debt",
              rule={"forbid_tag": "debt"}, confidence=0.99, source={"kind": "explicit"})
    cm.upsert(id="weak_low_travel", type="weak_preference", statement="Minimise commuting",
              dimension="time_cost_hours", direction=1.0, confidence=0.6, source={"kind": "inferred"})

    am = AuthorityManager(db, _tools())
    am.grant("email.send", "COMMIT", {"recipients": "known_contacts"}, note="replies to known contacts",
             grant_id="grant_email_known")
    am.grant("commerce.purchase", "COMMIT", {"max_amount": 3000}, note="small purchases under JPY 3,000",
             grant_id="grant_small_purchases")
    am.grant("calendar.invite", "COMMIT", {}, granted=False, note="never send invites without asking",
             grant_id="grant_no_invites")

    for cap, desc, attrs in [
        ("invoice.generate", "Produce invoices for clients", {"purchase_cost": 1200, "purchase_vendor": "InvoiceNow SaaS"}),
        ("payments.checkout", "Accept online payments for a product", {"web_url": "https://dashboard.stripe.com/register",
                                                                        "setup_cost": 0}),
    ]:
        ev.append("capability_changed", {"id": cap, "name": cap, "description": desc, "status": "missing",
                                         "provided_by": [], "attrs": attrs}, source="seed")


def _tools():
    from regent.runtime import get_services

    return get_services().tools


def seed_missions(db: Session) -> tuple[str, str]:
    g = MissionGraph(db)
    root = g.create(
        mission_id=ROOT_MISSION, title="Stabilise the next 90 days",
        objective=("Secure at least three months of runway within 90 days without abandoning ledgerline, "
                   "and keep every urgent relationship in good standing."),
        success_criteria=[
            {"id": "runway", "statement": "Runway of 3+ months secured",
             "condition": {"fact": "treasury.runway_months", "op": "ge", "value": 3}},
            {"id": "client", "statement": "Kinoshita's message answered",
             "condition": {"fact": "message.msg_kinoshita.answered", "op": "eq", "value": True}},
            {"id": "recruiter", "statement": "Northbridge answered before Friday",
             "condition": {"fact": "message.msg_northbridge.answered", "op": "eq", "value": True}},
        ],
        tags=["income", "runway"], value_scale=650000, horizon_days=90, source="seed")
    g.create(
        mission_id=WORK_MISSION, parent_id=root.id, title="Secure a reliable place to work this week",
        objective="Have a reliable, affordable workplace for the next 5 working days.",
        success_criteria=[{"id": "place", "statement": "A reliable workplace is confirmed",
                           "any": [{"fact": "place.place_friend_office.available", "op": "eq", "value": True},
                                   {"fact": "place.place_cowork.booked", "op": "exists"},
                                   {"fact": "place.place_home.internet_ok", "op": "eq", "value": True}]}],
        tags=["workplace"], value_scale=30, horizon_days=7, attrs={"days": 5}, source="seed")
    return ROOT_MISSION, WORK_MISSION


def seed(db: Session, api_url: str | None = None) -> dict[str, Any]:
    api_url = (api_url or settings.public_api_url).rstrip("/")
    settings.workspace.mkdir(parents=True, exist_ok=True)
    write_project(settings.workspace)
    seed_connectors()
    seed_world(db, api_url)
    root, work = seed_missions(db)
    db.commit()
    return {"missions": [root, work], "portal": f"{api_url}/sim/portal/kinoshita", "workspace": str(settings.workspace)}


# ---------------------------------------------------------------- portal

def portal_state(slug: str) -> dict[str, Any] | None:
    return store.get(f"portal_{slug}")


def portal_verify(slug: str, code: str) -> bool:
    st = portal_state(slug)
    if st is None or code.strip().upper() != PORTAL_CODE:
        return False
    store.put("sim_portal", "portal", {"captcha_solved": True, "slot_status": "confirmed"}, rid=f"portal_{slug}")
    return True


# ------------------------------------------------------ scripted world events

SCRIPT: dict[str, dict[str, Any]] = {
    "aoi_confirms_desk": {
        "label": "Aoi confirms the desk (Tue-Fri)",
        "events": [
            {"type": "email_received", "payload": {
                "id": "msg_aoi_2", "from": {"id": "person_aoi", "name": "Aoi Tanaka"}, "subject": "Re: Spare desk",
                "requires_reply": False, "body": "Tuesday to Friday is perfect, see you then!"}},
            {"type": "fact_observed", "payload": {"key": "place.place_friend_office.available", "value": True}},
        ]},
    "isp_restored": {
        "label": "Home internet restored",
        "events": [{"type": "fact_observed", "payload": {"key": "place.place_home.internet_ok", "value": True}}]},
    "northbridge_withdraws": {
        "label": "Northbridge withdraws the offer",
        "events": [
            {"type": "email_received", "payload": {
                "id": "msg_northbridge_2", "from": {"id": "person_ortiz", "name": "Daniel Ortiz"},
                "subject": "Re: 3-month Python contract", "requires_reply": False,
                "body": "Apologies - the client filled the role internally. The offer is withdrawn."}},
            {"type": "fact_observed", "payload": {"key": "contract.contract_northbridge.offer_open", "value": False}},
        ]},
    "client_payment": {
        "label": "Old invoice paid (+JPY 120,000)",
        "events": [{"type": "budget_changed", "payload": {"id": "res_cash", "delta": 120000}}]},
}


def apply_script(db: Session, key: str) -> list[int]:
    item = SCRIPT[key]
    ev = EventStore(db)
    out = []
    for e in item["events"]:
        out.append(ev.append(e["type"], e["payload"], source=f"world:{key}").seq)
    db.commit()
    return out
