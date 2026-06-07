import { resolveMember } from "@/lib/agents";
import { COUNCIL_ANALYST_IDS, isParallelCouncilNode } from "@/lib/council";
import type { AgentStatus, TranscriptTurn } from "@/lib/types";

export type WebNodeId = "cfo" | "treasury" | "fpna" | "risk" | "procurement" | "reliability";

export type WebNodeLayout = {
  id: WebNodeId;
  x: number;
  y: number;
};

export const WEB_SHORT_LABEL: Record<WebNodeId, string> = {
  cfo: "CFO",
  treasury: "Treasury",
  fpna: "FP&A",
  risk: "Risk",
  procurement: "Procurement",
  reliability: "Reliability",
};

/** Canvas coordinates (viewBox 1000 × 640) — hub + ring, spaced for compact orbs. */
export const WEB_NODE_LAYOUT: WebNodeLayout[] = [
  { id: "cfo", x: 500, y: 290 },
  { id: "treasury", x: 220, y: 120 },
  { id: "fpna", x: 780, y: 120 },
  { id: "risk", x: 220, y: 460 },
  { id: "procurement", x: 780, y: 460 },
  { id: "reliability", x: 500, y: 560 },
];

export const WEB_NODE_BY_ID = Object.fromEntries(WEB_NODE_LAYOUT.map((node) => [node.id, node])) as Record<
  WebNodeId,
  WebNodeLayout
>;

const PEER_RING: [WebNodeId, WebNodeId][] = [
  ["treasury", "fpna"],
  ["fpna", "procurement"],
  ["procurement", "risk"],
  ["risk", "treasury"],
];

const HUB_TARGETS: WebNodeId[] = ["treasury", "fpna", "risk", "procurement", "reliability"];

export type WebEdgeKind = "hub" | "peer" | "message";

export type CouncilWebEdge = {
  id: string;
  from: WebNodeId;
  to: WebNodeId;
  kind: WebEdgeKind;
  active: boolean;
  weight?: number;
};

function isWorking(status?: AgentStatus): boolean {
  const value = String(status?.status ?? "").toLowerCase();
  return ["thinking", "speaking", "running"].includes(value);
}

function resolveWebAgent(turn: TranscriptTurn): WebNodeId | undefined {
  if (turn.agent && turn.agent in WEB_NODE_BY_ID) return turn.agent as WebNodeId;
  const member = resolveMember(turn.agent ?? turn.role ?? turn.from_role);
  if (member?.id && member.id in WEB_NODE_BY_ID) return member.id as WebNodeId;
  return undefined;
}

function resolveRebuttalEnds(turn: TranscriptTurn): { from?: WebNodeId; to?: WebNodeId } {
  const fromMember = resolveMember(turn.from_role);
  const toMember = resolveMember(turn.to_role);
  const from = fromMember?.id && fromMember.id in WEB_NODE_BY_ID ? (fromMember.id as WebNodeId) : undefined;
  const to = toMember?.id && toMember.id in WEB_NODE_BY_ID ? (toMember.id as WebNodeId) : undefined;
  return { from, to };
}

export function webBezierPath(from: WebNodeLayout, to: WebNodeLayout): string {
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2;
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const bend = from.id === "cfo" || to.id === "cfo" ? 0.06 : 0.14;
  const cx = mx - dy * bend;
  const cy = my + dx * bend;
  return `M ${from.x} ${from.y} Q ${cx} ${cy} ${to.x} ${to.y}`;
}

export function webNodeStatusLine(args: {
  agentStatus?: AgentStatus;
  active: boolean;
  running: boolean;
  started: boolean;
}): string {
  const { agentStatus, active, running, started } = args;
  const backend = String(agentStatus?.status ?? "").toLowerCase();

  if (active && running) {
    if (backend === "speaking") return "Speaking";
    if (backend === "thinking" || backend === "running") return "In session";
    return "Live";
  }
  if (backend === "done" || backend === "complete") return "Ready";
  if (backend === "error") return "Retry";
  if (started && running) return "Queued";
  if (started) return "Standing by";
  return "Idle";
}

