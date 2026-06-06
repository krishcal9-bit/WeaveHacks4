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
  cash_forecast?: {
    month: string;
    base_cash: number;
    downside_cash: number;
    net_burn: number;
    weighted_pipeline_arr?: number;
  }[];
  pipeline_by_stage?: {
    stage: string;
    opportunities: number;
    arr: number;
    weighted_arr: number;
    risk?: string;
  }[];
  customer_cohorts?: {
    segment: string;
    customers: number;
    mrr: number;
    logo_churn_mom?: number;
    ndr?: number;
    risk?: string;
  }[];
  hiring_plan?: {
    team: string;
    roles: number;
    monthly_cost: number;
    start_month: string;
    dependency?: string;
  }[];
  security_incidents?: {
    date: string;
    severity: string;
    summary: string;
    cash_risk?: number;
    status?: string;
  }[];
  audit_findings?: {
    id: string;
    area: string;
    severity: string;
    finding: string;
    due?: string;
  }[];
  board_constraints?: string[];
  decision_outcomes?: {
    decision_id: string;
    owner: string;
    predicted: string;
    actual: string;
    outcome: string;
    calibration_score?: number;
  }[];
  prompt_versions?: {
    agent: string;
    current: string;
    candidate: string;
    promotion_gate: string;
  }[];
  // Forward-compatible: booked revenue/ARR history if a parallel worker adds it.
  // The dashboard prefers these for the revenue trend and otherwise falls back to
  // the weighted-pipeline-ARR build from cash_forecast.
  arr_history?: { month: string; arr: number; mrr?: number }[];
  revenue_history?: { month: string; revenue: number }[];
}

export interface Vendor {
  id?: string;
  name: string;
  category: string;
  annual_cost: number;
  monthly_cost: number;
  renewal_date: string;
  status: string;
  owner?: string;
  termination_notice_days?: number;
  switching_cost?: number;
  data_sensitivity?: string;
  notes?: string;
}

export interface DecisionEvent {
  _id: string;
  title: string;
  summary?: string;
  decision?: string;
  confidence?: number;
  reliability_scores?: ReliabilityScore[];
  learning_report?: LearningReport;
  source?: string;
  // Governance status of the recommendation (set by the governance + persist
  // nodes). Recorded as pending or system-generated — never falsely human-approved.
  approval_status?: ApprovalStatus | string;
  approval_status_label?: string;
  approval_id?: string;
  controls_flagged?: number;
  human_approvals_pending?: boolean;
  [k: string]: unknown;
}

export type TurnType = "framing" | "position" | "rebuttal" | "decision" | "reliability" | "challenge";
export type Stance = "support" | "oppose" | "conditional";

export interface TranscriptTurn {
  id?: string;
  at?: string;
  timestamp?: string;
  node?: string;
  trace_id?: string;
  tokens?: number;
  cost_usd?: number;
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
  // optional, defensively consumed if a worker attaches grounding to a turn
  evidence?: EvidenceItem[];
  citations?: string[];
  // OpenAI-native council extras (analyst/challenge turns; feature-detected)
  cited_metrics?: string[];
  evidence_used?: string[];
  prompt_version?: string;
  error?: string;
}

// Grounding artifact behind a claim (Redis-backed). Optional everywhere.
export interface EvidenceItem {
  id?: string;
  source?: string; // "RedisJSON" | "RediSearch" | "Vector RAG" | sponsor name
  kind?: string; // financials | vendor | policy | precedent | metric | stream
  label?: string;
  detail?: string;
  value?: string | number;
  redis_key?: string;
  score?: number;
  url?: string | null;
  [k: string]: unknown;
}

// A semantic-search policy / precedent hit returned by search_finance_policies.
export interface PolicyHit {
  id?: string;
  title?: string;
  name?: string;
  text?: string;
  summary?: string;
  content?: string;
  score?: number;
  distance?: number;
  source?: string;
  kind?: string;
  [k: string]: unknown;
}

