// Pure view-logic and shared view-types for the Decision Room.
// No React here — components stay thin and consistent by importing these helpers.

import type {
  AgentInfluence,
  AgentStatus,
  CouncilInfluenceReport,
  DebateState,
  ReliabilityScore,
  RosterMember,
  TranscriptTurn,
} from "./types";
import { ROSTER_BY_ID } from "./agents";

// --------------------------------------------------------------------------- //
// Tone system — color conveys information only (see globals.css design notes).
// --------------------------------------------------------------------------- //
export type Tone = "neutral" | "positive" | "warning" | "risk" | "info" | "accent";

export interface ToneClasses {
  text: string;
  soft: string; // chip: border + bg + text
  border: string;
  dot: string;
  ring: string; // border color for status rings
}

const TONE_MAP: Record<Tone, ToneClasses> = {
  neutral: {
    text: "text-muted-foreground",
    soft: "border-border bg-surface-muted text-muted-foreground",
    border: "border-border",
    dot: "bg-subtle-foreground",
    ring: "border-border-strong",
  },
  positive: {
    text: "text-positive",
    soft: "border-positive/20 bg-positive-bg text-positive",
    border: "border-positive/30",
    dot: "bg-positive",
    ring: "border-positive",
  },
  warning: {
    text: "text-warning",
    soft: "border-warning/20 bg-warning-bg text-warning",
    border: "border-warning/30",
    dot: "bg-warning",
    ring: "border-warning",
  },
  risk: {
    text: "text-risk",
    soft: "border-risk/20 bg-risk-bg text-risk",
    border: "border-risk/30",
    dot: "bg-risk",
    ring: "border-risk",
  },
  info: {
    text: "text-info",
    soft: "border-info/20 bg-info-bg text-info",
    border: "border-info/30",
    dot: "bg-info",
    ring: "border-info",
  },
  accent: {
    text: "text-foreground",
    soft: "border-border-strong bg-surface-muted text-foreground",
    border: "border-border-strong",
    dot: "bg-accent",
    ring: "border-accent",
  },
};

export function toneClasses(tone: Tone): ToneClasses {
  return TONE_MAP[tone];
}

// --------------------------------------------------------------------------- //
// Graph node / phase vocabulary (mirrors agent/src/agent.py)
// --------------------------------------------------------------------------- //
export const COUNCIL_ANALYST_IDS = ["treasury", "fpna", "risk", "procurement"] as const;

export const NODE_LABEL: Record<string, string> = {
  intake: "Convening the committee",
  planner: "Planning evidence for each role",
  committee_parallel: "All four analysts working in parallel",
  treasury: "Treasury is stress-testing liquidity timing",
  fpna: "FP&A is testing forecastability",
  risk: "Risk & Audit is challenging controls",
  procurement: "Procurement is building negotiation levers",
  challenge: "Evidence challenge panel",
  debate: "Committee cross-examination",
  influence: "Council is assigning influence weights",
  synthesis: "The CFO is deliberating",
  reliability: "Reliability Auditor is building evaluator scorecards",
  reliability_auditor: "Reliability Auditor is building evaluator scorecards",
  persist: "Recording the decision",
};

export const NODE_TO_AGENT: Record<string, string> = {
  intake: "cfo",
  planner: "planner",
  committee_parallel: "committee_parallel",
  treasury: "treasury",
  fpna: "fpna",
  risk: "risk",
  procurement: "procurement",
  challenge: "challenge",
  debate: "debate",
  influence: "cfo",
  synthesis: "cfo",
  reliability: "reliability",
  reliability_auditor: "reliability",
  persist: "cfo",
};

export const PHASE_LABEL: Record<string, string> = {
  intake: "Intake",
  analysis: "Functional analysis",
  debate: "Cross-examination",
  influence: "Council influence",
  synthesis: "CFO synthesis",
  reliability: "Reliability eval",
  persist: "Recording decision",
  done: "Decision recorded",
};

export const SPONSOR_DEFS = [
  { id: "weave", label: "W&B Weave", detail: "Trace readiness pending" },
  { id: "openai", label: "OpenAI", detail: "Model readiness pending" },
  { id: "redis", label: "Redis", detail: "Stack readiness pending" },
  { id: "copilotkit", label: "CopilotKit", detail: "AG-UI bridge pending" },
  { id: "cursor", label: "Cursor", detail: "Workflow rules pending" },
] as const;

