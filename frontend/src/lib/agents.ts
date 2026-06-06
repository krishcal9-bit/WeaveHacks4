import type { RosterMember, Stance } from "./types";

// Mirrors the agent ROSTER (agent/src/agent.py). Professional roles, not characters.
export const ROSTER: RosterMember[] = [
  { id: "cfo", label: "Office of the CFO", role: "Chief Financial Officer · Chair", monogram: "CF", mandate: "Balances growth, risk, and runway to make the final call." },
  { id: "treasury", label: "Treasury", role: "Treasury", monogram: "TR", mandate: "Liquidity, cash position, runway, and financing risk." },
  { id: "fpna", label: "FP&A", role: "Financial Planning & Analysis", monogram: "FP", mandate: "Growth, ROI, forecast, payback, and unit economics." },
  { id: "risk", label: "Risk & Audit", role: "Risk & Audit", monogram: "RA", mandate: "Downside scenarios, compliance, controls, and policy." },
  { id: "procurement", label: "Procurement", role: "Procurement", monogram: "PR", mandate: "Vendor terms, cost efficiency, and negotiation leverage." },
];

export const ROSTER_BY_ID: Record<string, RosterMember> = Object.fromEntries(
  ROSTER.map((r) => [r.id, r]),
);

// Resolve a roster member by id, or by the role title used in debate rebuttals.
export function resolveMember(idOrRole?: string): RosterMember | undefined {
  if (!idOrRole) return undefined;
  if (ROSTER_BY_ID[idOrRole]) return ROSTER_BY_ID[idOrRole];
  const lower = idOrRole.toLowerCase();
  return ROSTER.find(
    (r) => r.role.toLowerCase().includes(lower) || r.label.toLowerCase().includes(lower) || lower.includes(r.label.toLowerCase()),
  );
}

// Stance → information color (semantic only).
export const STANCE_STYLE: Record<Stance, { label: string; cls: string }> = {
  support: { label: "Supports", cls: "text-positive bg-positive-bg border-positive/20" },
  oppose: { label: "Opposes", cls: "text-risk bg-risk-bg border-risk/20" },
  conditional: { label: "Conditional", cls: "text-warning bg-warning-bg border-warning/20" },
};

// Final decision → information color.
export function decisionStyle(decision?: string): string {
  switch ((decision || "").toUpperCase()) {
    case "APPROVE":
      return "text-positive bg-positive-bg border-positive/20";
    case "REJECT":
      return "text-risk bg-risk-bg border-risk/20";
    case "CONDITIONAL":
      return "text-warning bg-warning-bg border-warning/20";
    default:
      return "text-info bg-info-bg border-info/20";
  }
}
