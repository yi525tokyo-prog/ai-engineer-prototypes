"use client";
// Small, honest charts: diverging score components and single-series sparklines.
import { componentLabel, num, signed, sortedComponents, sparkPoints, symmetricMax } from "@/lib/format";

/** Diverging bars: each component's signed contribution to a route's score. */
export function ScoreBars({ components, compare, compareLabel }: {
  components: Record<string, number> | undefined;
  compare?: Record<string, number>;
  compareLabel?: string;
}) {
  const rows = sortedComponents(components).filter(([, v]) => Math.abs(v) > 0.0005);
  if (!rows.length) return <div className="muted">No score yet.</div>;
  const max = symmetricMax([...rows.map((r) => r[1]), ...Object.values(compare ?? {})]);
  return (
    <div className="bars" role="table" aria-label="Score components">
      {rows.map(([k, v]) => {
        const w = (Math.abs(v) / max) * 50;
        const other = compare?.[k];
        const tip = `${componentLabel(k)}: ${signed(v)}` + (other !== undefined ? ` (vs ${compareLabel ?? "other"} ${signed(other)})` : "");
        return (
          <div key={k} style={{ display: "contents" }} role="row">
            <div className="lab" role="cell" title={componentLabel(k)}>{componentLabel(k)}</div>
            <div className="bar" role="cell" title={tip}>
              <span className="axis" />
              <span className={`fill ${v >= 0 ? "pos" : "neg"}`} style={{ width: `${w}%` }} />
            </div>
            <div className="val" role="cell">{signed(v)}</div>
          </div>
        );
      })}
    </div>
  );
}

/** Score history for one route: gray line, current point emphasised. */
export function Sparkline({ values, width = 84, height = 18, label }: { values: number[]; width?: number; height?: number; label?: string }) {
  if (values.length < 2) return <svg width={width} height={height} aria-hidden />;
  const pts = sparkPoints(values, width, height, 3);
  const last = pts.split(" ").at(-1)!.split(",").map(Number);
  const lo = Math.min(...values), hi = Math.max(...values);
  return (
    <svg width={width} height={height} role="img" aria-label={`${label ?? "score"} history ${num(values[0], 3)} to ${num(values.at(-1), 3)}`}>
      <title>{`${label ?? "score"}: ${values.map((v) => num(v, 3)).join(" → ")} (range ${num(lo, 3)}–${num(hi, 3)})`}</title>
      <polyline points={pts} fill="none" stroke="var(--spark)" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={last[0]} cy={last[1]} r={2.5} fill="var(--accent)" />
    </svg>
  );
}

export function Meter({ value, max, label }: { value: number; max: number; label: string }) {
  const pctv = Math.max(0, Math.min(1, max ? value / max : 0));
  return (
    <div className="meter" role="meter" aria-label={label} aria-valuenow={value} aria-valuemin={0} aria-valuemax={max} title={`${label}: ${num(value)} / ${num(max)}`}>
      <span style={{ width: `${pctv * 100}%` }} />
    </div>
  );
}