// --------------------------------------------------------------------------- //
// Health + sponsor view types (the /api/health contract, consumed defensively)
// --------------------------------------------------------------------------- //
export type HealthStatus = "loading" | "ready" | "blocked" | "unavailable";
export type SponsorStatus = "ready" | "blocked" | "checking";

export interface HealthCheck {
  id?: string;
  label: string;
  ready: boolean;
  detail?: string;
  error?: string | null;
  url?: string | null;
  checks?: HealthCheck[];
  capabilities?: string[];
  realtime?: { model?: string; reasoning_effort?: string; voice?: string; endpoint?: string };
  sandbox?: { configured?: boolean; id?: string | null; url?: string | null; detail?: string };
  modules?: Record<string, string>;
  indices?: Record<string, Record<string, unknown>>;
  streams?: Record<string, Record<string, unknown>>;
  model?: string;
  reasoning_effort?: string;
  verbosity?: string;
}

export interface HealthPayload {
  ready: boolean;
  mode?: string;
  blockers?: string[];
  env?: HealthCheck[];
  sponsors?: HealthCheck[];
  weave?: {
    configured?: boolean;
    initialized?: boolean;
    project?: string;
    entity?: string;
    error?: string | null;
    url?: string | null;
  };
}

export interface HealthView {
  status: HealthStatus;
  data?: HealthPayload;
  error?: string;
  refreshing?: boolean;
}

export interface SponsorView {
  id: string;
  label: string;
  detail: string;
  error?: string | null;
  url?: string | null;
  status: SponsorStatus;
  checks?: HealthCheck[];
  capabilities?: string[];
  realtime?: HealthCheck["realtime"];
  modules?: Record<string, string>;
  model?: string;
  reasoning_effort?: string;
  verbosity?: string;
}

export type TimelineStatus = "complete" | "active" | "pending" | "blocked";
export type RealtimeStatus = "idle" | "connecting" | "connected" | "blocked";

export interface RealtimeView {
  status: RealtimeStatus;
  detail: string;
  model?: string;
  voice?: string;
  micMuted?: boolean;
  listening?: boolean;
  speaking?: boolean;
  processing?: boolean;
}

export interface TimelineStep {
  id: string;
  kind: string;
  label: string;
  status: TimelineStatus;
}

export interface PhaseStep {
  id: string;
  label: string;
  status: TimelineStatus;
  target: string; // element id to scroll to on "jump"
}

// --------------------------------------------------------------------------- //
// Health helpers
// --------------------------------------------------------------------------- //
export function getHealthLabel(health: HealthView): string {
  if (health.status === "ready") return "Ready";
  if (health.status === "loading") return "Checking";
  if (health.status === "blocked") return "Blocked";
  return "Unavailable";
}

export function getSponsorRows(health: HealthView): SponsorView[] {
  return SPONSOR_DEFS.map((fallback) => {
    const live = health.data?.sponsors?.find((item) => item.id === fallback.id);
    const envOpenAI =
      fallback.id === "openai" ? health.data?.env?.find((item) => item.id === "openai_api_key") : undefined;
    const source = live ?? envOpenAI;
    const status: SponsorStatus =
      health.status === "loading" ? "checking" : source ? (source.ready ? "ready" : "blocked") : "blocked";

    return {
      id: fallback.id,
      label: source?.label ?? fallback.label,
      detail: source?.detail ?? fallback.detail,
      error: source?.error,
      url: source?.url,
      status,
      checks: source?.checks,
      capabilities: source?.capabilities,
      realtime: source?.realtime,
      modules: source?.modules,
      model: source?.model,
      reasoning_effort: source?.reasoning_effort,
      verbosity: source?.verbosity,
    } satisfies SponsorView;
  });
}

export function sponsorStatusTone(status: SponsorStatus): Tone {
  if (status === "ready") return "positive";
  if (status === "blocked") return "risk";
  return "warning";
}

