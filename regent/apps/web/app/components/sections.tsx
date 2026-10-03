"use client";
import { useState } from "react";
import type { Cockpit, Decision, Interrupt, Json, Route } from "@/lib/api";
import { ago, duration, estimateDelta, num, pct, signed } from "@/lib/format";
import { Meter, ScoreBars, Sparkline } from "./charts";
import { Auth, Estimates, OpsList, Panel, Status } from "./ui";

const PHASES = ["observe", "model", "generate", "evaluate", "select", "decompose", "execute", "verify", "update_world", "replan"];

/* ------------------------------------------------------------------ MISSION */

export function MissionPanel({ c, onSelect }: { c: Cockpit; onSelect: (id: string) => void }) {
  const m = c.mission;
  return (
    <Panel title="Mission" aside={<Status s={m.status} />}>
      {m.parent ? (
        <div className="tiny muted">sub-mission of <a href="#" onClick={(e) => { e.preventDefault(); onSelect(m.parent!.id); }}>{m.parent.title}</a></div>
      ) : null}
      <h3>{m.title}</h3>
      <p className="dim">{m.objective}</p>
      <div className="sub">Success criteria</div>
      {m.criteria.length ? m.criteria.map((cr: Json) => (
        <div className="crit" key={cr.id}>
          <span className={`check${cr.met ? " ok" : ""}`}>{cr.met ? "✓" : cr.known ? "✗" : "?"}</span>
          <span>{cr.statement}</span>
        </div>
      )) : <div className="muted">None declared.</div>}
      {m.children.length ? (
        <>
          <div className="sub">Sub-missions</div>
          {m.children.map((ch: Json) => (
            <div key={ch.id} className="crit" style={{ justifyContent: "space-between" }}>
              <a href="#" onClick={(e) => { e.preventDefault(); onSelect(ch.id); }}>{ch.title}</a>
              <Status s={ch.status} />
            </div>
          ))}
        </>
      ) : null}
      <div className="tiny muted" style={{ marginTop: 8 }}>
        horizon {num(m.horizon_days)}d · value scale {num(m.value_scale)} · tick {m.tick_count}
      </div>
    </Panel>
  );
}

/* ---------------------------------------------------------- BLOCKED BY YOU */

function resumeText(rc: Json): string {
  if (rc?.type === "page_state") return `Regent resumes on its own when the page no longer shows the ${rc.blocker_absent ?? "blocker"}.`;
  if (rc?.type === "fact") return `Regent resumes when it observes ${rc.fact}.`;
  return "Regent resumes as soon as you respond.";
}

export function BlockedPanel({ items, onResolve, busy }: {
  items: Interrupt[]; onResolve: (id: string, response: Json, resolution?: string) => void; busy: boolean;
}) {
  if (!items.length) return null;
  return (
    <Panel title="Blocked by you" attn aside={`${items.length} bounded action${items.length > 1 ? "s" : ""}`} flush>
      {items.map((h) => (
        <div className="hi" key={h.id}>
          <div className="tiny muted">{h.kind} · {duration(h.estimated_time_seconds)} · raised {ago(h.created_at)}</div>
          <div className="action">{h.required_action}</div>
          <div className="small dim">{h.reason}</div>
          {h.blocking_operation ? <div className="tiny muted" style={{ marginTop: 2 }}>blocking: {h.blocking_operation}</div> : null}
          <div className="tiny muted">{resumeText(h.resume_condition)}</div>
          <div className="row">
            {h.context?.url ? <a className="btn primary" href={h.context.url} target="_blank" rel="noreferrer">Open page</a> : null}
            {h.kind === "authorization" ? (
              <>
                <button className="btn primary" disabled={busy} onClick={() => onResolve(h.id, { approve: true })}>Approve once</button>
                <button className="btn" disabled={busy} onClick={() => onResolve(h.id, { approve: true, grant_standing: true })}>Always allow</button>
                <button className="btn" disabled={busy} onClick={() => onResolve(h.id, { approve: false }, "denied")}>Deny</button>
              </>
            ) : (
              <>
                <button className="btn" disabled={busy} onClick={() => onResolve(h.id, { done: true })}>I've done it</button>
                <button className="btn ghost" disabled={busy} onClick={() => onResolve(h.id, { done: false }, "declined")}>Can't</button>
              </>
            )}
          </div>
        </div>
      ))}
    </Panel>
  );
}

