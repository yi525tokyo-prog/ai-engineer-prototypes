"use client";
import type { ReactNode } from "react";
import type { Operation, Route } from "@/lib/api";
import { ago, humanStatus, num, pct, statusTone } from "@/lib/format";

export function Panel({ title, aside, children, attn, flush, id }: {
  title: string; aside?: ReactNode; children: ReactNode; attn?: boolean; flush?: boolean; id?: string;
}) {
  return (
    <section className={`panel${attn ? " attn" : ""}`} id={id} aria-label={title}>
      <header><h2>{title}</h2>{aside ? <span className="aside">{aside}</span> : null}</header>
      <div className={`body${flush ? " flush" : ""}`}>{children}</div>
    </section>
  );
}

export function Status({ s }: { s: string }) {
  return <span className={`st ${statusTone(s)}`}>{humanStatus(s)}</span>;
}

export function Auth({ level }: { level: string }) {
  return <span className={`auth ${level}`} title={`authority: ${level}`}>{level}</span>;
}

const EST_FIELDS: [string, string, (v: number) => string][] = [
  ["expected_upside", "Upside", (v) => num(v, 0)],
  ["success_probability", "P(success)", pct],
  ["time_cost_hours", "Time (h)", (v) => num(v, 1)],
  ["money_cost", "Cash cost", (v) => num(v, 0)],
  ["information_gain", "Info gain", pct],
  ["reversibility", "Reversible", pct],
  ["optionality", "Optionality", pct],
  ["risk", "Risk", pct],
  ["authority_cost", "Authority", pct],
];

export function Estimates({ route }: { route: Route }) {
  const eff = route.effective ?? {};
  const base = route.estimates ?? {};
  return (
    <div className="est">
      {EST_FIELDS.map(([k, label, f]) => {
        const v = eff[k] ?? base[k];
        const changed = base[k] !== undefined && eff[k] !== undefined && Math.abs(Number(base[k]) - Number(eff[k])) > 1e-6;
        return (
          <div key={k} title={changed ? `estimate ${f(Number(base[k]))} adjusted by evidence to ${f(Number(eff[k]))}` : undefined}>
            <div className="k">{label}</div>
            <div className="v">{changed ? <span className="was">{f(Number(base[k]))}</span> : null}{v === undefined ? "—" : f(Number(v))}</div>
          </div>
        );
      })}
    </div>
  );
}

export function OpsList({ ops, compact }: { ops: Operation[]; compact?: boolean }) {
  if (!ops.length) return <div className="muted">No operations.</div>;
  return (
    <ul className="ops">
      {ops.map((o) => (
        <li key={o.id}>
          <Status s={o.status} />
          <div className="goal">
            <div>{o.goal}{o.kind === "probe" ? <span className="tag" style={{ marginLeft: 6 }}>probe</span> : null}</div>
            {!compact ? (
              <div className="meta">
                {o.tool}.{o.action} · {o.executor}
                {o.attempts > 1 ? ` · ${o.attempts} attempts` : ""}
                {o.attempt_log?.some((a) => a.via) ? " · skill applied" : ""}
                {o.verification_result?.verdict ? ` · verified: ${o.verification_result.verdict} (${o.verification_result.method})` : ""}
                {o.authority_decision?.grant_id ? ` · grant ${o.authority_decision.grant_id}` : ""}
                {o.outputs?.summary ? ` · ${o.outputs.summary}` : ""}
              </div>
            ) : null}
            {o.error && o.status !== "succeeded" ? <div className="err">{o.error}</div> : null}
          </div>
          <div style={{ textAlign: "right" }}>
            <Auth level={o.required_authority} />
            <div className="tiny muted">{ago(o.finished_at ?? o.started_at ?? o.created_at)}</div>
          </div>
        </li>
      ))}
    </ul>
  );
}