// --------------------------------------------------------------------------- //
// Phase / timeline
// --------------------------------------------------------------------------- //
export function getCurrentPhaseLabel(args: {
  health: HealthView;
  healthReady: boolean;
  nodeName?: string;
  phase?: string;
  recommendation?: DebateState["recommendation"];
  running: boolean;
}): string {
  const { health, healthReady, nodeName, phase, recommendation, running } = args;
  if (!healthReady) {
    return health.status === "loading" ? "Strict preflight checking" : "Strict preflight blocked";
  }
  if (running) return NODE_LABEL[nodeName ?? ""] ?? "Council deliberating";
  if (recommendation?.decision) return "Recommendation issued";
  if (phase) return PHASE_LABEL[phase] ?? phase;
  return "Awaiting decision";
}

function timelineStatus(args: {
  complete: boolean;
  healthReady: boolean;
  id: string;
  nodeName?: string;
  running: boolean;
}): TimelineStatus {
  const { complete, healthReady, id, nodeName, running } = args;
  if (complete) return "complete";
  if (!healthReady) return "pending";
  if (running && nodeName === id) return "active";
  if (
    running &&
    nodeName === "committee_parallel" &&
    (COUNCIL_ANALYST_IDS as readonly string[]).includes(id)
  ) {
    return "active";
  }
  return "pending";
}

export function buildTimeline(args: {
  health: HealthView;
  healthReady: boolean;
  nodeName?: string;
  phase?: string;
  recommendation?: DebateState["recommendation"];
  running: boolean;
  transcript: TranscriptTurn[];
}): TimelineStep[] {
  const { health, healthReady, nodeName, phase, recommendation, running, transcript } = args;
  const preflightStatus: TimelineStatus = healthReady
    ? "complete"
    : health.status === "loading"
      ? "active"
      : "blocked";
  const hasFraming = transcript.some((turn) => turn.type === "framing");
  const hasDebate = transcript.some((turn) => turn.type === "rebuttal");
  const hasInfluence = transcript.some((turn) => turn.type === "influence");
  const hasReliability = transcript.some((turn) => turn.type === "reliability");
  const hasAgent = (agent: string) =>
    transcript.some((turn) => turn.agent === agent && turn.type === "position");

  return [
    { id: "preflight", kind: "gate", label: "Strict preflight", status: preflightStatus },
    {
      id: "intake",
      kind: "node",
      label: "Intake",
      status: timelineStatus({ complete: hasFraming, healthReady, id: "intake", nodeName, running }),
    },
    {
      id: "treasury",
      kind: "agent",
      label: "Treasury",
      status: timelineStatus({ complete: hasAgent("treasury"), healthReady, id: "treasury", nodeName, running }),
    },
    {
      id: "fpna",
      kind: "agent",
      label: "FP&A",
      status: timelineStatus({ complete: hasAgent("fpna"), healthReady, id: "fpna", nodeName, running }),
    },
    {
      id: "risk",
      kind: "agent",
      label: "Risk & Audit",
      status: timelineStatus({ complete: hasAgent("risk"), healthReady, id: "risk", nodeName, running }),
    },
    {
      id: "procurement",
      kind: "agent",
      label: "Procurement",
      status: timelineStatus({ complete: hasAgent("procurement"), healthReady, id: "procurement", nodeName, running }),
    },
    {
      id: "debate",
      kind: "node",
      label: "Cross-exam",
      status: timelineStatus({ complete: hasDebate, healthReady, id: "debate", nodeName, running }),
    },
    {
      id: "influence",
      kind: "node",
      label: "Influence",
      status: timelineStatus({ complete: hasInfluence, healthReady, id: "influence", nodeName, running }),
    },
    {
      id: "synthesis",
      kind: "node",
      label: phase === "done" ? "Persisted" : "CFO ruling",
      status: timelineStatus({
        complete: Boolean(recommendation?.decision) || phase === "done",
        healthReady,
        id: nodeName === "persist" ? "persist" : "synthesis",
        nodeName,
        running,
      }),
    },
    {
      id: "reliability",
      kind: "node",
      label: "Reliability eval",
      status: timelineStatus({
        complete: hasReliability || phase === "done",
        healthReady,
        id: "reliability",
        nodeName,
        running,
      }),
    },
  ];
}

