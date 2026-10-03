"""Software needs as a domain of the Regent loop.

The loop's ACQUIRE phase asks this adapter what it does not know yet:

1. ``analyze``   -- what does the principal's sentence actually need? (need analysis)
2. ``discover``  -- where is the subject, what runs it, what does it publish and promise?
3. ``inventory`` -- which sources could answer, with what access, meaning and constraints?
4. ``observe``   -- keep capabilities this mission relies on current (their refresh period)

The first three block route generation: comparing "compose / add a credential / add analytics
/ instrument the product / delegate / do it by hand" is meaningless before Regent knows what
exists. Routes then compete in the ordinary evaluator; the product's public commitments enter
the constitution as hard constraints; the selected route's operations run through the
``software`` tool; the human is only asked for what only the human has.

A mission is this adapter's if it says so by tag, or if no other adapter claims it and the
need analysis says it is about software (the analysis runs once, for untagged missions only).
"""

from __future__ import annotations

from datetime import datetime

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from regent.acquisition.domain import DomainAdapter
from regent.acquisition.tables import AcqRequest
from regent.core.observe.events import EventStore
from regent.ids import utcnow
from regent.software import capability as K
from regent.software import secrets


def latest_plan(s: Session, mission_id: str, goal: str) -> dict[str, Any] | None:
    r = s.scalar(select(AcqRequest).where(AcqRequest.mission_id == mission_id, AcqRequest.domain == "software",
                                          AcqRequest.goal == goal, AcqRequest.status == "done")
                 .order_by(AcqRequest.created_at.desc()).limit(1))
    return r.plan if r is not None else None


def _done(s: Session, mission_id: str, goal: str) -> AcqRequest | None:
    return s.scalar(select(AcqRequest).where(AcqRequest.mission_id == mission_id, AcqRequest.domain == "software",
                                             AcqRequest.goal == goal, AcqRequest.status.in_(("done", "failed")))
                    .order_by(AcqRequest.created_at.desc()).limit(1))


