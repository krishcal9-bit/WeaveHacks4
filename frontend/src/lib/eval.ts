// Frontend-safe types + a read-only client for the W&B Weave evaluation /
// replay / promotion operating system. Kept in its own module so it is purely
// additive — it does not touch the shared `api`/`types` surfaces other workers
// own. Mirrors agent/src/weave_eval.py, replay_sets.py, promotion_gates.py and
// the /api/evals* endpoints in agent/src/api.py.

import type { JsonValue, LearningReport } from "./types";

import { agentBase } from "./agent-base";

const BASE = agentBase();

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok && res.status !== 503) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

async function postJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "POST", cache: "no-store" });
  if (!res.ok && res.status !== 503) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) search.set(key, String(value));
  }
  const out = search.toString();
  return out ? `?${out}` : "";
}

// --------------------------------------------------------------------------- //
// Types
// --------------------------------------------------------------------------- //
export interface WeaveLinks {
  initialized?: boolean;
  project?: string | null;
  entity?: string | null;
  url?: string | null;
  uri?: string;
  name?: string;
  digest?: string;
  published?: boolean;
  error?: string;
  [k: string]: unknown;
}

export interface EvalRubricScore {
  dimension: string;
  label: string;
  score: number;
  weight?: number;
  threshold?: number;
  passed?: boolean;
  evidence?: string;
  metrics?: Record<string, JsonValue>;
}

export interface TraceQualityIssue {
  id?: string;
  node: string;
  severity: "low" | "medium" | "high" | string;
  summary: string;
  recommendation?: string;
}

export interface EvalPacket {
  id: string;
  created_at: string;
  created_ts?: number;
  source: string;
  decision: string;
  decision_label?: string;
  recommendation?: Record<string, JsonValue>;
  company?: string;
  model?: string;
  weave?: WeaveLinks;
  rubric_scores?: EvalRubricScore[];
  overall_score?: number;
  reliability_scores?: { agent_id?: string; reliability?: number }[];
  council_average?: number;
  trace_quality_issues?: TraceQualityIssue[];
  replay_set?: string | null;
  prompt_versions?: Record<string, JsonValue>[];
  learning_report?: LearningReport;
  _id?: string;
  [k: string]: unknown;
}

export interface ReplayCase {
  id: string;
  source: string;
  decision: string;
  expected_decision?: string | null;
  expected_confidence?: number | null;
  baseline_reliability?: number | null;
  tags?: string[];
  origin_event_id?: string | null;
  context?: Record<string, JsonValue>;
  created_at?: string;
}

export interface ReplaySetSummary {
  name: string;
  slug: string;
  description?: string;
  created_at?: string;
  case_count?: number;
  history_cases?: number;
  live_cases?: number;
  weave?: WeaveLinks;
}

export interface ReplaySet extends ReplaySetSummary {
  cases?: ReplayCase[];
}

export interface EnforcedGate {
  name: string;
  kind: "hard" | "soft" | string;
  rule: string;
  threshold: string;
}

export interface PromotionCandidate {
  id: string;
  agent_id: string;
  version_label: string;
  incumbent_label: string;
  prompt_adjustment?: string;
  promotion_gate?: string;
  replay_set?: string | null;
  status: "proposed" | "replaying" | "blocked" | "approved" | "needs_review" | string;
  created_at?: string;
  updated_at?: string;
  last_gate_id?: string | null;
}

export interface GateResult {
  name: string;
  label: string;
  kind: "hard" | "soft" | string;
  passed: boolean;
  incumbent?: number | null;
  candidate?: number | null;
  delta?: number | null;
  threshold?: number;
  detail?: string;
}

export interface GateDecision {
  id: string;
  candidate_id: string;
  candidate_label: string;
  incumbent_label: string;
  agent_id: string;
  replay_set?: string | null;
  status: "blocked" | "approved" | "needs_review" | string;
  decided_by?: "auto" | "human" | string;
  gates?: GateResult[];
  score_deltas?: Record<string, number>;
  incumbent_scores?: Record<string, number>;
  candidate_scores?: Record<string, number>;
  case_count?: number;
  board_explanation?: string;
  trace_quality_issues?: TraceQualityIssue[];
  weave?: WeaveLinks & {
    incumbent?: Record<string, JsonValue>;
    candidate?: Record<string, JsonValue>;
  };
  created_at?: string;
  _id?: string;
  [k: string]: unknown;
}