// The compact 5-stage reference rail with scroll targets for "jump to active phase".
export function buildPhaseSteps(steps: TimelineStep[]): PhaseStep[] {
  const byId = Object.fromEntries(steps.map((step) => [step.id, step.status]));
  const analystStatuses = ["treasury", "fpna", "risk", "procurement"].map((id) => byId[id]);
  const analysisStatus: TimelineStatus = analystStatuses.every((status) => status === "complete")
    ? "complete"
    : analystStatuses.some((status) => status === "active")
      ? "active"
      : analystStatuses.some((status) => status === "blocked")
        ? "blocked"
        : "pending";

  return [
    { id: "briefing", label: "Briefing", status: byId.intake ?? byId.preflight ?? "pending", target: "council-web" },
    { id: "analysis", label: "Analysis", status: analysisStatus, target: "council-transcript" },
    { id: "debate", label: "Debate", status: byId.debate ?? "pending", target: "council-transcript" },
    { id: "influence", label: "Influence", status: byId.influence ?? "pending", target: "council-web" },
    { id: "ruling", label: "Ruling", status: byId.synthesis ?? "pending", target: "council-memo" },
    { id: "evals", label: "Evals", status: byId.reliability ?? "pending", target: "evals" },
  ];
}

export function timelineLabel(status: TimelineStatus): string {
  if (status === "complete") return "Complete";
  if (status === "active") return "Active";
  if (status === "blocked") return "Blocked";
  return "Pending";
}

export function timelineTone(status: TimelineStatus): Tone {
  if (status === "complete") return "positive";
  if (status === "active") return "info";
  if (status === "blocked") return "risk";
  return "neutral";
}

// --------------------------------------------------------------------------- //
// Transcript / agent helpers
// --------------------------------------------------------------------------- //
export function latestSpeakerId(transcript: TranscriptTurn[]): string | undefined {
  for (let index = transcript.length - 1; index >= 0; index -= 1) {
    const turn = transcript[index];
    if (turn.agent && ROSTER_BY_ID[turn.agent]) return turn.agent;
    if (turn.type === "framing" || turn.type === "decision") return "cfo";
  }
  return undefined;
}

export function findLatestTurnForMember(memberId: string, transcript: TranscriptTurn[]): TranscriptTurn | undefined {
  for (let index = transcript.length - 1; index >= 0; index -= 1) {
    const turn = transcript[index];
    if (memberId === "cfo" && (turn.agent === "cfo" || turn.type === "framing" || turn.type === "decision")) {
      return turn;
    }
    if (turn.agent === memberId) return turn;
  }
  return undefined;
}

export function isParallelCouncilNode(nodeName?: string): boolean {
  return nodeName === "committee_parallel";
}

export function isAgentActive(args: {
  agentStatus?: AgentStatus;
  healthReady: boolean;
  memberId: string;
  nodeName?: string;
  running: boolean;
}): boolean {
  const { agentStatus, healthReady, memberId, nodeName, running } = args;
  if (!healthReady) return false;
  const statusValue = String(agentStatus?.status ?? "").toLowerCase();
  const parallelSeat =
    running && isParallelCouncilNode(nodeName) && (COUNCIL_ANALYST_IDS as readonly string[]).includes(memberId);
  return (
    parallelSeat ||
    (running && NODE_TO_AGENT[nodeName ?? ""] === memberId) ||
    ["thinking", "speaking", "running"].includes(statusValue)
  );
}

export function getAgentStatus(args: {
  active: boolean;
  agentStatus?: AgentStatus;
  healthReady: boolean;
  latestSpeaker?: string;
  latestTurn?: TranscriptTurn;
  member: RosterMember;
  nodeName?: string;
  started: boolean;
}): string {
  const { active, agentStatus, healthReady, latestSpeaker, latestTurn, member, nodeName, started } = args;
  if (!healthReady) return "Preflight blocked";
  const backendStatus = String(agentStatus?.status ?? "").toLowerCase();
  if (backendStatus === "thinking" || backendStatus === "running") return "Thinking";
  if (backendStatus === "speaking") return "Speaking";
  if (backendStatus === "done" || backendStatus === "complete" || backendStatus === "persisting") return "On record";
  if (backendStatus === "error") return "Error";
  if (backendStatus === "warning" || backendStatus === "blocked") return "Blocked";
  if (active) {
    const currentOutput =
      latestTurn &&
      (latestTurn.agent === nodeName ||
        (nodeName === "intake" && latestTurn.type === "framing") ||
        (nodeName === "synthesis" && latestTurn.type === "decision"));
    return currentOutput ? "Speaking" : "Thinking";
  }
  if (latestSpeaker === member.id) return "Last spoke";
  if (latestTurn) return "On record";
  if (started) return "Queued";
  return "Standing by";
}

