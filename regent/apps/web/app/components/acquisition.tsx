"use client";
import { useEffect, useState } from "react";
import { api, type Json } from "@/lib/api";
import { ago, beliefText, money, num, pct, ttlText } from "@/lib/format";
import { Panel, Status } from "./ui";

/* World Acquisition: what Regent went to the web for, where it could and could not look,
   and every candidate as evidence-backed competing hypotheses (never a single scraped value). */

const COLS: [string, string][] = [
  ["rent", "Rent / month"], ["management_fee", "Mgmt"], ["deposit", "Deposit"], ["key_money", "Key"],
  ["availability", "Available"], ["bedrooms", "Beds"], ["stations", "Access"],
];

function Belief({ b, attr, currency }: { b: Json | undefined; attr: string; currency?: string }) {
  if (!b) return <span className="muted">—</span>;
  const alts = (b.hypotheses ?? []) as Json[];
  const tip = alts.map((h) => `${beliefText(attr, h.value, currency)}  share ${pct(h.share)}  [${(h.hosts ?? []).join(", ")}]`).join("\n");
  return (
    <span title={tip}>
      <span className={b.conflict ? "belief conflict" : "belief"}>{beliefText(attr, b.value, currency)}</span>
      <span className="tiny muted"> {pct(b.confidence)}·{b.n_sources}src</span>
      {b.conflict ? <span className="tag bad" title="competing values kept, not overwritten">conflict</span> : null}
      {b.n_claims && !b.fresh ? <span className="tag warn" title={`older than ttl ${ttlText(b.ttl_s)}`}>stale</span> : null}
    </span>
  );
}

function Funnel({ f }: { f: Json }) {
  const steps: [string, unknown][] = [
    ["mentions", f.mentions], ["units (deduped)", f.units], ["buildings", f.buildings],
    ["pass filters", f.passed_filters], ["filtered", f.filtered], ["deep research", f.shortlisted],
  ];
  return (
    <div className="funnel">
      {steps.map(([k, v]) => (
        <div key={k}><div className="v num">{num(v, 0)}</div><div className="k">{k}</div></div>
      ))}
    </div>
  );
}

