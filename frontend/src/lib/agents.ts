import type { AgentStatus, AgentStatusKind, RosterMember } from "./types";

// Mirrors the agent ROSTER (agent/src/agent.py). Professional roles, not characters.
export const ROSTER: RosterMember[] = [
  { id: "cfo", label: "Office of the CFO", role: "Chief Financial Officer · Chair", monogram: "CF", mandate: "Balances growth, risk, and runway to make the final call." },
  { id: "treasury", label: "Treasury", role: "Liquidity Mechanics", monogram: "TR", mandate: "Cash runway, liquidity timing, payment terms, working capital, renewal cash schedules, and late-cash financing risk." },
  { id: "fpna", label: "FP&A", role: "Forecast & Unit Economics", monogram: "FP", mandate: "Forecast quality, ARR movement, pipeline probability, ROI, CAC/payback, margin, sensitivity ranges, scenario math, and plan-vs-actual deltas." },
  { id: "risk", label: "Risk & Audit", role: "Controls Adversary", monogram: "RA", mandate: "Policy violations, audit trail gaps, approvals, data quality, fraud/error risk, compliance blockers, hidden obligations, security evidence, and source provenance." },
  { id: "procurement", label: "Procurement", role: "Vendor & Commercial Negotiation", monogram: "PR", mandate: "Supplier leverage, contract terms, auto-renewal, renewal dates, price benchmarks, consolidation, switching cost, SLAs, termination clauses, volume discounts, and negotiation strategy." },
  { id: "reliability", label: "Reliability Auditor", role: "Evaluator & Replay Auditor", monogram: "RL", mandate: "Audits grounding, calibration, policy compliance, debate value, trace quality, and weaknesses; generates replay cases and prompt directives." },
];

export const ROSTER_BY_ID: Record<string, RosterMember> = Object.fromEntries(
  ROSTER.map((r) => [r.id, r]),
);

// Display order with the CFO chair first — used by the matrix and inspector.
export const COUNCIL_ORDER = ["cfo", "treasury", "fpna", "risk", "procurement", "reliability"] as const;

// Faint identity hue per seat — a wayfinding aid only. Information color (stance,
// decision, reliability) always overrides this where it carries meaning.
export const AGENT_TONE: Record<string, "accent" | "info" | "warning" | "risk" | "positive" | "neutral"> = {
  cfo: "accent",
  treasury: "info",
  fpna: "warning",
  risk: "risk",
  procurement: "positive",
  reliability: "neutral",
};

export interface CouncilMember extends RosterMember {
  status?: AgentStatus;
  statusLabel: string;
  statusClass: string;
  ready: boolean;
}

const ROLE_ALIASES: Record<string, string> = {
  "chief financial officer": "cfo",
  cfo: "cfo",
  chair: "cfo",
  treasury: "treasury",
  "financial planning analysis": "fpna",
  "financial planning and analysis": "fpna",
  "fp&a": "fpna",
  fpna: "fpna",
  "risk audit": "risk",
  "risk and audit": "risk",
  "risk & audit": "risk",
  procurement: "procurement",
  "reliability auditor": "reliability",
  reliability: "reliability",
  "reliability and learning": "reliability",
};