export function agentStatusTone(status: string): Tone {
  if (status === "Speaking" || status === "Last spoke") return "positive";
  if (status === "Thinking") return "info";
  if (status === "Preflight blocked" || status === "Error") return "risk";
  if (status === "Blocked") return "warning";
  if (status === "On record") return "info";
  return "neutral";
}

export function getAgentStanceLabel(
  member: RosterMember,
  turn?: TranscriptTurn,
  recommendation?: DebateState["recommendation"],
): string {
  if (member.id === "cfo" && recommendation?.decision) return recommendation.decision;
  if (member.id === "reliability") return "Evaluator";
  if (turn?.stance) {
    const stance = String(turn.stance).toLowerCase();
    if (stance === "conditional") return "Conditional";
    if (stance === "support") return "Supports";
    if (stance === "oppose") return "Opposes";
    return String(turn.stance).replace(/\b\w/g, (letter) => letter.toUpperCase());
  }
  return member.id === "cfo" ? "Chair" : "No stance yet";
}

export function agentStanceTone(
  member: RosterMember,
  turn?: TranscriptTurn,
  recommendation?: DebateState["recommendation"],
): Tone {
  if (member.id === "cfo" && recommendation?.decision) return decisionTone(recommendation.decision);
  if (member.id === "reliability") return "info";
  if (turn?.stance) return stanceTone(String(turn.stance));
  return "neutral";
}

export function stanceTone(stance?: string): Tone {
  const value = (stance ?? "").toLowerCase();
  if (value.includes("support") || value.includes("approve")) return "positive";
  if (value.includes("oppose") || value.includes("reject")) return "risk";
  if (value.includes("conditional") || value.includes("caution") || value.includes("defer")) return "warning";
  return "info";
}

export function decisionTone(decision?: string): Tone {
  switch ((decision ?? "").toUpperCase()) {
    case "APPROVE":
      return "positive";
    case "REJECT":
      return "risk";
    case "CONDITIONAL":
    case "DEFER":
      return "warning";
    default:
      return "info";
  }
}

/** Editorial flourish + kicker copy for the CFO ruling surface. */
export function cfoRulingFlourish(decision?: string): { kicker: string; flourish: string } {
  switch ((decision ?? "").toUpperCase()) {
    case "APPROVE":
      return { kicker: "Office of the CFO", flourish: "The chair approves — proceed with eyes open." };
    case "REJECT":
      return { kicker: "Office of the CFO", flourish: "The chair declines — not on these terms." };
    case "CONDITIONAL":
      return { kicker: "Office of the CFO", flourish: "Approved only if the guardrails hold." };
    case "DEFER":
      return { kicker: "Office of the CFO", flourish: "The chair defers — more evidence required." };
    default:
      return { kicker: "Office of the CFO", flourish: "The chair has ruled." };
  }
}

/** Split rationale into a lead sentence (emphasized) and the remainder. */
export function splitCfoRationale(text: string): { lead: string; rest: string } {
  const trimmed = text.trim();
  if (!trimmed) return { lead: "", rest: "" };
  const match = trimmed.match(/^(.+?[.!?])(?:\s+([\s\S]+))?$/);
  if (!match) return { lead: trimmed, rest: "" };
  return { lead: match[1].trim(), rest: (match[2] ?? "").trim() };
}

export function getAgentHeadline(
  member: RosterMember,
  turn?: TranscriptTurn,
  recommendation?: DebateState["recommendation"],
  agentStatus?: AgentStatus,
): string {
  if (member.id === "cfo" && recommendation?.decision) {
    return `${recommendation.decision} at ${recommendation.confidence ?? "--"}% confidence`;
  }
  return agentStatus?.headline || turn?.headline || member.mandate || "Awaiting live council output";
}

