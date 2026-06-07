import { resolveMember } from "@/lib/agents";
import { COUNCIL_ANALYST_IDS, isParallelCouncilNode } from "@/lib/council";
import type { AgentStatus, TranscriptTurn } from "@/lib/types";

export type WebNodeId = "cfo" | "treasury" | "fpna" | "risk" | "procurement" | "reliability";

export type WebNodeLayout = {
  id: WebNodeId;
  x: number;
  y: number;
};

/** Canvas coordinates (viewBox 1000 × 620) — CFO hub, analysts on the ring. */
export const WEB_NODE_LAYOUT: WebNodeLayout[] = [
  { id: "cfo", x: 500, y: 300 },
  { id: "treasury", x: 200, y: 130 },
  { id: "fpna", x: 800, y: 130 },
  { id: "risk", x: 200, y: 470 },
  { id: "procurement", x: 800, y: 470 },
  { id: "reliability", x: 500, y: 540 },
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
  label?: string;
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
  const bend = from.id === "cfo" || to.id === "cfo" ? 0.08 : 0.18;
  const cx = mx - dy * bend;
  const cy = my + dx * bend;
  return `M ${from.x} ${from.y} Q ${cx} ${cy} ${to.x} ${to.y}`;
}

export function buildCouncilWebEdges(args: {
  agentStatuses: AgentStatus[];
  running: boolean;
  nodeName?: string;
  transcript: TranscriptTurn[];
}): CouncilWebEdge[] {
  const { agentStatuses, running, nodeName, transcript } = args;
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
      label: edge.label ?? existing.label,
    });
  };

  for (const target of HUB_TARGETS) {
    upsert({
      id: `hub-cfo-${target}`,
      from: "cfo",
      to: target,
      kind: "hub",
      active: false,
    });
  }

  for (const [a, b] of PEER_RING) {
    upsert({ id: `peer-${a}-${b}`, from: a, to: b, kind: "peer", active: false });
    upsert({ id: `peer-${b}-${a}`, from: b, to: a, kind: "peer", active: false });
  }

  const analystsWorking = COUNCIL_ANALYST_IDS.filter((id) => isWorking(byId[id]));
  const parallelSession = running && (isParallelCouncilNode(nodeName) || analystsWorking.length >= 2);

  if (parallelSession) {
    for (const [a, b] of PEER_RING) {
      upsert({ id: `peer-${a}-${b}`, from: a, to: b, kind: "peer", active: true, label: "In session" });
      upsert({ id: `peer-${b}-${a}`, from: b, to: a, kind: "peer", active: true, label: "In session" });
    }
  }

  for (const target of HUB_TARGETS) {
    if (isWorking(byId[target])) {
      upsert({
        id: `hub-cfo-${target}`,
        from: target,
        to: "cfo",
        kind: "hub",
        active: true,
        label: byId[target]?.detail ?? "Reporting in",
      });
      upsert({
        id: `hub-active-cfo-${target}`,
        from: "cfo",
        to: target,
        kind: "hub",
        active: true,
        label: "Chair listening",
      });
    }
  }

  if (running && isWorking(byId.cfo)) {
    for (const target of COUNCIL_ANALYST_IDS) {
      upsert({
        id: `hub-cfo-broadcast-${target}`,
        from: "cfo",
        to: target as WebNodeId,
        kind: "hub",
        active: true,
        label: "Chair directing",
      });
    }
  }

  const recent = transcript.slice(-12);
  for (const turn of recent) {
    if (turn.type === "thinking") {
      const agent = resolveWebAgent(turn);
      if (!agent || agent === "cfo") continue;
      upsert({
        id: `think-${agent}-cfo`,
        from: agent,
        to: "cfo",
        kind: "message",
        active: true,
        label: turn.argument?.slice(0, 48) ?? "Thinking",
      });
      for (const peer of COUNCIL_ANALYST_IDS) {
        if (peer === agent) continue;
        if (!isWorking(byId[peer]) && !parallelSession) continue;
        upsert({
          id: `think-${agent}-${peer}`,
          from: agent,
          to: peer as WebNodeId,
          kind: "message",
          active: true,
          label: "Syncing",
        });
      }
      continue;
    }

    if (turn.type === "position") {
      const agent = resolveWebAgent(turn);
      if (!agent || agent === "cfo") continue;
      upsert({
        id: `pos-${agent}-cfo-${turn.headline ?? ""}`,
        from: agent,
        to: "cfo",
        kind: "message",
        active: true,
        label: turn.headline ?? "Position",
      });
      for (const peer of COUNCIL_ANALYST_IDS) {
        if (peer === agent) continue;
        upsert({
          id: `pos-${agent}-${peer}-${turn.headline ?? ""}`,
          from: agent,
          to: peer as WebNodeId,
          kind: "message",
          active: Boolean(parallelSession),
          label: turn.stance ?? "Position",
        });
      }
      continue;
    }

    if (turn.type === "rebuttal") {
      const { from, to } = resolveRebuttalEnds(turn);
      if (!from || !to) continue;
      upsert({
        id: `reb-${from}-${to}-${turn.point ?? ""}`,
        from,
        to,
        kind: "message",
        active: true,
        label: turn.point?.slice(0, 40) ?? "Challenge",
      });
    }
  }

  return Array.from(edgeMap.values());
}