export interface EvalSummary {
  packet_count?: number;
  average_overall?: number;
  latest_overall?: number | null;
  latest_id?: string | null;
  high_severity_issues?: number;
  weave?: WeaveLinks;
  namespace?: string;
  stream?: string;
}

export interface ReplaySummary {
  replay_set_count?: number;
  total_cases?: number;
  default?: string | null;
  sets?: { slug?: string; name?: string; case_count?: number }[];
  weave?: WeaveLinks;
}

export interface PromotionStatusSummary {
  counts?: Record<string, number>;
  candidate_count?: number;
  decided_candidates?: number;
  latest?: GateDecision | null;
  enforced_gates?: EnforcedGate[];
  thresholds?: Record<string, number>;
  weave?: WeaveLinks;
}

export interface EvalsResponse {
  summary?: EvalSummary;
  packets?: EvalPacket[];
  packet?: EvalPacket;
}

export interface ReplaySetsResponse {
  summary?: ReplaySummary;
  replay_sets?: ReplaySetSummary[];
}

export interface PromotionsResponse {
  summary?: PromotionStatusSummary;
  candidates?: PromotionCandidate[];
  promotions?: GateDecision[];
  enforced_gates?: EnforcedGate[];
}

export interface EvalObservability {
  ready?: boolean;
  mode?: string;
  weave?: WeaveLinks;
  evals?: EvalSummary;
  replay_sets?: ReplaySummary;
  promotions?: PromotionStatusSummary;
  recent_packets?: EvalPacket[];
  recent_promotions?: GateDecision[];
  enforced_gates?: EnforcedGate[];
  blockers?: string[];
}

// The extra fields the Reliability Auditor streams onto LearningReport. The base
// LearningReport already carries them at runtime via its index signature; this is
// a typed view for read-only eval surfaces.
export interface LearningReportEval extends LearningReport {
  weave_entity?: string | null;
  replay_set?: string;
  replay_set_slug?: string;
  enforced_gates?: EnforcedGate[];
  gate_status?: Record<string, number>;
  promotion_candidates?: number;
  score_deltas?: Record<string, number> | null;
  latest_gate?: { candidate?: string | null; status?: string | null; replay_set?: string | null } | null;
  eval_packet_id?: string | null;
  eval_overall_score?: number | null;
  rubric_scores?: EvalRubricScore[];
  trace_quality_issues?: TraceQualityIssue[];
  weave_eval?: WeaveLinks;
}

// --------------------------------------------------------------------------- //
// Read-only client (+ explicit write actions for the promotion workflow)
// --------------------------------------------------------------------------- //
export const evalApi = {
  evals: (limit = 25) => getJSON<EvalsResponse>(`/api/evals${qs({ limit })}`),
  evalPacket: (id: string) => getJSON<EvalsResponse>(`/api/evals${qs({ id })}`),
  replaySets: () => getJSON<ReplaySetsResponse>("/api/evals/replay-sets"),
  replaySet: (slug: string) => getJSON<ReplaySet>(`/api/evals/replay-sets${qs({ slug })}`),
  createReplaySet: (opts: { name?: string; description?: string; limit?: number } = {}) =>
    postJSON<ReplaySetSummary>(`/api/evals/replay-sets${qs(opts)}`),
  promotions: (limit = 25) => getJSON<PromotionsResponse>(`/api/evals/promotions${qs({ limit })}`),
  // Live replay of a candidate vs incumbent → GateDecision (makes real model calls).
  runReplay: (candidate: string, opts: { replay_set?: string; max_cases?: number } = {}) =>
    postJSON<{ decision: GateDecision; weave?: WeaveLinks }>(
      `/api/evals/promotions${qs({ action: "replay", candidate, ...opts })}`,
    ),
  blockCandidate: (candidate: string, replay_set?: string) =>
    postJSON<{ decision: GateDecision }>(
      `/api/evals/promotions${qs({ action: "block", candidate, replay_set })}`,
    ),
  markCandidate: (candidate: string, status: "approved" | "blocked" | "needs_review", note?: string) =>
    postJSON<{ gate_id: string; status: string }>(
      `/api/evals/promotions${qs({ action: "mark", candidate, status, note })}`,
    ),
  observability: () => getJSON<EvalObservability>("/api/observability/evals"),
};