export function buildCouncilWebEdges(args: {
  agentStatuses: AgentStatus[];
  influenceByAgent?: Record<string, { influence_weight?: number }>;
  running: boolean;
  nodeName?: string;
  transcript: TranscriptTurn[];
}): CouncilWebEdge[] {
  const { agentStatuses, influenceByAgent, running, nodeName, transcript } = args;
  const influenceWeight = (agentId: string) => {
    const raw = influenceByAgent?.[agentId]?.influence_weight;
    return typeof raw === "number" && Number.isFinite(raw) ? Math.max(0, Math.min(100, raw)) : undefined;
  };
  const byId = Object.fromEntries(agentStatuses.map((status) => [status.id, status])) as Record<string, AgentStatus>;
  const edgeMap = new Map<string, CouncilWebEdge>();

  const upsert = (edge: CouncilWebEdge) => {
    const key = `${edge.from}-${edge.to}-${edge.kind}`;
    const existing = edgeMap.get(key);
    if (!existing) {
      edgeMap.set(key, edge);
      return;
    }
    edgeMap.set(key, {
      ...existing,
      active: existing.active || edge.active,
      weight: Math.max(existing.weight ?? 0, edge.weight ?? 0),
    });
  };

  for (const target of HUB_TARGETS) {
    upsert({ id: `hub-cfo-${target}`, from: "cfo", to: target, kind: "hub", active: false });
    upsert({ id: `hub-${target}-cfo`, from: target, to: "cfo", kind: "hub", active: false });
  }

  for (const [a, b] of PEER_RING) {
    upsert({ id: `peer-${a}-${b}`, from: a, to: b, kind: "peer", active: false });
  }

  const analystsWorking = COUNCIL_ANALYST_IDS.filter((id) => isWorking(byId[id]));
  const parallelSession = running && (isParallelCouncilNode(nodeName) || analystsWorking.length >= 2);

  if (parallelSession) {
    for (const [a, b] of PEER_RING) {
      upsert({ id: `peer-${a}-${b}`, from: a, to: b, kind: "peer", active: true });
    }
  }

  for (const target of HUB_TARGETS) {
    const weight = influenceWeight(target);
    if (isWorking(byId[target])) {
      upsert({
        id: `hub-${target}-cfo`,
        from: target as WebNodeId,
        to: "cfo",
        kind: "hub",
        active: true,
        weight,
      });
      upsert({
        id: `hub-cfo-${target}`,
        from: "cfo",
        to: target,
        kind: "hub",
        active: true,
        weight,
      });
    } else if (weight && weight >= 20 && (COUNCIL_ANALYST_IDS as readonly string[]).includes(target)) {
      upsert({
        id: `hub-${target}-cfo-weight`,
        from: target as WebNodeId,
        to: "cfo",
        kind: "hub",
        active: true,
        weight,
      });
    }
  }

  const recent = transcript.slice(-6);
  for (const turn of recent) {
    if (turn.type === "thinking") {
      const agent = resolveWebAgent(turn);
      if (!agent || agent === "cfo") continue;
      upsert({ id: `msg-${agent}-cfo-think`, from: agent, to: "cfo", kind: "message", active: running });
      continue;
    }
    if (turn.type === "position") {
      const agent = resolveWebAgent(turn);
      if (!agent || agent === "cfo") continue;
      upsert({
        id: `msg-${agent}-cfo-pos`,
        from: agent,
        to: "cfo",
        kind: "message",
        active: true,
        weight: influenceWeight(agent),
      });
      continue;
    }
    if (turn.type === "rebuttal") {
      const { from, to } = resolveRebuttalEnds(turn);
      if (!from || !to) continue;
      upsert({ id: `msg-${from}-${to}-reb`, from, to, kind: "message", active: true });
    }
  }

  return Array.from(edgeMap.values());
}