// Optional command surface contributed by parallel CopilotKit work. Feature-detected.
export interface CouncilCommand {
  id?: string;
  label: string;
  prompt?: string;
  description?: string;
  icon?: string;
  kind?: string;
  [k: string]: unknown;
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
  context?: CouncilContext;
  positions?: TranscriptTurn[];
  transcript?: TranscriptTurn[];
  recommendation?: Recommendation;
  agent_statuses?: AgentStatus[];
  observability_events?: ObservabilityEvent[];
  trace_summary?: TraceSummary;
  redis_activity?: RedisActivity[];
  sponsor_health?: SponsorHealth | SponsorCheck[];
  reliability_scores?: ReliabilityScore[];
  learning_report?: LearningReport;
  // Deterministic strategic-planning digital twin (agent/src/planning.py),
  // attached by the CFO synthesis node for multi-month plan prompts.
  strategic_plan?: StrategicPlan;
  // Governance outcome (policy controls, approval route, audit, obligations),
  // attached by the governance node (agent/src/governance.py).
  governance?: GovernanceState;
  // optional, feature-detected affordance from parallel CopilotKit work
  commands?: CouncilCommand[];
  // --- OpenAI-native council expansion (mirrors agent DebateState + -------- //
  // STREAM_STATE_KEYS; source in agent/src/openai_council.py + structured_models.py) //
  decision_type?: DecisionType | string;
  decision_plan?: DecisionPlanState;
  evidence_plan?: RoleEvidencePlan[];
  tool_plan?: ToolPlanEntry[];
  follow_up?: FollowUp;
  challenge_report?: ChallengeReport;
  evidence_gaps?: string[];
  board_memo?: BoardMemo;
  operator_actions?: OperatorAction[];
  model_telemetry?: ModelTelemetry;
  realtime_status?: RealtimeStatus;
  prompt_versions?: PromptVersion[];
  // --- AG-UI command-and-control layer (mirrors agent DebateState + ------- //
  // STREAM_STATE_KEYS; single source of truth in agent/src/agui_commands.py) //
  command_queue?: OperatorCommand[];
  active_command?: ActiveCommand;
  pinned_evidence?: PinnedEvidence[];
  requested_scenario?: RequestedScenario;
  agent_focus?: AgentFocus;
  phase_controls?: PhaseControls;
  export_status?: ExportStatus;
  command_audit_log?: CommandAuditEntry[];
}

// --------------------------------------------------------------------------- //
// AG-UI command-and-control protocol — mirrors agent/src/agui_commands.py and
// council_commands.py. (Distinct from the prompt-suggestion `CouncilCommand`
// surface above; this is the operator steering channel.)
// --------------------------------------------------------------------------- //
export type CommandType =
  | "clarify"
  | "route_question"
  | "challenge_claim"
  | "scenario_fork"
  | "compare_options"
  | "pin_evidence"
  | "pause_phase"
  | "resume_phase"
  | "export_memo";

export type CommandStatus = "queued" | "accepted" | "executed" | "rejected" | "failed";

export interface ScenarioParams {
  extra_monthly_spend?: number;
  one_time_cost?: number;
  added_monthly_revenue?: number;
}

export interface OperatorCommand {
  id?: string;
  type: CommandType;
  agent?: string;
  room?: string;
  source?: string;
  payload?: {
    question?: string;
    point?: string;
    claim?: string;
    label?: string;
    kind?: "policy" | "vendor" | "financial" | "custom";
    query?: string;
    note?: string;
    ref?: string;
    phase?: string;
    reason?: string;
    options?: Array<ScenarioParams & { label?: string }>;
    context?: {
      decision?: string;
      position?: Partial<TranscriptTurn>;
      [k: string]: unknown;
    };
    [k: string]: unknown;
  } & ScenarioParams;
  created_at?: string;
}

