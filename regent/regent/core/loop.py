"""The Regent loop, explicit and un-abstracted.

    OBSERVE -> MODEL WORLD STATE -> GENERATE ROUTES -> EVALUATE ROUTES
    -> SELECT PLAN -> DECOMPOSE -> EXECUTE -> VERIFY -> UPDATE WORLD
    -> FULL RE-EVALUATION -> REPLAN

Each call to ``tick`` performs one pass for one mission and returns a phase
log. ``run`` repeats ticks while progress is being made; ``run_all`` drives
every active mission (including sub-missions Regent spawned) to quiescence:
either nothing is runnable, or the only remaining work is a bounded human
interrupt, or the mission's success criteria are met.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.core.audit.log import DecisionLog
from regent.core.capabilities.manager import CapabilityManager
from regent.core.executor.executor import Executor
from regent.core.goals.missions import MissionGraph
from regent.core.human.interrupts import HumanInterruptManager
from regent.core.observe.events import EventStore
from regent.core.replan.replanner import Replanner
from regent.core.routes.generator import RouteGenerator
from regent.core.verify.verifier import Verifier
from regent.core.world.projector import take_snapshot
from regent.core.world.state import WorldView
from regent.db import Mission, Operation, Snapshot
from regent.ids import utcnow
from regent.runtime import Services, get_services

# Events the loop writes about itself; they do not count as "the world changed".
SELF_EVENTS = {"loop_tick", "routes_ranked", "operation_planned", "operation_started", "operation_verified",
               "evidence_recorded", "route_selected", "plan_changed", "routes_generated", "mission_status_changed",
               "operation_cancelled", "route_invalidated", "capability_missing", "tool_succeeded", "tool_failed",
               "operation_rerouted", "human_interrupt_raised", "principal_notified"}


@dataclass
class TickReport:
    mission_id: str
    tick: int
    phases: list[dict[str, Any]] = field(default_factory=list)
    progress: bool = False
    status: str = "active"
    idle: bool = False

    def phase(self, name: str, **detail: Any) -> None:
        self.phases.append({"phase": name, "at": utcnow().isoformat(), **detail})


class RegentLoop:
    def __init__(self, db: Session, services: Services | None = None):
        self.db = db
        self.services = services or get_services()
        self.events = EventStore(db)
        self.graph = MissionGraph(db)
        self.generator = RouteGenerator(db, self.services)
        self.executor = Executor(db, self.services)
        self.verifier = Verifier(db, self.services)
        self.replanner = Replanner(db, self.services)
        self.interrupts = HumanInterruptManager(db, self.services)
        self.capabilities = CapabilityManager(db, self.services)
        self.log = DecisionLog(db)

    # ------------------------------------------------------------------ tick

    def tick(self, mission_id: str, *, force: bool = False) -> TickReport:
        m = self.graph.get(mission_id)
        if m is None:
            raise KeyError(mission_id)
        self.db.flush()
        self.db.refresh(m)                  # tools update missions from their own sessions
        rep = TickReport(mission_id=m.id, tick=m.tick_count + 1)
        if m.status in ("completed", "abandoned", "paused"):
            rep.status, rep.idle = m.status, True
            return rep

        # 1. OBSERVE ---------------------------------------------------------
        new_events = self.events.since(m.last_event_seq_seen, mission_id=m.id)
        external = [e for e in new_events if e.type not in SELF_EVENTS or e.mission_id != m.id]
        world = WorldView.load(self.db)
        facts, present = world.fact_map()
        resumed = [hi for hi in self.interrupts.check_resume_conditions(facts, present) if hi.mission_id == m.id]
        pending = self._runnable_count(m)
        if not pending and not external and not resumed and m.tick_count > 0 and not force:
            from regent.acquisition.domain import for_mission

            if for_mission(m):
                from regent.acquisition import service

                try:
                    pending = len(service.needs(self.db, m, world))   # e.g. a TTL expired since last tick
                except Exception:
                    pending = 0
        if not force and not external and not resumed and not pending and m.tick_count > 0:
            rep.idle, rep.status = True, m.status
            return rep
        m.tick_count += 1
        m.phase = "observe"
        rep.phase("observe", new_events=len(new_events), external=[f"{e.type}#{e.seq}" for e in external][-20:],
                  resumed_interrupts=[hi.id for hi in resumed])

        # 2. MODEL WORLD STATE ----------------------------------------------
        m.phase = "model"
        world = WorldView.load(self.db)
        snap = self._snapshot_if_changed(m, world)
        rep.phase("model", event_seq=world.event_seq, snapshot=snap.id, entities=len(world.entities),
                  facts=len(world.facts), runway_months=world.runway_months())

        # 2b. ACQUIRE: what don't I know that blocks a decision? -----------
        from regent.acquisition.domain import for_mission

        if for_mission(m):
            m.phase = "acquire"
            needs, created, ran = [], [], []
            for _round in range(4):
                acq = self._acquisition_needs(m, world)
                needs += acq["needs"]
                created += acq["created"]
                if not acq["blocking"]:
                    break
                # Nothing is known that strategies could be compared on: acquire first, then
                # generate and evaluate routes on evidence instead of priors. One answer can
                # reveal the next blocking question (what is needed -> where is it -> what can
                # answer it), so keep going while acquisition keeps blocking.
                xr0 = self.executor.run(m, WorldView.load(self.db), only=set(acq["blocking"]))
                ran += xr0.started
                self.db.refresh(m)          # acquisition may have updated the mission itself
                world = WorldView.load(self.db)
                if not xr0.started or not for_mission(m):
                    break
            if created:
                rep.progress = True
            rep.phase("acquire", needs=needs, operations=created, executed_before_planning=ran)

        # 3. GENERATE ROUTES -------------------------------------------------
        m.phase = "generate"
        self.capabilities.sync_from_registry()
        world = WorldView.load(self.db)
        why = self.generator.needs_generation(m, world)
        model_outputs: list[dict] = []
        if why or force and not self.generator.routes(m.id):
            g = self.generator.generate(m, world, reason=why or "forced")
            model_outputs = g.provider_outputs
            rep.phase("generate", reason=why, created=[r.key for r in g.created], updated=[r.key for r in g.updated],
                      providers=[o.get("provider") for o in g.provider_outputs])
        else:
            rep.phase("generate", skipped=True, live_routes=len(self.generator.routes(m.id, ("alive", "selected"))))

        # 4-5. EVALUATE + SELECT --------------------------------------------
        m.phase = "evaluate"
        world = WorldView.load(self.db)
        ev = self.replanner.reevaluate(m, world, trigger="tick", snapshot_id=snap.id, model_outputs=model_outputs)
        sel = ev["selection"]
        rep.phase("evaluate", ranking=[e.summary() for e in ev["evals"]][:8],
                  uncertainties=[{k: u[k] for k in ("fact", "flips_selection", "voi")} for u in ev["uncertainties"][:5]])
        rep.phase("select", kind=sel["kind"], selected=sel["selected"].route.key if sel["selected"] else None,
                  reason=sel["reason"], decision=ev["decision"].id if ev["decision"] else None)
        if sel["changed"]:
            rep.progress = True

        # 6. DECOMPOSE -------------------------------------------------------
        m.phase = "decompose"
        created: list[Operation] = []
        selected = self._selected_route(m)
        if selected is not None:
            created += self.replanner.planner.materialize(m, selected)
        created += self.replanner.planner.materialize_probes(m, ev["evals"], ev["uncertainties"])
        acq = self._capability_acquisition(m, ev["evals"])
        rep.phase("decompose", operations=[f"{o.key} ({o.tool}.{o.action}, {o.required_authority})" for o in created],
                  acquisitions=acq)

        # 7. EXECUTE ---------------------------------------------------------
        m.phase = "execute"
        world = WorldView.load(self.db)
        xr = self.executor.run(m, world)
        rep.phase("execute", started=len(xr.started), interrupts=xr.interrupts, blocked=xr.blocked,
                  authority=[{"op": a["operation_id"], "allowed": a["allowed"], "reason": a["reason"]}
                             for a in xr.authority])
        if xr.started or xr.interrupts:
            rep.progress = True

        # 8. VERIFY ----------------------------------------------------------
        m.phase = "verify"
        verified = []
        evidence_ids = []
        notes: list[str] = []
        for op in self.db.scalars(select(Operation).where(Operation.mission_id == m.id,
                                                          Operation.status == "unverified")):
            vr, evd = self.verifier.verify_and_record(op)
            verified.append({"op": op.key, "verdict": vr.verdict, "method": vr.method})
            evidence_ids.append(evd.id)
            # 9. UPDATE WORLD (consequences of verified work) -----------------
            notes += self.replanner.apply_consequences(op, evd)
        rep.phase("verify", verified=verified)
        rep.phase("update_world", evidence=evidence_ids, notes=notes)
        if verified:
            rep.progress = True

        # 10. FULL RE-EVALUATION + REPLAN -----------------------------------
        if verified or xr.interrupts:
            m.phase = "replan"
            world = WorldView.load(self.db)
            snap2 = self._snapshot_if_changed(m, world)
            ev2 = self.replanner.reevaluate(m, world, trigger="post-verification", snapshot_id=snap2.id,
                                            evidence_ids=evidence_ids)
            s2 = ev2["selection"]
            rep.phase("replan", kind=s2["kind"], selected=s2["selected"].route.key if s2["selected"] else None,
                      reason=s2["reason"], decision=ev2["decision"].id if ev2["decision"] else None)
            if s2["changed"]:
                rep.progress = True
                new_sel = self._selected_route(m)
                if new_sel is not None:
                    self.replanner.planner.materialize(m, new_sel)

        # status ------------------------------------------------------------
        world = WorldView.load(self.db)
        status = self._status(m, world)
        self.graph.set_status(m, status, reason="tick")
        m.phase = "idle" if status != "active" else "observe"
        m.last_event_seq_seen = self.events.head()
        m.attrs = {**(m.attrs or {}), "last_tick": {"tick": m.tick_count, "phases": rep.phases, "status": status}}
        m.updated_at = utcnow()
        from regent.software.reasoner import get_reasoner

        self.log.record_model_calls(self.services.providers.drain_calls() + get_reasoner().drain_calls())
        self.events.append("loop_tick", {"mission_id": m.id, "tick": m.tick_count, "status": status,
                                         "progress": rep.progress}, source="regent", mission_id=m.id)
        m.last_event_seq_seen = self.events.head()
        rep.status = status
        self.db.commit()
        return rep

    # --------------------------------------------------------------- drivers

    def run(self, mission_id: str, max_ticks: int = 12) -> list[TickReport]:
        reports = []
        for i in range(max_ticks):
            r = self.tick(mission_id, force=(i == 0))
            reports.append(r)
            if r.idle or r.status in ("completed", "abandoned") or not r.progress:
                break
        return reports

    def run_all(self, max_passes: int = 6, max_ticks: int = 8) -> dict[str, list[TickReport]]:
        out: dict[str, list[TickReport]] = {}
        self.maintain()
        for _ in range(max_passes):
            progressed = False
            for m in self.graph.active():
                reps = []
                for _i in range(max_ticks):
                    r = self.tick(m.id)
                    if r.idle:
                        break
                    reps.append(r)
                    if not r.progress or r.status in ("completed", "abandoned"):
                        break
                if reps:
                    out.setdefault(m.id, []).extend(reps)
                    progressed = progressed or any(r.progress for r in reps)
            if not progressed:
                break
        return out

    def maintain(self) -> list[str]:
        """Keep the capabilities Regent built current, independent of any mission's status."""
        try:
            from regent.software.domain import maintain

            self.db.commit()
            return maintain(self.db)
        except Exception as e:                      # maintenance must never stop the loop
            self.db.rollback()
            return [f"maintenance failed: {type(e).__name__}: {e}"[:200]]

    # --------------------------------------------------------------- helpers

    def _selected_route(self, m: Mission):
        from regent.db import Route

        return self.db.get(Route, m.selected_route_id) if m.selected_route_id else None

    def _runnable_count(self, m: Mission) -> int:
        return len(self.executor.ready(m))

    def _snapshot_if_changed(self, m: Mission, world: WorldView) -> Snapshot:
        last = self.db.scalar(select(Snapshot).order_by(Snapshot.event_seq.desc()).limit(1))
        if last is not None and last.event_seq == world.event_seq:
            return last
        return take_snapshot(self.db, reason=f"{m.id} tick {m.tick_count}")

    def _acquisition_needs(self, m: Mission, world: WorldView) -> dict[str, Any]:
        """Turn information needs into acquisition operations (AUTO: read-only public web)."""
        from regent.acquisition import service
        from regent.ids import new_id

        try:
            needs = service.needs(self.db, m, world)
        except Exception as e:  # acquisition must never break the loop
            return {"needs": [{"error": f"{type(e).__name__}: {e}"[:200]}], "created": [], "blocking": []}
        created: list[str] = []
        blocking: list[str] = []
        busy = {o.action for o in self.db.scalars(select(Operation).where(
            Operation.mission_id == m.id, Operation.tool == "acquire",
            Operation.status.in_(("pending", "running", "unverified", "waiting_human"))))}
        for n in needs:
            if n["action"] in busy:
                continue
            inputs = {"domain": n["domain"], **n["params"]}
            op = Operation(id=new_id("op"), mission_id=m.id, route_id=None, key=f"acquire.{n['action']}.{m.tick_count}",
                           goal=f"{n['action'].title()} ({n['domain']}): {n['reason']}", kind="acquire",
                           executor="search", tool="acquire", action=n["action"], required_authority="AUTO",
                           status="pending", inputs=inputs, outputs={}, timeout_s=3000,
                           retry_policy={"max_attempts": 1, "fallback": [], "fallback_index": -1},
                           verification={"method": "schema", "required_keys": ["request_id", "funnel"]},
                           cost_estimate={"minutes": 5}, depends_on=[], resolves=[], emits={}, sequence=-1,
                           priority=float(n.get("priority", 2.0)), attempt_log=[], evidence_ids=[])
            self.db.add(op)
            busy.add(n["action"])
            created.append(f"{op.key}: {n['reason']}")
            if n.get("blocking"):
                blocking.append(op.id)
        self.db.flush()
        return {"needs": [{k: v for k, v in n.items() if k in ("domain", "action", "reason", "state")} for n in needs],
                "created": created, "blocking": blocking}

    def _capability_acquisition(self, m: Mission, evals) -> list[str]:
        if "capability_acquisition" in (m.tags or []):
            return []
        opened = []
        live = [e for e in evals if not e.invalid][:2]
        for e in live:
            for cap in e.missing_capabilities:
                child = self.capabilities.open_acquisition(m, e.route, cap)
                opened.append(f"{cap} -> {child.id}")
        return opened

    def _status(self, m: Mission, world: WorldView) -> str:
        if self.graph.is_complete(m, world):
            return "completed"
        if self._runnable_count(m):
            return "active"
        running = self.db.scalar(select(Operation.id).where(Operation.mission_id == m.id,
                                                            Operation.status.in_(("running", "unverified"))).limit(1))
        if running:
            return "active"
        if self.interrupts.open(m.id):
            return "waiting_human"
        return "monitoring"


class BackgroundLoop:
    """Drives all active missions periodically (pragmatic in-process worker)."""

    def __init__(self, interval_s: float = 2.0):
        import threading

        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="regent-loop", daemon=True)
        self.last_error: str | None = None
        self.passes = 0
        self.lock = threading.Lock()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def run_once(self) -> dict[str, Any]:
        from regent import db as dbm

        with self.lock:
            s = dbm.session()
            try:
                out = RegentLoop(s).run_all(max_passes=3, max_ticks=6)
                s.commit()
                self.passes += 1
                return {m: [r.status for r in reps] for m, reps in out.items()}
            except Exception as e:
                s.rollback()
                self.last_error = f"{type(e).__name__}: {e}"
                raise
            finally:
                s.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                pass
            self._stop.wait(self.interval_s)