/* --------------------------------------------------------------- BEST ROUTE */

export function BestRoutePanel({ c }: { c: Cockpit }) {
  const r = c.best_route;
  if (!r) return <Panel title="Best route"><div className="muted">No route selected yet.</div></Panel>;
  const hist = (c.score_history[r.id] ?? []).map((x) => x.score);
  const done = (r.operations ?? []).filter((o) => o.status === "succeeded").length;
  return (
    <Panel title="Best route" aside={<span className="num">score {num(r.score, 3)}</span>}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <h3 style={{ flex: 1 }}>{r.title}</h3>
        <Sparkline values={hist} label={r.title} />
      </div>
      <p className="dim">{r.thesis}</p>
      <div>
        {r.tags.map((t) => <span className="tag" key={t}>{t.replace(/_/g, " ")}</span>)}
        <span className="tiny muted">proposed by {r.generated_by.join(", ")} · {r.archetype.replace(/_/g, " ")}</span>
      </div>
      <Estimates route={r} />
      <div className="sub">Operations <span className="muted" style={{ textTransform: "none", letterSpacing: 0 }}>{done}/{r.operations?.length ?? 0} done</span></div>
      <OpsList ops={r.operations ?? []} />
      {r.critiques?.length ? (
        <details style={{ marginTop: 8 }}>
          <summary className="small">Critiques ({r.critiques.reduce((n, x) => n + (x.issues?.length ?? 0), 0)})</summary>
          <ul className="small dim">{r.critiques.flatMap((x) => (x.issues ?? []).map((i: string, k: number) => <li key={`${x.provider}${k}`}>{i} <span className="muted">— {x.provider}</span></li>))}</ul>
        </details>
      ) : null}
    </Panel>
  );
}

/* ---------------------------------------------------------------------- WHY */

