"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Cockpit, type Json, type Route } from "@/lib/api";
import { num } from "@/lib/format";
import {
  AlternativesPanel, BestRoutePanel, BlockedPanel, ChangesPanel, ConstitutionPanel, ExecutingPanel,
  MissionPanel, NowPanel, SystemPanel, WhyPanel, WorldPanel,
} from "./components/sections";
import { Status } from "./components/ui";

const POLL_MS = 1500;

export default function Page() {
  const [missions, setMissions] = useState<Json[]>([]);
  const [mid, setMid] = useState<string | null>(null);
  const [c, setC] = useState<Cockpit | null>(null);
  const [sys, setSys] = useState<Json | null>(null);
  const [script, setScript] = useState<{ key: string; label: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("m");
    if (q) setMid(q);
    api.system().then(setSys).catch(() => {});
    api.script().then(setScript).catch(() => {});
  }, []);

  const refresh = useCallback(async () => {
    try {
      const ms = await api.missions();
      setMissions(ms);
      const target = mid ?? ms.find((m) => !m.parent_id)?.id ?? null;
      if (target && target !== mid) setMid(target);
      if (target) setC(await api.cockpit(target));
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, [mid]);

  useEffect(() => {
    let alive = true;
    const loop = async () => {
      await refresh();
      if (alive) timer.current = setTimeout(loop, POLL_MS);
    };
    loop();
    return () => { alive = false; if (timer.current) clearTimeout(timer.current); };
  }, [refresh]);

  const select = (id: string) => {
    setMid(id);
    const u = new URL(window.location.href);
    u.searchParams.set("m", id);
    window.history.replaceState(null, "", u);
  };

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      setToast(label);
      setTimeout(() => setToast(null), 2500);
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const m = c?.mission;
  const cash = c?.treasury.resources?.find((r: Json) => r.kind === "money");
  const apiRes = c?.treasury.resources?.find((r: Json) => r.kind === "api_spend");
  const att = c?.treasury.resources?.find((r: Json) => r.kind === "attention");
  const openCount = missions.reduce((n, x) => n + (x.open_interrupts ?? 0), 0);

  return (
    <main>
      <div className="topbar">
        <span className="brand">REGENT</span>
        <select aria-label="Mission" value={mid ?? ""} onChange={(e) => select(e.target.value)}>
          {missions.map((x) => (
            <option key={x.id} value={x.id}>{x.parent_id ? "  └ " : ""}{x.title}</option>
          ))}
        </select>
        {m ? <Status s={m.status} /> : null}
        {m ? <span className="kv"><span className="k">phase</span><span className="mono">{m.phase}</span></span> : null}
        {m ? <span className="kv"><span className="k">tick</span><span className="num">{m.tick_count}</span></span> : null}
        {cash ? <span className="kv"><span className="k">cash</span><span className="num">{num(cash.balance)} {cash.unit}</span></span> : null}
        {c?.world.runway_months != null ? <span className="kv"><span className="k">runway</span><span className="num">{num(c.world.runway_months)} mo</span></span> : null}
        {apiRes ? <span className="kv"><span className="k">api</span><span className="num">${num(apiRes.balance)}</span></span> : null}
        {att ? <span className="kv"><span className="k">attention</span><span className="num">{num(att.balance, 1)} min</span></span> : null}
        {openCount ? <a href="#blocked" className="kv"><span className="st warn">{openCount} waiting on you</span></a> : null}
        <span className="spacer" />
        {err ? <span className="st bad" title={err}>API unreachable</span> : null}
        <button className="btn" disabled={busy || !mid} onClick={() => act("Loop ran", () => api.tick(mid!))}>Run loop now</button>
      </div>

      {!c ? (
        <div style={{ padding: 24 }}>
          {missions.length === 0 && !err ? (
            <div className="panel" style={{ maxWidth: 520, padding: 16 }}>
              <h3>No missions yet</h3>
              <p className="dim">Load the seeded case study (a freelancer with limited cash, one client meeting, a software project, unanswered messages and an uncertain workplace).</p>
              <button className="btn primary" disabled={busy} onClick={() => act("Case study loaded", api.seed)}>Load case study</button>
            </div>
          ) : <span className="muted">{err ?? "Loading…"}</span>}
        </div>
      ) : (
        <div className="grid">
          <div className="col">
            <MissionPanel c={c} onSelect={select} />
            <WorldPanel c={c} />
            <ConstitutionPanel c={c} />
            <SystemPanel sys={sys} c={c} busy={busy} script={script}
                         onSeed={() => act("Case study reset", api.seed)}
                         onScript={(k) => act("World event injected", () => api.applyScript(k))} />
          </div>
          <div className="col center">
            <div id="blocked">
              <BlockedPanel items={c.blocked_by_you} busy={busy}
                            onResolve={(id, resp, res) => act("Response recorded; Regent resumes", () => api.resolve(id, resp, res))} />
            </div>
            <BestRoutePanel c={c} />
            <WhyPanel c={c} />
            <ExecutingPanel c={c} />
          </div>
          <div className="col right">
            <AlternativesPanel c={c} busy={busy}
                               onOverride={(r: Route) => act("Override recorded", () => api.override(c.mission.id, r.id))}
                               onReject={(r: Route) => act("Route rejected", () => api.reject(c.mission.id, r.id))} />
            <ChangesPanel c={c} />
            <NowPanel c={c} busy={busy} onTell={(t) => act("Fact ingested", () => api.tell(t))} />
          </div>
        </div>
      )}
      {toast ? <div className="toast">{toast}</div> : null}
    </main>
  );
}