export interface ActiveCommand {
  id?: string;
  type?: CommandType;
  agent?: string;
  status?: CommandStatus;
  reason?: string | null;
  message?: string;
  payload?: Record<string, unknown>;
  result?: Record<string, unknown>;
  at?: string;
  stream_id?: string | null;
}

export interface PinnedEvidence {
  id: string;
  kind: "policy" | "vendor" | "financial" | "custom" | string;
  title?: string;
  detail?: string;
  source?: string;
  at?: string;
}

export interface ScenarioOption {
  label?: string;
  params?: ScenarioParams;
  impact?: RunwayImpact;
}

export interface RequestedScenario {
  id?: string;
  mode?: "single" | "compare";
  label?: string;
  params?: ScenarioParams;
  impact?: RunwayImpact;
  options?: ScenarioOption[];
  at?: string;
}

export interface AgentFocus {
  agent?: string;
  label?: string;
  mode?: "clarify" | "route" | "challenge" | string;
  question?: string;
  headline?: string;
  response?: string;
  key_points?: string[];
  revised_stance?: string;
  at?: string;
}

export interface PhaseControls {
  paused?: boolean;
  phase?: string | null;
  reason?: string | null;
  updated_at?: string | null;
}

export interface ExportStatus {
  ready?: boolean;
  id?: string;
  format?: string;
  generated_at?: string;
  title?: string;
  memo?: string;
  stream_id?: string | null;
}

export interface CommandAuditEntry {
  id?: string;
  type?: CommandType;
  agent?: string;
  status?: CommandStatus;
  reason?: string | null;
  summary?: string;
  at?: string;
  stream_id?: string | null;
  source?: string;
}

export interface CommandState {
  command_queue: OperatorCommand[];
  active_command: ActiveCommand;
  pinned_evidence: PinnedEvidence[];
  requested_scenario: RequestedScenario;
  agent_focus: AgentFocus;
  phase_controls: PhaseControls;
  export_status: ExportStatus;
  command_audit_log: CommandAuditEntry[];
}

export interface CommandResult {
  status: CommandStatus;
  reason?: string | null;
  message?: string;
  result: Record<string, unknown>;
  command: { id?: string; type?: CommandType; agent?: string };
  stream_id?: string | null;
  state: CommandState;
  room: string;
}

export interface CouncilContext {
  financials?: CompanyFinancials;
  vendors?: Vendor[];
  policies?: PolicyHit[] | JsonValue;
  [k: string]: unknown;
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
  capabilities?: string[];
  realtime?: {
    model?: string;
    reasoning_effort?: string;
    voice?: string;
    endpoint?: string;
    [k: string]: unknown;
  };
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
  uds?: AgentUdsSnapshot;
  reliability_score?: number;
  reliability_dimensions?: ReliabilityDimensions;
  reliability_rationale?: string;
  known_weaknesses?: string[];
  prompt_adjustment?: string;
  promotion_gate?: string;
  [k: string]: unknown;
}

export interface ReliabilityDimensions {
  outcome_accuracy?: number;
  evidence_grounding?: number;
  forecast_calibration?: number;
  policy_compliance?: number;
  debate_value?: number;
  confidence_calibration?: number;
  trace_quality?: number;
}

export interface ReliabilityScore extends ReliabilityDimensions {
  agent_id: string;
  reliability: number;
  rationale: string;
  known_weaknesses?: string[];
  prompt_adjustment?: string;
  promotion_gate?: string;
}

export interface LearningReport {
  summary?: string;
  eval_dataset?: string;
  replay_plan?: string[];
  promotion_gate?: string;
  score_formula?: Record<string, number>;
  weave_project?: string | null;
  weave_url?: string | null;
  [k: string]: unknown;
}