class SoftwareAdapter(DomainAdapter):
    name = "software"
    # reuse, build, use-existing and by-hand routes are the considered set; when reuse fails the
    # world is examined again and the full set is generated
    routes_are_complete = True
    tags = ("software_need",)
    keywords = ()

    # ------------------------------------------------------------- intake

    def claims(self, mission: Any) -> bool:
        attrs = (mission.get("attrs") if isinstance(mission, dict) else getattr(mission, "attrs", None)) or {}
        tags = (mission.get("tags") if isinstance(mission, dict) else getattr(mission, "tags", None)) or []
        need = attrs.get("need")
        if need is not None:
            return need.get("handled_as") == "software_capability"
        if tags or (mission.get("success_criteria") if isinstance(mission, dict)
                    else getattr(mission, "success_criteria", None)):
            return False
        # a bare sentence nobody has planned yet: find out what it needs
        s = None if isinstance(mission, dict) else object_session(mission)
        if s is not None:
            from regent.db import Route

            if s.scalar(select(Route.id).where(Route.mission_id == mission.id).limit(1)) is not None:
                return False
        return True

    # -------------------------------------------------------------- needs

    def information_needs(self, mission, world, state) -> list[dict[str, Any]]:
        s = object_session(mission)
        need = (mission.attrs or {}).get("need")
        sentence = mission.objective or mission.title
        if need is None:
            paused = (mission.attrs or {}).get("paused")
            if s is not None and _done(s, mission.id, "analyze") is not None:
                if not paused or (utcnow() - datetime.fromisoformat(paused["since"])).total_seconds() < 300:
                    return []
                m_attrs = dict(mission.attrs or {})
                m_attrs.pop("paused", None)
                mission.attrs = m_attrs
            return [{"action": "analyze", "params": {"sentence": sentence}, "blocking": True, "priority": 6,
                     "reason": "the principal's sentence has not been analysed into information needs"}]
        if need.get("handled_as") != "software_capability" or s is None:
            return []
        out = []
        disc = _done(s, mission.id, "discover")
        inv = _done(s, mission.id, "inventory")
        reusing = bool(need.get("reuse")) and not self._reuse_failed(s, mission)
        if reusing:
            pass                     # an existing capability answers: no need to look at the world again
        elif disc is None:
            out.append({"action": "discover", "params": {}, "blocking": True, "priority": 5,
                        "reason": "unknown where " + ", ".join(x["name"] for x in need.get("subjects", []))
                                  + " runs, what it publishes and what it promises"})
        elif inv is None or inv.created_at < disc.created_at:
            out.append({"action": "inventory", "params": {}, "blocking": True, "priority": 5,
                        "reason": "unknown which sources could answer the need and on what terms"})
        self._observe_credentials(s, mission)
        due = [c for c in s.scalars(select(K.SwCapability).where(K.SwCapability.mission_id == mission.id,
                                                                 K.SwCapability.status.in_(("usable", "degraded"))))
               if c.implementation != "application" and (K.due(c) or K.delivery_due(c))]
        if due:
            out.append({"action": "observe", "params": {"capabilities": [c.id for c in due]}, "blocking": False,
                        "priority": 1, "reason": f"{len(due)} capability(ies) past their refresh period"})
        return out

    @staticmethod
    def _reuse_failed(s: Session, mission) -> bool:
        from regent.db import Route

        return s.scalar(select(Route.id).where(Route.mission_id == mission.id, Route.archetype == "reuse",
                                               Route.status.in_(("failed", "invalidated"))).limit(1)) is not None

    def _observe_credentials(self, s: Session, mission) -> None:
        """A credential the principal provided (through an interrupt or the environment) becomes a
        world fact -- never its value -- so waiting routes and interrupts can resume."""
        from regent.db import Fact

        inv = latest_plan(s, mission.id, "inventory") or {}
        for p in inv.get("platform_sources", []):
            for cred in p.get("credentials", []):
                if secrets.present(cred) and s.get(Fact, f"credential.{cred}") is None:
                    EventStore(s).append("fact_observed", {"key": f"credential.{cred}", "value": "present",
                                                           "confidence": 1.0, "source": "regent:secrets"},
                                         source="regent", mission_id=mission.id)

    # ------------------------------------------------------------ actions

    def run_custom(self, action: str, s: Session, req: AcqRequest, params: dict[str, Any],
                   mission_id: str | None, transport=None) -> dict[str, Any]:
        from regent.db import Mission

        m = s.get(Mission, mission_id) if mission_id else None
        log = []

        def note(stage: str, message: str, **data: Any) -> None:
            log.append({"at": utcnow().isoformat(), "stage": stage, "message": message, **data})

        stats: dict[str, Any] = {}
        if m is not None and (m.attrs or {}).get("fresh"):
            from regent.software.reasoner import FRESH

            FRESH.add(m.id)                 # a redo survives a restart of Regent too
        if action == "analyze":
            from regent.software.need import analyze

            need = analyze(params.get("sentence") or (m.objective if m else ""), mission_id=mission_id)
            if (need.get("analysis") or {}).get("reasoner_error") and m is not None:
                # it could not understand the request: wait and try again, never guess
                m.attrs = {**(m.attrs or {}), "paused": {"why": "Regent's reasoning service did not answer: "
                                                         + need["analysis"]["reasoner_error"][:200],
                                                         "since": utcnow().isoformat()}}
                req.plan = {"paused": True}
                note("analyze", "reasoning worker unavailable; will retry")
                req.log = list(req.log or []) + log
                return {"funnel": {"paused": True}}
            tz = (m.attrs or {}).get("timezone") if m is not None else None
            need["principal"] = {**_principal_hint(need["sentence"]), **({"timezone": tz} if tz else {})}
            if need.get("handled_as") == "software_capability":
                from regent.software.reuse import match

                need["reuse"] = match(s, need, mission_id=mission_id)
                if need["reuse"]:
                    note("analyze", "existing capabilities answer this: "
                                    + ", ".join(f"{x['slug']} (judged by {x['judged_by']})" for x in need["reuse"]))
            req.plan = need
            if m is not None:
                self._adopt(s, m, need)
            stats = {"handled_as": need.get("handled_as"), "questions": len(need.get("questions", [])),
                     "subjects": [x["name"] for x in need.get("subjects", [])]}
            note("analyze", f"need: {need.get('handled_as')}; subjects {stats['subjects']}; "
                            f"{stats['questions']} question(s); deliverable {need.get('deliverable', {}).get('form')}")
        elif action == "discover":
            from regent.software.discover import resolve

            need = (m.attrs or {}).get("need") or {}
            res = resolve(s, need, request_id=req.id, transport=transport, log=note)
            req.plan = res
            self._project_discovery(s, m, res)
            stats = {"subjects": [{"name": x["name"], "deployments": [d["host"] for d in x.get("deployments", [])],
                                   "probes": x.get("probes")} for x in res["subjects"]]}
        elif action == "inventory":
            from regent.software.discover import inventory, inventory_tool

            need = (m.attrs or {}).get("need") or {}
            res = latest_plan(s, m.id, "discover") or {}
            fn = inventory_tool if need.get("need_type") in ("tool", "action") else inventory
            inv = fn(s, need, res, mission_id=mission_id, log=note, request_id=req.id, transport=transport)
            req.plan = inv
            self._project_inventory(s, m, need, inv)
            stats = {"public_fields": sum(1 for f in inv.get("public_fields", []) if f["relation_to_need"] != "unrelated"),
                     "platform_sources": len(inv.get("platform_sources", [])), "constraints": len(inv["constraints"]),
                     "app_sources": len(inv.get("app_sources", [])), "alternatives": len(inv.get("alternatives", [])),
                     "existing_capabilities": len(inv["existing_capabilities"])}
        elif action == "observe":
            events = EventStore(s)
            n = 0
            for cid in params.get("capabilities") or []:
                cap = s.get(K.SwCapability, cid)
                if cap is None or cap.status not in ("usable", "degraded"):
                    continue
                obs = K.collect(s, cap)
                r = K.read(s, cap, count_use=False)
                events.append("fact_observed", {"facts": K.facts(cap, r)}, source=f"capability:{cap.slug}",
                              mission_id=mission_id)
                n += 1
                note("observe", f"{cap.slug}: {', '.join(f'{o.source_id}={o.status}' for o in obs)}")
                day = K.delivery_due(cap)
                if day:
                    text = K.digest(r)
                    events.append("principal_notified", {"capability": cap.slug, "channel": "regent_inbox",
                                                         "for_date": day, "text": text,
                                                         "view": f"/software/{cap.slug}"},
                                  source=f"capability:{cap.slug}", mission_id=mission_id)
                    prov = dict(cap.provenance or {})
                    prov["deliveries"] = {**(prov.get("deliveries") or {}), day: {"at": K.now().isoformat(),
                                                                                   "text": text}}
                    cap.provenance = prov
                    note("deliver", f"{cap.slug} -> principal ({day}): {text[:200]}")
            stats = {"capabilities_observed": n}
        else:
            raise ValueError(f"unknown software action {action}")
        req.log = list(req.log or []) + log
        req.stats = {**(req.stats or {}), **stats}
        return {"funnel": stats}

    def _adopt(self, s: Session, m, need: dict[str, Any]) -> None:
        attrs = dict(m.attrs or {})
        attrs["need"] = need
        m.attrs = attrs
        if need.get("handled_as") == "housing":
            m.tags = sorted(set(m.tags or []) | {"housing"})      # the housing work takes it from here
            return
        if need.get("handled_as") == "conversation":
            from regent.software.reasoner import ReasonerUnavailable
            from regent.software.reply import reply

            try:
                ans = reply(need["sentence"], mission_id=m.id)
            except ReasonerUnavailable as e:
                m.attrs = {**attrs, "paused": {"why": "Regent's reasoning service did not answer: " + str(e)[:200],
                                               "since": utcnow().isoformat()}}
                return
            m.attrs = {**attrs, "reply": ans}
            m.status = "completed"
            return
        if need.get("handled_as") != "software_capability":
            # nothing Regent can do here yet: say so instead of producing placeholder plans
            m.attrs = {**attrs, "unsupported": {"why": "This isn't something Regent can take on yet."}}
            m.status = "abandoned"
            return
        m.tags = sorted(set(m.tags or []) | {"software_need"})
        if not m.success_criteria:
            m.success_criteria = [
                {"id": "usable", "statement": "A capability Regent verified answers the need and is in use",
                 "condition": {"fact": f"software.need.{m.id}.usable", "op": "truthy"}},
                {"id": "covered", "statement": "The answer covers the core question (not only bounds or signals)",
                 "condition": {"fact": f"software.need.{m.id}.coverage", "op": "ge", "value": 0.8}}]
        EventStore(s).append("entity_upserted", {"id": f"need_{m.id}", "kind": "need", "name": need["sentence"][:120],
                                                 "attrs": {"mission_id": m.id, "questions": need.get("questions"),
                                                           "deliverable": need.get("deliverable"),
                                                           "subjects": need.get("subjects")}},
                             source="software:analyze", mission_id=m.id)

    def _project_discovery(self, s: Session, m, res: dict[str, Any]) -> None:
        ev = EventStore(s)
        for sub in res.get("subjects", []):
            for d in sub.get("deployments", []):
                fp = d["fingerprint"]
                ev.append("entity_upserted", {
                    "id": f"deploy_{d['host']}", "kind": "service", "name": d["host"],
                    "attrs": {"product": sub["name"], "url": d["url"], "aliases": d.get("aliases", []),
                              "platforms": fp["platforms"], "analytics": fp["analytics"],
                              "promises": [p["phrase"] for p in fp["promises"]], "owner_evidence": d["owner_evidence"],
                              "endpoints": [e["url"] for e in d["endpoints"] if e.get("json")]}},
                    source="software:discover", mission_id=m.id if m else None)

    def _project_inventory(self, s: Session, m, need: dict[str, Any], inv: dict[str, Any]) -> None:
        from regent.core.constitution.model import ConstitutionModel

        ev = EventStore(s)
        host = (inv.get("deployments") or [{}])[0].get("host", "product")
        for c in inv.get("constraints", []):
            if not c.get("evidence_found"):
                continue
            forb = set(c.get("forbids") or [])
            if {"adds_client_code", "third_party_tracking"} & forb:
                ConstitutionModel(s).upsert(
                    id=f"con_promise_tracking_{host}"[:40], type="hard_constraint",
                    statement=f"{host} publicly promises: \"{c['evidence'][:120]}\" -- nothing may add tracking to it",
                    rule={"forbid_tag": f"third_party_tracking:{host}"},
                    source={"kind": "observed_commitment", "evidence": c["evidence"][:300], "mission_id": m.id})
        ev.append("fact_observed", {"facts": [
            {"key": f"software.need.{m.id}.inventory", "value": {
                "public_fields": sum(1 for f in inv.get("public_fields", []) if f["relation_to_need"] != "unrelated"),
                "platform_sources": [p["id"] for p in inv.get("platform_sources", [])],
                "constraints": [c["statement"][:160] for c in inv["constraints"] if c.get("evidence_found")]},
             "confidence": 1.0, "source": "software:inventory"}]}, source="software:inventory", mission_id=m.id)

    # -------------------------------------------------------------- routes

    def strategies(self, mission: dict[str, Any], world: dict[str, Any]):
        from regent import db as dbm
        from regent.software.routes import strategies

        need = (mission.get("attrs") or {}).get("need") or {}
        if need.get("handled_as") != "software_capability":
            return []
        s = dbm.session()
        try:
            inv = latest_plan(s, mission["id"], "inventory")
        finally:
            s.close()
        if not inv:
            if need.get("reuse"):
                inv = {"existing_capabilities": need["reuse"]}
            else:
                return []
        return strategies(mission, need, inv)

    # ------------------------------------------ listing pipeline (unused here)

    def run_discovery(self, engine, request) -> None:
        raise NotImplementedError("software needs use run_custom")

    def extract(self, doc, purpose):
        return []

    def resolver(self):
        return None

    def funnel(self, engine, request):
        return {}

    def enrichment_jobs(self, engine, entity):
        return []

    def run_job(self, engine, job):
        return {}

    def project(self, engine, request):
        return []


def maintain(s: Session) -> list[str]:
    """Capabilities are Regent's own assets: keep every usable one current and deliver what is
    scheduled, whether or not the mission that created it is still open."""
    from regent.acquisition import service

    done = []
    for c in s.scalars(select(K.SwCapability).where(K.SwCapability.status.in_(("usable", "degraded")))):
        if c.implementation == "application" or not (K.due(c) or K.delivery_due(c)):
            continue
        out = service.run_action("observe", mission_id=c.mission_id, params={"capabilities": [c.id]},
                                 domain="software")
        done.append(f"{c.slug}: {out.get('status')}")
    from regent.software import appcap

    return done + appcap.maintain(s)


def _principal_hint(sentence: str) -> dict[str, Any]:
    from regent.acquisition.housing.geography import language_of

    lang = language_of(sentence)
    return {"language": lang, "country": {"ja": "jp", "de": "de", "fr": "fr", "es": "es"}.get(lang)}
