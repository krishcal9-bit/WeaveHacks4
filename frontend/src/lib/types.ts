// Shapes mirroring the agent's Redis data, readiness checks, and LangGraph debate state.

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export interface CompanyFinancials {
  id?: string;
  name: string;
  stage: string;
  sector?: string;
  hq?: string;
  founded?: number;
  updated?: string;
  headcount: number;
  cash_on_hand: number;
  monthly_revenue: number;
  cogs_monthly?: number;
  monthly_gross_burn: number;
  monthly_net_burn: number;
  runway_months: number;
  mrr: number;
  arr: number;
  mrr_growth_mom: number;
  gross_margin: number;
  logo_churn_mom: number;
  ndr: number;
  cac: number;
  ltv: number;
  magic_number?: number;
  opex_monthly: { rd: number; sm: number; ga: number };
  last_raise?: {
    round: string;
    amount: number;
    date: string;
    lead: string;
    post_money: number;
  };
  cash_history: { month: string; cash: number; net_burn: number }[];
}

export interface Vendor {
  id?: string;
  name: string;
  category: string;
  annual_cost: number;
  monthly_cost: number;
  renewal_date: string;
  status: string;
  notes?: string;
}

export interface DecisionEvent {
  _id: string;
  title: string;
  summary?: string;
  decision?: string;
  confidence?: number;
  source?: string;
  [k: string]: unknown;
}

export type TurnType = "framing" | "position" | "rebuttal" | "decision";
export type Stance = "support" | "oppose" | "conditional";

export interface TranscriptTurn {
  agent?: string;
  label?: string;
  role?: string;
  monogram?: string;
  type: TurnType;
  stance?: Stance | string;
  headline?: string;
  argument?: string;
  key_points?: string[];
  // rebuttal-only
  from_role?: string;
  to_role?: string;
  point?: string;
}

export interface RunwayImpact {
  current_runway_months?: number;
  scenario_runway_months?: number | null;
  delta_months?: number | null;
  note?: string;
  [k: string]: unknown;
}

export interface Recommendation {
  decision?: string;
  confidence?: number;
  rationale?: string;
  key_risks?: string[];
  conditions?: string[];
  impact?: RunwayImpact;
}

export interface DebateState {
  decision?: string;
  phase?: string;
  current_phase?: string;
  context?: {
    financials?: CompanyFinancials;
    vendors?: Vendor[];
    policies?: JsonValue;
    [k: string]: unknown;
  };
  positions?: TranscriptTurn[];
  transcript?: TranscriptTurn[];
  recommendation?: Recommendation;
  agent_statuses?: AgentStatus[];
  observability_events?: ObservabilityEvent[];
  trace_summary?: TraceSummary;
  redis_activity?: RedisActivity[];
  sponsor_health?: SponsorHealth | SponsorCheck[];
}

export interface RosterMember {
  id: string;
  label: string;
  role: string;
  monogram: string;
  mandate?: string;
}

export interface SponsorCheck {
  id?: string;
  label: string;
  ready: boolean;
  detail?: string | null;
  error?: string | null;
  url?: string | null;
  checks?: SponsorCheck[];
  [k: string]: unknown;
}

export interface WeaveStatus {
  configured: boolean;
  initialized: boolean;
  project: string;
  entity?: string | null;
  error?: string | null;
  url?: string | null;
  [k: string]: unknown;
}

export interface SponsorHealth {
  ready: boolean;
  mode: string;
  blockers: string[];
  env: SponsorCheck[];
  sponsors: SponsorCheck[];
  weave: WeaveStatus;
  [k: string]: unknown;
}

export type AgentStatusKind =
  | "idle"
  | "queued"
  | "running"
  | "thinking"
  | "speaking"
  | "complete"
  | "done"
  | "blocked"
  | "error";

export interface AgentStatus {
  id: string;
  status: AgentStatusKind | string;
  label?: string;
  role?: string;
  monogram?: string;
  mandate?: string;
  headline?: string;
  stance?: Stance | string;
  ready?: boolean;
  phase?: string;
  node?: string;
  message?: string;
  detail?: string;
  error?: string | null;
  updated_at?: string;
  last_seen?: string;
  last_update?: string;
  current_turn?: TranscriptTurn;
  [k: string]: unknown;
}

export interface ObservabilityEvent {
  _id?: string;
  id?: string;
  at?: string;
  sponsor?: string;
  label?: string;
  detail?: string;
  tone?: string;
  event?: string;
  type?: string;
  title?: string;
  summary?: string;
  decision?: string;
  confidence?: number;
  source?: string;
  stream?: string;
  channel?: string;
  agent?: string;
  node?: string;
  status?: string;
  timestamp?: string;
  created_at?: string;
  payload?: Record<string, JsonValue>;
  fields?: Record<string, JsonValue>;
  [k: string]: unknown;
}

export interface TraceSummary {
  id?: string;
  trace_id?: string;
  call_id?: string;
  name?: string;
  op_name?: string;
  node?: string;
  model?: string;
  model_calls?: number;
  tool_calls?: number;
  weave_project?: string;
  weave_url?: string | null;
  updated_at?: string;
  status?: string;
  url?: string | null;
  project?: string;
  entity?: string | null;
  started_at?: string;
  ended_at?: string;
  duration_ms?: number;
  latency_ms?: number;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  cost_usd?: number;
  error?: string | null;
  spans?: TraceSummary[];
  [k: string]: unknown;
}

export interface RedisActivity {
  key?: string;
  label?: string;
  detail?: string;
  kind?: string;
  name?: string;
  type?: string;
  count?: number;
  length?: number;
  memory_bytes?: number;
  ttl_seconds?: number | null;
  last_id?: string;
  entries?: ObservabilityEvent[];
  checks?: SponsorCheck[];
  streams?: Record<string, number>;
  pubsub?: Record<string, JsonValue>;
  [k: string]: unknown;
}

export interface ObservabilitySnapshot {
  ready?: boolean;
  mode?: string;
  generated_at?: string;
  health?: SponsorHealth;
  sponsor_health?: SponsorCheck[];
  blockers?: string[];
  sponsors?: SponsorCheck[];
  weave?: WeaveStatus;
  agents?: AgentStatus[];
  events: ObservabilityEvent[];
  traces?: TraceSummary[];
  redis?: RedisActivity | RedisActivity[];
  redis_activity?: RedisActivity[];
  [k: string]: unknown;
}