export function getAgentSnippet(args: {
  agentStatus?: AgentStatus;
  member: RosterMember;
  turn?: TranscriptTurn;
  recommendation?: DebateState["recommendation"];
  healthReady: boolean;
  started: boolean;
}): string {
  const { agentStatus, member, turn, recommendation, healthReady, started } = args;
  if (member.id === "cfo" && recommendation?.ruling) return recommendation.ruling;
  if (member.id === "cfo" && recommendation?.rationale) return recommendation.rationale;
  if (agentStatus?.detail && agentStatus.detail !== "Awaiting council turn") return agentStatus.detail;
  if (turn?.argument) return turn.argument;
  if (turn?.point) return turn.point;
  if (!healthReady) return "Strict live preflight must pass before this seat can produce a live utterance.";
  if (started && (agentStatus?.status === "thinking" || agentStatus?.status === "running")) {
    return agentStatus.detail ?? "Working in the live council room…";
  }
  if (started) return "Standing by in the council room.";
  return "Ready to join once a decision command is submitted.";
}

export function activeCouncilWorkers(
  agentStatuses: AgentStatus[],
  running: boolean,
  nodeName?: string,
): AgentStatus[] {
  if (!running) return [];
  const working = agentStatuses.filter((status) =>
    ["thinking", "speaking", "running"].includes(String(status.status ?? "").toLowerCase()),
  );
  if (working.length > 0) return working;
  if (isParallelCouncilNode(nodeName)) {
    return agentStatuses.filter((status) =>
      (COUNCIL_ANALYST_IDS as readonly string[]).includes(status.id),
    );
  }
  const mapped = NODE_TO_AGENT[nodeName ?? ""];
  if (!mapped || mapped === "committee_parallel") return [];
  const match = agentStatuses.find((status) => status.id === mapped);
  return match ? [match] : [];
}

// --------------------------------------------------------------------------- //
// Reliability helpers
// --------------------------------------------------------------------------- //
export function resolveReliabilityValue(agentStatus?: AgentStatus, score?: ReliabilityScore): number | undefined {
  const raw = score?.reliability ?? agentStatus?.reliability_score;
  return typeof raw === "number" && Number.isFinite(raw) ? Math.max(0, Math.min(100, Math.round(raw))) : undefined;
}

export function averageReliability(scores: ReliabilityScore[]): number | undefined {
  if (!scores.length) return undefined;
  return Math.round(scores.reduce((sum, score) => sum + score.reliability, 0) / scores.length);
}

export function reliabilityColor(value: number): string {
  if (value >= 85) return "var(--positive)";
  if (value >= 70) return "var(--info)";
  if (value >= 55) return "var(--warning)";
  return "var(--risk)";
}

export function reliabilityTone(value?: number): Tone {
  if (typeof value !== "number") return "neutral";
  if (value >= 85) return "positive";
  if (value >= 70) return "info";
  if (value >= 55) return "warning";
  return "risk";
}

export function reliabilityDimensionsFromScore(score: ReliabilityScore): Record<string, number | undefined> {
  return {
    outcome_accuracy: score.outcome_accuracy,
    evidence_grounding: score.evidence_grounding,
    forecast_calibration: score.forecast_calibration,
    policy_compliance: score.policy_compliance,
    debate_value: score.debate_value,
    confidence_calibration: score.confidence_calibration,
    trace_quality: score.trace_quality,
  };
}

export function fmtTelemetry(value?: number, unit?: string): string {
  if (typeof value !== "number") return "Waiting";
  const formatted = value >= 1000 ? value.toLocaleString() : String(value);
  return unit ? `${formatted}${unit}` : formatted;
}

// --------------------------------------------------------------------------- //
// Council influence helpers
// --------------------------------------------------------------------------- //
export function influenceByAgent(report?: CouncilInfluenceReport): Record<string, AgentInfluence> {
  return Object.fromEntries((report?.weights ?? []).map((weight) => [weight.agent_id, weight]));
}

export function resolveInfluenceValue(
  agentStatus?: AgentStatus,
  influence?: AgentInfluence,
): number | undefined {
  const raw = influence?.influence_weight ?? agentStatus?.influence_weight;
  return typeof raw === "number" && Number.isFinite(raw) ? Math.max(0, Math.min(100, Math.round(raw))) : undefined;
}

export function topInfluenceAgent(report?: CouncilInfluenceReport): AgentInfluence | undefined {
  const weights = report?.weights ?? [];
  if (!weights.length) return undefined;
  return weights.reduce((top, item) => (item.influence_weight > top.influence_weight ? item : top));
}