function Geography({ g }: { g: Json }) {
  const regions = (g.regions ?? []) as Json[];
  if (!regions.length) return null;
  const acquired = Object.fromEntries(((g.regions_acquired ?? []) as Json[]).map((r) => [r.id, r]));
  const ref = g.ref_currency ?? "JPY";
  return (
    <>
      <div className="sub">Where to live — regions compete</div>
      <div className="tiny muted">{g.principal?.home_evidence ? `evidence: ${g.principal.home_evidence}; ` : ""}{g.geography_rationale}</div>
      <table className="t">
        <thead><tr><th>region</th><th className="r">price signal</th><th className="r">utility</th><th className="r">P(may live there)</th><th className="r">distance</th><th className="r">units</th></tr></thead>
        <tbody>
          {regions.map((r) => (
            <tr key={r.id}>
              <td>{r.name} <span className="tiny muted">{r.country}{r.home ? " · home evidence" : ""}</span></td>
              <td className="r num">{r.price_ref ? money(r.price_ref, ref) : "—"}</td>
              <td className="r num" title={JSON.stringify(r.utility_parts)}>{num(r.utility, 3)}</td>
              <td className="r num">{pct(r.stay_p)}</td>
              <td className="r num">{r.distance_km != null ? `${num(r.distance_km, 0)} km` : "—"}</td>
              <td className="r num">{acquired[r.id]?.units ?? "…"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {(g.regions_rejected ?? []).length ? (
        <details><summary className="tiny muted">{g.regions_rejected.length} regions considered and not acquired</summary>
          {(g.regions_rejected as Json[]).map((r, i) => (
            <div key={i} className="tiny">{r.name} ({r.country}) — {r.reason}{r.price_ref ? `, ${money(r.price_ref, ref)}` : ""}</div>
          ))}
        </details>
      ) : null}
    </>
  );
}

function Drawer({ id, onClose }: { id: string; onClose: () => void }) {
  const [d, setD] = useState<Json | null>(null);
  useEffect(() => { api.acqEntity(id).then(setD).catch(() => setD({ error: true })); }, [id]);
  if (!d) return <div className="drawer"><div className="muted">Loading…</div></div>;
  if (d.error) return <div className="drawer"><div className="muted">Could not load entity.</div></div>;
  const attrs = Object.entries(d.attributes as Record<string, Json>);
  const cur = (d.attributes as Json).currency?.belief?.value as string | undefined;
  return (
    <div className="drawer" role="dialog" aria-label="Entity claims">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3>{d.parent?.label ? `${d.parent.label} — ` : ""}{d.entity.label}</h3>
        <button className="btn ghost" onClick={onClose}>Close</button>
      </div>
      <div className="tiny muted">{d.entity.id} · stage {d.entity.stage} · score {num(d.entity.score, 3)} · {d.entity.mention_count} mentions from {(d.entity.source_hosts ?? []).join(", ")}</div>
      {(d.entity.score_detail?.rejections ?? []).length ? (
        <div className="small" style={{ marginTop: 4 }}>Rejected: {d.entity.score_detail.rejections.join("; ")}</div>
      ) : null}
      <div className="sub">Claims by attribute (latest first)</div>
      {attrs.map(([a, x]) => (
        <details key={a} open={x.belief.conflict || ["rent", "availability"].includes(a)}>
          <summary>
            <span className="mono">{a}</span> = <Belief b={x.belief} attr={a} currency={cur} />
            <span className="tiny muted"> ttl {ttlText(x.belief.ttl_s)}</span>
          </summary>
          <table className="t">
            <thead><tr><th>value</th><th>source</th><th className="r">conf</th><th>observed</th><th>evidence</th></tr></thead>
            <tbody>
              {(x.claims as Json[]).map((c) => (
                <tr key={c.id}>
                  <td className="mono">{beliefText(a, c.value, cur)}</td>
                  <td><a href={c.url} target="_blank" rel="noreferrer">{c.source_host}</a> <span className="tiny muted">{c.source_kind}</span></td>
                  <td className="r num">{num(c.confidence, 2)}</td>
                  <td className="tiny">{ago(c.observed_at)}</td>
                  <td className="tiny muted">{c.evidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ))}
      {d.parent ? (
        <>
          <div className="sub">Building</div>
          <div className="small">{Object.entries(d.parent.attrs as Record<string, Json>).map(([a, b]) => (
            <div key={a}><span className="mono">{a}</span>: <Belief b={b} attr={a} /></div>
          ))}</div>
        </>
      ) : null}
      <div className="sub">Identity decisions</div>
      <table className="t">
        <tbody>
          {(d.resolution_links as Json[]).slice(0, 12).map((l) => (
            <tr key={l.id}><td><Status s={l.decision} /></td><td className="num">{num(l.probability, 3)}</td>
              <td className="tiny muted mono">{JSON.stringify(l.features)}</td></tr>
          ))}
        </tbody>
      </table>
      {(d.jobs as Json[]).length ? (
        <>
          <div className="sub">Enrichment jobs</div>
          {(d.jobs as Json[]).map((j) => (
            <div key={j.id} className="small"><Status s={j.status} /> <span className="mono">{j.kind}</span> <span className="muted">{j.reason}</span>{j.error ? <span className="err"> {j.error}</span> : null}</div>
          ))}
        </>
      ) : null}
    </div>
  );
}

export function AcquisitionPanel({ missionId }: { missionId: string }) {
  const [o, setO] = useState<Json | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.acqOverview(missionId).then((x) => alive && setO(x)).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => { alive = false; clearInterval(t); };
  }, [missionId]);
  if (!o || (!o.requests.length && !o.candidates.length)) return null;
  const running = (o.requests as Json[]).find((r) => r.status === "running");
  return (
    <Panel title="World acquisition" id="acquisition"
           aside={<>{running ? <Status s="running" /> : null} {o.documents} pages · {o.claims} claims · {o.open_conflicts} open conflicts</>}>
      <Funnel f={o.funnel} />
      <div className="tiny muted">
        resolution: {Object.entries(o.resolution as Json).map(([k, v]) => `${k} ${v}`).join(" · ")} · {o.multi_source_units} units seen on ≥2 sites
      </div>
      <Geography g={o.geography ?? {}} />
      <div className="sub">Sources</div>
      <table className="t">
        <thead><tr><th>host</th><th>kind</th><th className="r">pages</th><th className="r">records</th><th>access</th><th className="r">reliability</th></tr></thead>
        <tbody>
          {(o.sources as Json[]).map((s) => (
            <tr key={s.host}>
              <td className="mono">{s.host}</td><td className="tiny">{s.kind}</td>
              <td className="r num">{s.ok}/{s.fetches}</td><td className="r num">{s.records}</td>
              <td className="tiny">{s.blocked ? <span className="tag bad">blocked {s.blocked}</span> : null}
                {s.robots_disallowed ? <span className="tag warn">robots {s.robots_disallowed}</span> : null}
                <span className="muted"> {s.last_status}</span></td>
              <td className="r num" title={`kind prior ${s.kind_prior}; ${s.reliability_n} reconciled claims`}>{num(s.reliability, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="sub">Candidates (shortlisted first) — click for claims</div>
      <div className="scrollx">
        <table className="t">
          <thead><tr><th>stage</th><th>region</th><th>building / unit</th>{COLS.map(([k, l]) => <th key={k}>{l}</th>)}<th>sources</th><th className="r">score</th></tr></thead>
          <tbody>
            {(o.candidates as Json[]).map((c) => (
              <tr key={c.id} className="click" onClick={() => setOpen(c.id)}>
                <td><Status s={c.stage} /></td>
                <td className="tiny">{c.region ?? "—"}</td>
                <td><div>{c.building?.label ?? c.attrs.title?.value ?? "—"}</div><div className="tiny muted">{c.label}</div></td>
                {COLS.map(([k]) => <td key={k}><Belief b={c.attrs[k]} attr={k} currency={c.attrs.currency?.value} /></td>)}
                <td className="tiny">{(c.sources ?? []).join(" ")}</td>
                <td className="r num">{num(c.score, 3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details>
        <summary className="sub">Acquisition log</summary>
        {(o.discovery_log as Json[]).map((l, i) => (
          <div key={i} className="tiny"><span className="mono muted">{l.stage}</span> {l.message}</div>
        ))}
      </details>
      {open ? <Drawer id={open} onClose={() => setOpen(null)} /> : null}
    </Panel>
  );
}