function normalizeLookup(value: string): string {
  return value
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

// Resolve a roster member by id, or by the role title used in debate rebuttals.
export function resolveMember(idOrRole?: string): RosterMember | undefined {
  if (!idOrRole) return undefined;
  const exact = ROSTER_BY_ID[idOrRole];
  if (exact) return exact;

  const lower = normalizeLookup(idOrRole);
  const alias = ROLE_ALIASES[lower];
  if (alias) return ROSTER_BY_ID[alias];

  return ROSTER.find(
    (r) => {
      const role = normalizeLookup(r.role);
      const label = normalizeLookup(r.label);
      const mandate = normalizeLookup(r.mandate ?? "");
      return role.includes(lower) || lower.includes(role) || label.includes(lower) || lower.includes(label) || mandate.includes(lower);
    },
  );
}

export function normalizeRosterMember(member: Partial<RosterMember> & { id: string }): RosterMember {
  const fallback = resolveMember(member.id);
  return {
    id: member.id,
    label: member.label ?? fallback?.label ?? member.id,
    role: member.role ?? fallback?.role ?? member.label ?? member.id,
    monogram: member.monogram ?? fallback?.monogram ?? member.id.slice(0, 2).toUpperCase(),
    mandate: member.mandate ?? fallback?.mandate,
  };
}

export function memberFromStatus(status: AgentStatus): RosterMember {
  return normalizeRosterMember(status);
}

export function rosterWithStatuses(statuses: AgentStatus[] = [], roster: RosterMember[] = ROSTER): CouncilMember[] {
  const statusesById = Object.fromEntries(statuses.map((status) => [status.id, status]));
  const statusOnlyMembers = statuses
    .filter((status) => !roster.some((member) => member.id === status.id))
    .map(memberFromStatus);

  return [...roster, ...statusOnlyMembers].map((member) => {
    const status = statusesById[member.id];
    const style = agentStatusStyle(status);
    return {
      ...member,
      status,
      statusLabel: style.label,
      statusClass: style.cls,
      ready: status?.ready ?? !["blocked", "error"].includes(normalizeStatus(status?.status)),
    };
  });
}

export function activeCouncilMember(statuses: AgentStatus[] = []): AgentStatus | undefined {
  return statuses.find((status) => ["running", "thinking", "speaking"].includes(normalizeStatus(status.status)));
}

// Stance → information color (semantic only).
export const STANCE_STYLE: Record<string, { label: string; cls: string }> = {
  support: { label: "Supports", cls: "text-positive bg-positive-bg border-positive/20" },
  oppose: { label: "Opposes", cls: "text-risk bg-risk-bg border-risk/20" },
  conditional: { label: "Conditional", cls: "text-warning bg-warning-bg border-warning/20" },
};

export const AGENT_STATUS_STYLE: Record<AgentStatusKind, { label: string; cls: string }> = {
  idle: { label: "Idle", cls: "text-muted-foreground bg-surface-muted border-border" },
  queued: { label: "Queued", cls: "text-info bg-info-bg border-info/20" },
  running: { label: "Running", cls: "text-info bg-info-bg border-info/20" },
  thinking: { label: "Thinking", cls: "text-info bg-info-bg border-info/20" },
  speaking: { label: "Speaking", cls: "text-info bg-info-bg border-info/20" },
  complete: { label: "Complete", cls: "text-positive bg-positive-bg border-positive/20" },
  done: { label: "Done", cls: "text-positive bg-positive-bg border-positive/20" },
  blocked: { label: "Blocked", cls: "text-warning bg-warning-bg border-warning/20" },
  error: { label: "Error", cls: "text-risk bg-risk-bg border-risk/20" },
};

function normalizeStatus(status?: AgentStatus["status"]): AgentStatusKind {
  const normalized = String(status ?? "idle").toLowerCase().replace(/[^a-z]+/g, "_");
  if (normalized in AGENT_STATUS_STYLE) return normalized as AgentStatusKind;
  if (["started", "active", "in_progress"].includes(normalized)) return "running";
  if (["success", "succeeded", "finished"].includes(normalized)) return "complete";
  if (["failed", "failure"].includes(normalized)) return "error";
  return "idle";
}

export function agentStatusStyle(status?: AgentStatus | AgentStatus["status"]): { label: string; cls: string } {
  const statusValue = typeof status === "object" ? status.status : status;
  const normalized = normalizeStatus(statusValue);
  return AGENT_STATUS_STYLE[normalized];
}

// Final decision → information color.
export function decisionStyle(decision?: string): string {
  switch ((decision || "").toUpperCase()) {
    case "APPROVE":
      return "text-positive bg-positive-bg border-positive/20";
    case "REJECT":
      return "text-risk bg-risk-bg border-risk/20";
    case "CONDITIONAL":
      return "text-warning bg-warning-bg border-warning/20";
    // Non-verdict answers (open-ended recommendation / multiple-choice selection)
    // render with the neutral info style — never green/red.
    case "RECOMMENDATION":
    case "SELECTION":
      return "text-info bg-info-bg border-info/20";
    default:
      return "text-info bg-info-bg border-info/20";
  }
}
