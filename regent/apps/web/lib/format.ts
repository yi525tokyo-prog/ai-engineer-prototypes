// Pure presentation helpers (unit-tested).

export function num(v: unknown, digits = 2): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  if (Math.abs(n) >= 1000) return Math.round(n).toLocaleString("en-US");
  return n.toFixed(digits).replace(/\.?0+$/, "") || "0";
}

export function signed(v: number, digits = 3): string {
  const s = v.toFixed(digits);
  return v > 0 ? `+${s}` : s;
}

export function pct(v: unknown): string {
  const n = Number(v);
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : "—";
}

export function ago(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function duration(seconds: number): string {
  if (seconds < 60) return `~${seconds}s`;
  return `~${Math.round(seconds / 60)} min`;
}

export const COMPONENT_LABELS: Record<string, string> = {
  expected_value: "Expected value",
  information_gain: "Information gain",
  optionality: "Optionality",
  reversibility: "Reversibility",
  time_cost: "Time cost",
  money_cost: "Money cost",
  risk: "Risk",
  authority_cost: "Authority cost",
};

export function componentLabel(k: string): string {
  if (k.startsWith("constitution:")) return `Constitution: ${k.slice(13).replace(/_/g, " ")}`;
  return COMPONENT_LABELS[k] ?? k.replace(/_/g, " ");
}

/** Components sorted by absolute contribution, for the WHY chart. */
export function sortedComponents(c: Record<string, number> | undefined): [string, number][] {
  return Object.entries(c ?? {}).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
}

/** Symmetric scale so positive and negative bars share one axis. */
export function symmetricMax(values: number[], floor = 0.05): number {
  return Math.max(floor, ...values.map((v) => Math.abs(v)));
}

/** Map a series to SVG polyline points within w x h (with padding). */
export function sparkPoints(values: number[], w: number, h: number, pad = 2): string {
  if (values.length === 0) return "";
  const lo = Math.min(...values), hi = Math.max(...values);
  const span = hi - lo || 1;
  const step = values.length > 1 ? (w - 2 * pad) / (values.length - 1) : 0;
  return values
    .map((v, i) => `${(pad + i * step).toFixed(1)},${(h - pad - ((v - lo) / span) * (h - 2 * pad)).toFixed(1)}`)
    .join(" ");
}

export function statusTone(status: string): "good" | "warn" | "bad" | "info" | "muted" {
  if (["succeeded", "completed", "selected", "resolved"].includes(status)) return "good";
  if (["waiting_human", "blocked", "pending", "unverified", "open", "acquiring", "degraded"].includes(status)) return "warn";
  if (["failed", "invalidated", "abandoned", "missing"].includes(status)) return "bad";
  if (["running", "active"].includes(status)) return "info";
  return "muted";
}

export function humanStatus(s: string): string {
  return s.replace(/_/g, " ");
}

/** Describe what changed between two effective-estimate maps. */
export function estimateDelta(changes: Record<string, { from: number; to: number }> | undefined): string[] {
  return Object.entries(changes ?? {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${num(v.from)} → ${num(v.to)}`);
}
