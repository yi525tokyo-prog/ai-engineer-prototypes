// Typed client for the Regent API (same-origin via Next rewrites).

export type Json = Record<string, any>;

export interface Operation {
  id: string; key: string; goal: string; kind: string; executor: string; tool: string; action: string;
  required_authority: "AUTO" | "COMMIT" | "IDENTITY"; authority_decision: Json; status: string;
  inputs: Json; outputs: Json; error: string | null; attempts: number; attempt_log: Json[];
  verification: Json; verification_result: Json; evidence_ids: string[]; depends_on: string[];
  resolves: string[]; route_id: string | null; mission_id: string; priority: number;
  started_at: string | null; finished_at: string | null; created_at: string;
}

export interface Route {
  id: string; key: string; title: string; thesis: string; archetype: string; status: string;
  generated_by: string[]; estimates: Json; effective: Json; estimate_sources: Json;
  applied_sensitivities: Json[]; score: number; rank: number; score_breakdown: Json; tags: string[];
  critiques: Json[]; uncertainty: Json[]; required_capabilities: string[]; invalidated_reason: string | null;
  evidence_ids: string[]; operations?: Operation[];
}

export interface Interrupt {
  id: string; mission_id: string; operation_id: string | null; kind: string; reason: string;
  required_action: string; estimated_time_seconds: number; blocking_operation: string | null;
  resume_condition: Json; response_schema: Record<string, { type: string; label?: string }>;
  context: Json; status: string; resolution: string | null; created_at: string; resolved_at: string | null;
}

export interface Decision {
  id: string; kind: string; tick: number; summary: string; snapshot_id: string | null; event_seq: number;
  routes_considered: Json[]; selected_route_id: string | null; previous_route_id: string | null;
  rationale: Json; evidence_ids: string[]; authority: Json; created_at: string;
}

export interface EventRow { seq: number; id: string; type: string; source: string; at: string; text: string; payload: Json }

export interface Cockpit {
  mission: Json & { id: string; title: string; objective: string; status: string; phase: string; tick_count: number;
    criteria: Json[]; children: Json[]; parent: Json | null };
  now: { events: EventRow[]; since_seq: number; new_since_last_strategy: number; world_diff: Json };
  best_route: Route | null;
  why: { last_decision: Decision | null; evaluation: Json | null; uncertainties: Json[] };
  executing: { phase: string; status: string; last_tick: Json | null; operations: Operation[] };
  blocked_by_you: Interrupt[];
  interrupt_history: Interrupt[];
  alternatives: Route[];
  score_history: Record<string, { tick: number; score: number; rank: number }[]>;
  changes: Decision[];
  decisions: Decision[];
  evidence: Json[];
  world: Json;
  treasury: Json;
  constitution: Record<string, Json[]>;
  model_calls: Json[];
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { cache: "no-store", ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) } });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const api = {
  missions: () => req<Json[]>("/api/missions"),
  cockpit: (id: string) => req<Cockpit>(`/api/missions/${id}/cockpit`),
  system: () => req<Json>("/api/system"),
  tick: (id: string) => req<Json>(`/api/missions/${id}/tick`, { method: "POST" }),
  resolve: (id: string, response: Json, resolution = "completed") =>
    req<Json>(`/api/interrupts/${id}/resolve`, { method: "POST", body: JSON.stringify({ response, resolution }) }),
  override: (mid: string, route_id: string, note = "") =>
    req<Json>(`/api/missions/${mid}/override`, { method: "POST", body: JSON.stringify({ route_id, note }) }),
  reject: (mid: string, route_id: string, note = "") =>
    req<Json>(`/api/missions/${mid}/reject`, { method: "POST", body: JSON.stringify({ route_id, note }) }),
  seed: () => req<Json>("/api/sim/seed", { method: "POST", body: JSON.stringify({ reset: true, run: true }) }),
  script: () => req<{ key: string; label: string }[]>("/api/sim/script"),
  applyScript: (key: string) => req<Json>(`/api/sim/script/${key}`, { method: "POST" }),
  tell: (text: string) => req<Json>("/api/ingest/text", { method: "POST", body: JSON.stringify({ text }) }),
  event: (type: string, payload: Json) =>
    req<Json>("/api/events", { method: "POST", body: JSON.stringify({ type, payload, source: "principal" }) }),
  decision: (id: string) => req<Json>(`/api/decisions/${id}`),
};