function EvidenceEffects({ effects }: { effects: Json[] }) {
  if (!effects?.length) return null;
  return (
    <ul className="effects small" style={{ margin: "4px 0 0", paddingLeft: 16 }}>
      {effects.map((a, i) => (
        <li key={i}>
          <span className="mono">{a.fact}</span> = <b>{String(a.value)}</b>
          <span className="dim"> — {a.rationale}</span>
          <span className="muted"> ({Object.entries(a.effects ?? {}).map(([k, v]: [string, any]) => `${k.replace(/_/g, " ")} ${num(v.from)}→${num(v.to)}`).join(", ")})</span>
          {a.evidence_id ? <span className="muted mono"> {a.evidence_id}</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function WhyPanel({ c }: { c: Cockpit }) {
  const r = c.best_route;
  const d = c.why.last_decision;
  const ev = c.why.evaluation;
  const vr = d?.rationale?.versus_runner_up;
  const runner = vr ? c.alternatives.find((a) => a.key === vr.runner_up) : undefined;
  if (!r) return null;
  const prev = d?.rationale?.routes?.find((x: Json) => x.route_id === d.previous_route_id);
  return (
    <Panel title="Why" aside={d ? `${d.kind.replace(/_/g, " ")} · ${ago(d.created_at)}` : undefined}>
      {d ? <p>{d.summary}</p> : null}
      {prev?.new_evidence_effects?.length ? (
        <>
          <div className="sub">Evidence that moved the previous route</div>
          <div className="small"><b>{prev.title}</b> {num(prev.previous_score, 3)} → {num(prev.score, 3)}</div>
          <EvidenceEffects effects={prev.new_evidence_effects} />
        </>
      ) : null}
      <div className="sub">Score composition{runner ? <span className="muted" style={{ textTransform: "none", letterSpacing: 0 }}> · margin over “{runner.title}” {signed(vr.margin)}</span> : null}</div>
      <ScoreBars components={r.score_breakdown?.components} compare={runner?.score_breakdown?.components} compareLabel={runner?.title} />
      {r.applied_sensitivities?.length ? (
        <>
          <div className="sub">Evidence applied to this route</div>
          <EvidenceEffects effects={r.applied_sensitivities} />
        </>
      ) : null}
      {c.why.uncertainties?.length ? (
        <>
          <div className="sub">Uncertainties that matter</div>
          <table className="t small">
            <thead><tr><th>Unknown</th><th>Could flip plan</th><th className="r">VOI</th></tr></thead>
            <tbody>
              {c.why.uncertainties.slice(0, 5).map((u) => (
                <tr key={u.fact}>
                  <td className="wrap">{u.question}<div className="tiny muted mono">{u.fact}{u.resolvable_by?.length ? ` ← ${u.resolvable_by.join(", ")}` : ""}</div></td>
                  <td>{u.flips_selection ? <span className="st warn">yes</span> : <span className="st">no</span>}</td>
                  <td className="r num">{num(u.voi, 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
      {ev ? (
        <details style={{ marginTop: 8 }}>
          <summary className="small">Weights ({ev.weight_reasons?.length ?? 0} adjustments)</summary>
          <table className="t small" style={{ marginTop: 4 }}>
            <tbody>{Object.entries(ev.weights ?? {}).map(([k, v]) => <tr key={k}><td>{k.replace(/_/g, " ")}</td><td className="r num">{num(v, 3)}</td></tr>)}</tbody>
          </table>
          <ul className="small dim">{(ev.weight_reasons ?? []).map((w: Json, i: number) => <li key={i}>{w.source}: {w.statement} {w.factor ? `(×${num(w.factor, 2)})` : w.effect ? `(${w.effect})` : ""}</li>)}</ul>
        </details>
      ) : null}
    </Panel>
  );
}

/* ---------------------------------------------------------------- EXECUTING */

export function ExecutingPanel({ c }: { c: Cockpit }) {
  const lt = c.executing.last_tick;
  const seen = new Set((lt?.phases ?? []).map((p: Json) => p.phase));
  const ops = c.executing.operations.filter((o) => o.route_id !== c.best_route?.id).slice(0, 12);
  return (
    <Panel title="Executing" aside={<span>phase <b>{c.executing.phase}</b> · tick {lt?.tick ?? 0}</span>}>
      <div className="phase" aria-label="loop phases">
        {PHASES.map((p) => <span key={p} className={seen.has(p) ? "on" : ""}>{p.replace("_", " ")}</span>)}
      </div>
      {lt ? (
        <details style={{ marginTop: 6 }}>
          <summary className="small">Last tick detail</summary>
          <ul className="small dim mono" style={{ paddingLeft: 16 }}>
            {lt.phases.map((p: Json, i: number) => (
              <li key={i} className="wrap">{p.phase}: {JSON.stringify(Object.fromEntries(Object.entries(p).filter(([k]) => !["phase", "at", "ranking"].includes(k))))}</li>
            ))}
          </ul>
        </details>
      ) : null}
      <div className="sub">Other operations (probes, deselected routes, sub-missions)</div>
      <OpsList ops={ops} />
    </Panel>
  );
}

/* ------------------------------------------------------------- ALTERNATIVES */

export function AlternativesPanel({ c, onOverride, onReject, busy }: {
  c: Cockpit; onOverride: (r: Route) => void; onReject: (r: Route) => void; busy: boolean;
}) {
  const best = c.best_route?.score ?? 0;
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Panel title="Alternatives" aside={`${c.alternatives.filter((a) => a.status === "alive").length} alive`} flush>
      {c.alternatives.length ? c.alternatives.map((r) => {
        const dead = ["invalidated", "abandoned"].includes(r.status);
        const hist = (c.score_history[r.id] ?? []).map((x) => x.score);
        const why = [...(r.score_breakdown?.blocked ?? []), ...(r.score_breakdown?.invalid ?? [])];
        return (
          <div className={`alt${dead ? " dead" : ""}`} key={r.id}>
            <div className="head">
              <span className="rank">{r.rank}</span>
              <a href="#" className="title" style={{ color: "inherit" }} onClick={(e) => { e.preventDefault(); setOpen(open === r.id ? null : r.id); }}>{r.title}</a>
              <Sparkline values={hist} width={56} label={r.title} />
              <span className="score">{num(r.score, 3)}</span>
              <span className="delta">{signed(r.score - best, 2)}</span>
            </div>
            <div className="line2">
              <Status s={r.status} />
              {why.map((w: string) => <span className="tiny muted" key={w}>{w}</span>)}
              <span className="tiny muted">P {pct(r.effective?.success_probability)} · opt {pct(r.effective?.optionality)}</span>
              {r.applied_sensitivities?.length ? <span className="tiny muted">{r.applied_sensitivities.length} evidence effect(s)</span> : null}
            </div>
            {open === r.id ? (
              <div style={{ margin: "6px 0 0 26px" }}>
                <p className="small dim">{r.thesis}</p>
                <Estimates route={r} />
                <EvidenceEffects effects={r.applied_sensitivities} />
                <ScoreBars components={r.score_breakdown?.components} />
                {r.operations?.length ? <><div className="sub">Operations</div><OpsList ops={r.operations} compact /></> : null}
                <div className="row" style={{ display: "flex", gap: 6, marginTop: 8 }}>
                  <button className="btn" disabled={busy || dead} onClick={() => onOverride(r)}>Choose this route</button>
                  <button className="btn ghost" disabled={busy || dead} onClick={() => onReject(r)}>Reject</button>
                </div>
              </div>
            ) : null}
          </div>
        );
      }) : <div className="empty">No alternatives.</div>}
    </Panel>
  );
}

/* ------------------------------------------------------------------ CHANGES */

function changedRoutes(d: Decision): Json[] {
  return (d.rationale?.routes ?? []).filter((r: Json) => r.new_evidence_effects?.length);
}

export function ChangesPanel({ c }: { c: Cockpit }) {
  return (
    <Panel title="Changes" aside="strategy history" flush>
      {c.changes.length ? (
        <ul className="tl">
          {c.changes.map((d) => (
            <li key={d.id} className={d.kind}>
              <div className="small"><b>{d.kind.replace(/_/g, " ")}</b> <span className="muted">· tick {d.tick} · {ago(d.created_at)}</span></div>
              <div>{d.summary}</div>
              {d.kind !== "route_selected" && changedRoutes(d).map((r) => (
                <div key={r.route_id} className="small" style={{ marginTop: 3 }}>
                  <span className="dim">{r.title}: {num(r.previous_score, 3)} → {num(r.score, 3)}</span>
                  <ul className="effects">
                    {r.new_evidence_effects.map((a: Json, i: number) => (
                      <li key={i}><span className="mono">{a.fact}</span> = {String(a.value)} — {a.rationale}</li>
                    ))}
                    {estimateDelta(r.estimate_changes).slice(0, 3).map((t) => <li key={t} className="muted">{t}</li>)}
                  </ul>
                </div>
              ))}
              {d.evidence_ids?.length ? <div className="tiny muted mono">evidence: {d.evidence_ids.slice(0, 4).join(", ")}{d.evidence_ids.length > 4 ? "…" : ""}</div> : null}
            </li>
          ))}
        </ul>
      ) : <div className="empty">No strategy decisions yet.</div>}
    </Panel>
  );
}

/* ---------------------------------------------------------------------- NOW */

export function NowPanel({ c, onTell, busy }: { c: Cockpit; onTell: (t: string) => void; busy: boolean }) {
  const [text, setText] = useState("");
  const diff = c.now.world_diff ?? {};
  const facts = diff.facts ? [...(diff.facts.added ?? []), ...(diff.facts.changed ?? []).map((x: Json) => x.id)] : [];
  return (
    <Panel title="Now" aside={`${c.now.new_since_last_strategy} world events since last strategy change`} flush>
      {facts.length ? (
        <div className="small" style={{ padding: "6px 12px", borderBottom: "1px solid var(--line)" }}>
          <span className="muted">Changed since the last decision: </span>
          {facts.slice(0, 10).map((f: string) => <span className="tag mono" key={f}>{f}</span>)}
        </div>
      ) : null}
      <ul className="feed">
        {c.now.events.map((e) => (
          <li key={e.seq} className={e.seq > c.now.since_seq ? "new" : ""} title={`${e.type} from ${e.source}`}>
            <span className="seq">#{e.seq}</span>
            <span className="wrap">{e.text} <span className="tiny muted">· {e.source} · {ago(e.at)}</span></span>
          </li>
        ))}
      </ul>
      <form style={{ padding: "8px 12px", borderTop: "1px solid var(--line)" }}
            onSubmit={(e) => { e.preventDefault(); if (text.trim()) { onTell(text); setText(""); } }}>
        <input className="in" placeholder="Tell Regent a fact (e.g. “Invoice from Mori Ltd paid ¥120,000”) — it is extracted into world state"
               value={text} onChange={(e) => setText(e.target.value)} disabled={busy} />
      </form>
    </Panel>
  );
}

/* -------------------------------------------------------------------- WORLD */

export function WorldPanel({ c }: { c: Cockpit }) {
  const w = c.world;
  const res: Json[] = c.treasury.resources ?? [];
  const byKind = (k: string) => (w.entities ?? []).filter((e: Json) => e.kind === k);
  const caps: Json[] = (w.capabilities ?? []).filter((x: Json) => ["missing", "acquiring"].includes(x.status) || x.attrs?.built_tool_id);
  return (
    <Panel title="World" aside={`event #${w.event_seq}`}>
      <table className="t small">
        <tbody>
          {res.map((r) => (
            <tr key={r.id}>
              <td>{r.name}{r.limit ? <Meter value={r.balance} max={r.limit} label={r.name} /> : null}</td>
              <td className="r num">{num(r.balance)} <span className="muted">{r.unit}</span></td>
            </tr>
          ))}
          {w.runway_months !== null && w.runway_months !== undefined ? (
            <tr><td>Runway</td><td className="r num">{num(w.runway_months)} <span className="muted">months</span></td></tr>
          ) : null}
        </tbody>
      </table>
      {byKind("contract").length ? <div className="sub">Opportunities</div> : null}
      {byKind("contract").map((e: Json) => (
        <div key={e.id} className="small" style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
          <span>{e.name}</span><span className="muted">{e.attrs.status}</span>
        </div>
      ))}
      {byKind("commitment").length || byKind("event").length ? <div className="sub">Commitments</div> : null}
      {[...byKind("event"), ...byKind("commitment")].slice(0, 8).map((e: Json) => (
        <div key={e.id} className="small" style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
          <span>{e.name}</span><span className="muted num">{(e.attrs.start ?? e.attrs.due ?? "").slice(5, 16).replace("T", " ")}</span>
        </div>
      ))}
      <div className="sub">Unanswered ({w.unanswered?.length ?? 0})</div>
      {(w.unanswered ?? []).map((m: Json) => <div key={m.id} className="small">{m.subject} <span className="muted">· {m.from}</span></div>)}
      {!w.unanswered?.length ? <div className="small muted">Inbox clear.</div> : null}
      <div className="sub">Recent facts</div>
      <table className="t small">
        <tbody>
          {(w.facts ?? []).slice(0, 12).map((f: Json) => (
            <tr key={f.key} title={`source ${f.source}${f.evidence_id ? `, evidence ${f.evidence_id}` : ""}`}>
              <td className="mono wrap" style={{ fontSize: 11 }}>{f.key}</td>
              <td className="r mono" style={{ fontSize: 11, whiteSpace: "nowrap" }}>{typeof f.value === "object" ? JSON.stringify(f.value) : String(f.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {caps.length ? <div className="sub">Capabilities</div> : null}
      {caps.map((x) => (
        <div key={x.id} className="small" style={{ display: "flex", justifyContent: "space-between" }}>
          <span className="mono">{x.id}</span>
          <span>{x.attrs?.built_tool_id ? <span className="tag">built by Regent</span> : null}<Status s={x.status} /></span>
        </div>
      ))}
      {w.skills?.length ? <div className="sub">Skills learned</div> : null}
      {(w.skills ?? []).slice(0, 4).map((s: Json) => (
        <div key={s.id} className="small wrap"><span className="tag">{s.domain}</span>{s.name} <span className="muted">conf {num(s.confidence)}</span></div>
      ))}
    </Panel>
  );
}

/* ------------------------------------------------------------- CONSTITUTION */

export function ConstitutionPanel({ c }: { c: Cockpit }) {
  const groups: [string, string][] = [["hard_constraints", "Hard constraints"], ["priorities", "Current priorities"],
    ["strong_preferences", "Strong preferences"], ["weak_preferences", "Weak preferences"], ["conflicts", "Unresolved conflicts"]];
  return (
    <Panel title="Constitution" aside="inferred · editable by override">
      {groups.map(([k, label]) => (c.constitution[k]?.length ? (
        <div key={k}>
          <div className="sub">{label}</div>
          {c.constitution[k].map((it: Json) => (
            <div key={it.id} className="small" style={{ padding: "2px 0" }} title={`sources: ${(it.source ?? []).map((s: Json) => s.kind).join(", ")}`}>
              <span className="conf" aria-label={`confidence ${pct(it.confidence)}`}><span style={{ width: `${it.confidence * 100}%` }} /></span>
              {it.statement} <span className="muted num">{pct(it.confidence)}</span>
            </div>
          ))}
        </div>
      ) : null))}
    </Panel>
  );
}

/* ------------------------------------------------------------ SYSTEM & SIM */

export function SystemPanel({ sys, c, onSeed, onScript, script, busy }: {
  sys: Json | null; c: Cockpit; onSeed: () => void; onScript: (k: string) => void;
  script: { key: string; label: string }[]; busy: boolean;
}) {
  return (
    <Panel title="System">
      {sys ? (
        <>
          <div className="small">db <b>{sys.database}</b> · loop {sys.background_loop?.enabled ? "running" : "manual"} · {sys.tools?.length} tools</div>
          <div className="sub">Model providers</div>
          {sys.providers.map((p: Json) => (
            <div key={p.name} className="small" style={{ display: "flex", justifyContent: "space-between" }}>
              <span>{p.name} <span className="muted mono">{p.model}</span></span>
              {p.available ? <span className="st good">ready</span> : <span className="st warn" title={p.missing_credentials.join(", ")}>needs {p.missing_credentials[0]}</span>}
            </div>
          ))}
          <details style={{ marginTop: 6 }}>
            <summary className="small">Missing credentials ({sys.missing_credentials.length}) — local backends in use</summary>
            <div className="mono tiny wrap dim">{sys.missing_credentials.join(" · ")}</div>
          </details>
          <div className="small muted" style={{ marginTop: 4 }}>{c.model_calls.length} recent model calls</div>
        </>
      ) : <div className="muted">…</div>}
      <div className="sub">Simulation</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {script.map((s) => <button key={s.key} className="btn" disabled={busy} onClick={() => onScript(s.key)}>{s.label}</button>)}
        <button className="btn ghost" disabled={busy} onClick={onSeed}>Reset case study</button>
      </div>
    </Panel>
  );
}

export { Auth };