export interface AgentUdsSnapshot {
  current_task?: string;
  inputs?: string[];
  outputs?: string[];
  tools?: string[];
  trace_node?: string;
  redis_keys?: string[];
  sponsor_events?: string[];
  updated_at?: string;
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
  reasoning_effort?: string;
  text_verbosity?: string;
  realtime_model?: string;
  realtime_reasoning_effort?: string;
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
  // OpenAI-native observability (feature-detected)
  model_family?: string;
  tool_plan_size?: number | null;
  evidence_gap_count?: number | null;
  refusals?: number;
  errors?: number;
  spans?: TraceSummary[];
  [k: string]: unknown;
}

export interface RedisActivity {
  key?: string;
  at?: string;
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

export interface RealtimeSession {
  ready: boolean;
  model: string;
  reasoning_effort?: string;
  voice?: string;
  expires_at?: number | string | null;
  client_secret: string;
  // Robust control-surface metadata (agent/src/realtime.py mint_session)
  endpoint?: string;
  transport?: string;
  issued_at?: number | null;
  ttl_seconds?: number;
  seconds_remaining?: number | null;
  policy?: RealtimeStatus["policy"];
  health?: Record<string, unknown>;
  [k: string]: unknown;
}

// --------------------------------------------------------------------------- //
// OpenAI-native council expansion — mirrors agent/src/structured_models.py and
// the streamed DebateState fields set by the planner, analyst, challenge, and
// CFO synthesis nodes (agent/src/openai_council.py). All optional / feature-
// detected so the UI consumes them defensively.
// --------------------------------------------------------------------------- //
export type DecisionType =
  | "vendor_renewal"
  | "hiring_plan"
  | "capital_allocation"
  | "security_blocker"
  | "pricing_change"
  | "financing_scenario"
  | "general";

export interface RequiredFact {
  name: string;
  why?: string;
  available: boolean;
  source?: string;
}

export interface FollowUpQuestion {
  question: string;
  fact?: string;
  blocking?: boolean;
}

export interface RoleEvidencePlan {
  role: string;
  tools?: string[];
  policy_queries?: string[];
  focus_slices?: string[];
  prior_decisions?: string[];
  rationale?: string;
}

export interface DecisionPlanState {
  decision_type?: DecisionType | string;
  title?: string;
  summary?: string;
  entities?: string[];
  required_facts?: RequiredFact[];
  assumptions?: string[];
  follow_up_questions?: FollowUpQuestion[];
  role_plans?: RoleEvidencePlan[];
  decision_specific_focus?: string[];
  [k: string]: unknown;
}

export interface ToolPlanEntry {
  role?: string;
  tool?: string;
  target?: string;
  rationale?: string;
  kind?: string;
}

export interface FollowUp {
  needed?: boolean;
  questions?: FollowUpQuestion[];
  missing_facts?: string[];
  assumptions?: string[];
  source?: string;
}

export interface ChallengeFinding {
  role: string;
  cited_enough_numbers?: boolean;
  grounding_score?: number;
  strongest_number?: string;
  missing_evidence?: string[];
  challenge?: string;
}

export interface ChallengeReport {
  summary?: string;
  overall_grounding?: number | null;
  findings?: ChallengeFinding[];
  unresolved_gaps?: string[];
  error?: string;
  [k: string]: unknown;
}

export interface OperatorAction {
  owner?: string;
  action: string;
  due?: string;
  priority?: string;
  depends_on?: string;
}

export interface BoardMemo {
  title?: string;
  decision_type?: string;
  headline?: string;
  context?: string;
  recommendation?: string;
  key_figures?: string[];
  risks?: string[];
  conditions?: string[];
  operator_actions?: OperatorAction[];
  financing_or_next_steps?: string[];
  dissent?: string;
  [k: string]: unknown;
}

export interface ModelCallTelemetry {
  node?: string;
  role?: string;
  model?: string;
  model_family?: string;
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_tokens?: number | null;
  cost_usd?: number | null;
  refusal?: string | null;
  error?: string | null;
  attempts?: number;
  ok?: boolean;
}

export interface ModelTelemetry {
  provider?: string;
  model?: string;
  model_family?: string;
  reasoning_effort?: string;
  text_verbosity?: string;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  estimated_cost_usd?: number | null;
  cost_available?: boolean;
  pricing_source?: string;
  model_calls?: number;
  successful_calls?: number;
  calls?: ModelCallTelemetry[];
  refusals?: { node?: string; role?: string; detail?: string }[];
  errors?: { node?: string; role?: string; detail?: string }[];
  [k: string]: unknown;
}

export interface RealtimeStatus {
  id?: string;
  label?: string;
  ready?: boolean;
  detail?: string;
  model?: string;
  voice?: string;
  reasoning_effort?: string;
  endpoint?: string;
  transport?: string;
  ttl_seconds?: number;
  api_key_configured?: boolean;
  capabilities?: string[];
  policy?: Record<string, unknown>;
  checks?: SponsorCheck[];
  [k: string]: unknown;
}

export interface PromptVersion {
  role: string;
  version: string;
  prompt_hash: string;
  candidate?: string;
  promotion_gate?: string;
}

// --- Finance-operations connectors: ingestion, provenance, reconciliation ---

export type ImportStatus =
  | "not_configured"
  | "missing_file"
  | "imported"
  | "partial"
  | "empty"
  | "skipped_unchanged"
  | "error";

export type DiscrepancySeverity = "info" | "low" | "medium" | "high" | "critical";

export interface ImportConfidence {
  score: number;
  coverage: number;
  validation_pass_rate: number;
  freshness_days?: number | null;
  sources_imported: number;
  sources_total: number;
  detail: string;
  components: Record<string, number>;
}

export interface ConnectorStatus {
  connector_id: string;
  source_type: string;
  description: string;
  env_var: string;
  configured: boolean;
  configured_path?: string | null;
  demo_fixture_available: boolean;
  transport: string;
  status: ImportStatus | string;
  origin?: string | null;
  record_count: number;
  source_name?: string | null;
  source_timestamp?: string | null;
  imported_at?: string | null;
  checksum_sha256?: string | null;
  reconciliation_status: string;
  blockers: string[];
}

export interface ConnectorInventory {
  mode: string;
  connectors: ConnectorStatus[];
  confidence: ImportConfidence;
}

export interface ValidationIssue {
  location: string;
  field?: string | null;
  message: string;
}

export interface SourceProvenance {
  connector_id: string;
  source_type: string;
  origin: string;
  status: ImportStatus | string;
  env_var: string;
  schema_version: string;
  source_name?: string | null;
  source_path?: string | null;
  source_format?: string | null;
  source_timestamp?: string | null;
  imported_at?: string | null;
  checksum_sha256?: string | null;
  record_count: number;
  accepted_count: number;
  rejected_count: number;
  duplicate_count: number;
  reconciliation_status: string;
  blockers: string[];
  validation_errors: ValidationIssue[];
  [k: string]: unknown;
}

export interface SourceDetail {
  provenance: SourceProvenance;
  record_count: number;
  sample: Record<string, unknown>[];
}

export interface ConnectorImportResult {
  provenance: SourceProvenance;
  records: Record<string, unknown>[];
}

export interface ConnectorImportResponse {
  import_result: ConnectorImportResult;
  connectors: ConnectorStatus[];
  confidence: ImportConfidence;
  reconciliation: ReconciliationReport;
}

export interface Discrepancy {
  id: string;
  kind: string;
  severity: DiscrepancySeverity | string;
  title: string;
  detail: string;
  sources: string[];
  expected?: unknown;
  observed?: unknown;
  delta?: number | null;
  recommended_action: string;
  confidence: number;
  references?: Record<string, unknown>;
}

export interface WorkflowSummary {
  workflow: string;
  status: string;
  checked: number;
  discrepancy_count: number;
  detail: string;
  blockers: string[];
}

export interface ReconciliationReport {
  run_id?: string;
  generated_at?: string;
  schema_version?: string;
  status: string;
  workflows?: WorkflowSummary[];
  discrepancies: Discrepancy[];
  counts_by_severity?: Record<string, number>;
  confidence?: ImportConfidence;
  sources_considered?: string[];
  blockers?: string[];
  detail?: string;
  [k: string]: unknown;
}

export interface DemoResetResponse {
  status: string;
  deleted: Record<string, number>;
  connectors: ConnectorStatus[];
  confidence: ImportConfidence;
  command_state?: CommandState;
}

// --------------------------------------------------------------------------- //
// Strategic planning digital twin — mirrors agent/src/planning.py,
// playbooks.py, and stress_tests.py. Every figure is computed deterministically;
// only the BoardNarrative prose is model-generated (and cites the figures it was
// given via deterministic_basis).
// --------------------------------------------------------------------------- //
export interface ScenarioAssumption {
  key: string;
  label: string;
  value: number;
  unit: string;
  source: "system_of_record" | "derived" | "playbook" | "override" | string;
  rationale?: string;
}

export interface PlaybookStep {
  order: number;
  action: string;
  owner: string;
  kind: "hire" | "vendor_savings" | "spend" | "revenue_unlock" | "financing" | "cut" | "policy";
  start_month_index: number;
  financial_effect: Record<string, number>;
  dependency?: string;
  reversible?: boolean;
  detail?: string;
}

export interface CapitalPlan {
  instrument: string;
  raise_amount: number;
  close_month?: string | null;
  close_month_index?: number | null;
  dilution_pct?: number | null;
  runway_extension_months?: number | null;
  triggers?: string[];
  notes?: string;
}

export interface Milestone {
  id: string;
  month: string;
  month_index: number;
  label: string;
  category: "runway" | "cash" | "revenue" | "compliance" | "hiring" | "financing" | "efficiency";
  metric?: string;
  target?: number | null;
  projected?: number | null;
  comparator?: ">=" | "<=" | "==" | "n/a";
  status: "met" | "on_track" | "at_risk" | "missed" | "scheduled";
  depends_on?: string[];
  source?: string;
}

export interface MonthProjection {
  month: string;
  month_index: number;
  headcount: number;
  mrr: number;
  arr: number;
  revenue: number;
  new_mrr: number;
  churned_mrr: number;
  cogs: number;
  opex: number;
  gross_burn: number;
  net_burn: number;
  one_time_cost: number;
  financing_inflow: number;
  cash_begin: number;
  cash_end: number;
  runway_months: number | null;
  gross_margin: number;
}

export interface PolicyBlocker {
  policy: string;
  severity: "info" | "warning" | "high" | "critical";
  source?: string;
  month?: string | null;
  detail: string;
}

export interface PlanSummary {
  horizon_months?: number;
  start_month?: string;
  end_month?: string;
  starting_cash?: number;
  ending_cash?: number;
  min_cash?: number;
  min_cash_month?: string;
  starting_runway_months?: number | null;
  runway_at_horizon?: number | null;
  lowest_runway_months?: number | null;
  lowest_runway_month?: string | null;
  starting_mrr?: number;
  ending_mrr?: number;
  ending_arr?: number;
  arr_growth_pct?: number;
  total_net_burn?: number;
  total_financing?: number;
  total_one_time?: number;
  cash_flow_positive_month?: string | null;
  months_below_runway_floor?: number;
  months_below_cash_buffer?: number;
  breaches_runway_floor?: boolean;
  breaches_cash_buffer?: boolean;
  goes_insolvent?: boolean;
  insolvent_month?: string | null;
  ending_headcount?: number;
  [k: string]: unknown;
}

export interface StrategicPlan {
  id: string;
  title: string;
  horizon_months: number;
  created_at: string;
  start_month: string;
  playbook_id?: string | null;
  playbook_label?: string | null;
  objective?: string;
  company?: string;
  assumptions: ScenarioAssumption[];
  steps: PlaybookStep[];
  capital_plan: CapitalPlan;
  projection: MonthProjection[];
  milestones: Milestone[];
  policy_blockers: PolicyBlocker[];
  summary: PlanSummary;
  risks?: string[];
  monitoring_triggers?: string[];
  provenance?: Record<string, unknown>;
  calc_metadata?: Record<string, unknown>;
}

export interface PlanCard {
  id: string;
  title: string;
  playbook?: string | null;
  horizon_months: number;
  created_at?: string;
  summary: PlanSummary;
  policy_blockers?: number;
}

export interface PlaybookCatalogEntry {
  id: string;
  label: string;
  summary: string;
  keywords?: string[];
}

export interface SensitivityPoint {
  value: number;
  min_cash?: number | null;
  lowest_runway_months?: number | null;
  runway_at_horizon?: number | null;
  ending_arr?: number | null;
  output?: number | null;
  [k: string]: number | null | undefined;
}

export interface SensitivityResult {
  variable: string;
  label: string;
  unit: string;
  base_value: number;
  output_metric: string;
  base_output?: number | null;
  points: SensitivityPoint[];
  elasticity?: number | null;
  swing?: number | null;
  direction?: string;
  note?: string;
}

export interface SensitivitySuite {
  output_metric: string;
  horizon_months: number;
  results: SensitivityResult[];
  ranking: { variable: string; label: string; swing: number | null; elasticity: number | null }[];
  most_sensitive?: string | null;
  provenance?: Record<string, unknown>;
}

export interface PercentileBand {
  p5: number | null;
  p25: number | null;
  p50: number | null;
  p75: number | null;
  p95: number | null;
  mean: number | null;
  min: number | null;
  max: number | null;
}

export interface StressTest {
  id?: string;
  name: string;
  description?: string;
  trials: number;
  horizon_months: number;
  seed: number;
  distributions?: Record<string, Record<string, number>>;
  metrics: Record<string, PercentileBand>;
  prob_runway_breach?: number | null;
  prob_cash_negative?: number | null;
  prob_below_cash_buffer?: number | null;
  expected_breach_month?: string | null;
  worst_case?: Record<string, unknown>;
  base_case?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
}

export interface PortfolioCandidate {
  playbook_id: string;
  label: string;
  plan_id: string;
  score: number;
  score_breakdown: Record<string, number>;
  metrics: Record<string, number>;
  card: PlanCard;
  policy_blockers: PolicyBlocker[];
}

export interface PortfolioPick {
  playbook_id: string;
  label: string;
  role: "primary" | "no_regret" | "stabilizer" | string;
  weight: number;
}

export interface DecisionPortfolio {
  id?: string;
  decision: string;
  horizon_months: number;
  created_at?: string;
  candidates: PortfolioCandidate[];
  ranking: string[];
  recommended_portfolio: PortfolioPick[];
  rationale?: string;
  tradeoffs?: string[];
  scoring_weights?: Record<string, number>;
  provenance?: Record<string, unknown>;
}

export interface BoardNarrative {
  plan_id: string;
  headline?: string;
  narrative?: string;
  key_metrics?: { label?: string; value?: unknown; text?: string }[];
  risks?: string[];
  asks?: string[];
  recommended_decision?: string;
  generated_by?: string;
  deterministic_basis?: Record<string, unknown>;
  generated_at?: string;
}

// --------------------------------------------------------------------------- //
// Governance — policy controls, approval routing, audit, obligations.
// Mirrors agent/src/governance_models.py (read-only shapes for the dashboard).
// The integrity contract: actor_type is never auto-set to "human"; the system
// records "recommended"/"routed"/"auto_cleared", and a human sign-off is only
// ever an operator-supplied API decision.
// --------------------------------------------------------------------------- //
export type ApprovalStatus =
  | "draft"
  | "pending_approval"
  | "approved"
  | "rejected"
  | "conditionally_approved"
  | "expired"
  | "superseded";

export type ActorType = "system" | "agent" | "service" | "human";

export interface PolicyRule {
  id: string;
  control_id: string;
  title: string;
  category: string;
  severity?: string;
  text?: string;
  amount_threshold?: number | null;
  runway_floor_months?: number | null;
  margin_floor?: number | null;
  burn_growth_cap?: number | null;
  requires_board_notification?: boolean;
  requires_board_approval?: boolean;
  requires_security_review?: boolean;
  applies_to?: string[];
  evidence_required?: string[];
  remediation?: string;
  [k: string]: unknown;
}

export interface ControlViolation {
  control_id: string;
  policy_id: string;
  title: string;
  category: string;
  severity: string;
  message: string;
  observed?: string | null;
  limit?: string | null;
  blocking?: boolean;
  requires_exception?: boolean;
  requires_board?: boolean;
  requires_security_review?: boolean;
  remediation?: string;
  evidence_required?: string[];
}

export interface ApprovalStep {
  sequence: number;
  approver_role: string;
  approver_type: ActorType;
  reason: string;
  status: ApprovalStatus;
  decided_by?: string | null;
  decided_by_type?: ActorType | null;
  decided_at?: string | null;
  note?: string | null;
  policy_refs?: string[];
}

export interface ApprovalDecision {
  id: string;
  request_id: string;
  actor: string;
  actor_type: ActorType;
  action: string;
  status_after: ApprovalStatus;
  rationale: string;
  conditions?: string[];
  at: string;
  provenance?: string;
  step_sequence?: number | null;
}

export interface ExceptionRequest {
  id: string;
  request_id: string;
  policy_id: string;
  control_id: string;
  justification: string;
  requested_by: string;
  requested_by_type: ActorType;
  status: ApprovalStatus;
  compensating_controls?: string[];
  expires_at?: string | null;
  at: string;
}

export interface Obligation {
  id: string;
  request_id?: string | null;
  title: string;
  description: string;
  kind: string;
  owner_role: string;
  due_date?: string | null;
  status: string;
  source_policy?: string | null;
  evidence_required?: string[];
  created_at?: string;
}

export interface MonitoringTrigger {
  id: string;
  request_id?: string | null;
  kind: string;
  label: string;
  trigger_date?: string | null;
  condition?: string | null;
  metric?: string | null;
  target?: string | null;
  status: string;
  obligation_id?: string | null;
}

export interface AuditEvent {
  _id?: string;
  type: string;
  request_id?: string | null;
  actor: string;
  actor_type: ActorType;
  summary: string;
  at: string;
  payload?: Record<string, JsonValue>;
  [k: string]: unknown;
}

export interface GovernanceState {
  id?: string;
  company_id?: string;
  title?: string;
  decision_text?: string;
  recommendation?: Recommendation;
  status?: ApprovalStatus | string;
  status_label?: string;
  summary?: string;
  amount_annualized?: number;
  one_time_cost?: number;
  monthly_cost?: number;
  added_monthly_revenue?: number;
  department?: string;
  data_sensitivity?: string;
  risk_tier?: string;
  runway_before_months?: number | null;
  runway_after_months?: number | null;
  runway_delta_months?: number | null;
  route?: ApprovalStep[];
  violations?: ControlViolation[];
  decisions?: ApprovalDecision[];
  exceptions?: ExceptionRequest[];
  obligations?: Obligation[];
  monitoring?: MonitoringTrigger[];
  evidence_required?: string[];
  evidence_present?: string[];
  evidence_missing?: string[];
  blocked?: boolean;
  human_approvals_pending?: boolean;
  created_by?: string;
  created_by_type?: ActorType;
  created_at?: string;
  updated_at?: string;
  expires_at?: string | null;
  superseded_by?: string | null;
  error?: string;
  [k: string]: unknown;
}

export interface ApprovalMatrix {
  company_id?: string;
  currency?: string;
  expiry_days?: number;
  delegated_authority_max?: number;
  amount_tiers?: { min: number; max: number | null; approvers: string[] }[];
  [k: string]: unknown;
}
