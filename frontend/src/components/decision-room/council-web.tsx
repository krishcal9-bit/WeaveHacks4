"use client";

import { useMemo } from "react";
import Image from "next/image";
import { Network } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { PopIn } from "@/components/motion/presence";
import { springSoft, springSnappy } from "@/components/motion/variants";
import { cx } from "@/components/ui";
import {
  findLatestTurnForMember,
  influenceByAgent,
  isAgentActive,
  isParallelCouncilNode,
  resolveInfluenceValue,
} from "@/lib/council";
import {
  buildCouncilWebEdges,
  WEB_NODE_BY_ID,
  WEB_NODE_LAYOUT,
  WEB_SHORT_LABEL,
  webBezierPath,
  webNodeStatusLine,
  type CouncilWebEdge,
  type WebNodeId,
} from "@/lib/council-web";
import type { AgentInfluence } from "@/lib/types";
import { useMounted } from "@/lib/use-mounted";
import type { AgentStatus, CouncilInfluenceReport, DebateState, TranscriptTurn } from "@/lib/types";
import { Panel, StatusBadge } from "./primitives";

const AGENT_NODE_ICON_SRC: Record<WebNodeId, string> = {
  cfo: "/assets/atlas-icons/atlas-agent-cfo.png",
  treasury: "/assets/atlas-icons/atlas-agent-treasury.png",
  fpna: "/assets/atlas-icons/atlas-agent-fpna.png",
  risk: "/assets/atlas-icons/atlas-agent-risk.png",
  procurement: "/assets/atlas-icons/atlas-agent-procurement.png",
  reliability: "/assets/atlas-icons/atlas-agent-reliability.png",
};

export function CouncilWeb({
  agentStatuses,
  councilInfluence,
  healthReady,
  nodeName,
  onSelectAgent,
  recommendation,
  running,
  selectedAgentId,
  started,
  transcript,
}: {
  agentStatuses: AgentStatus[];
  councilInfluence?: CouncilInfluenceReport;
  healthReady: boolean;
  nodeName?: string;
  onSelectAgent: (id: string) => void;
  recommendation?: DebateState["recommendation"];
  running: boolean;
  selectedAgentId: string;
  started: boolean;
  transcript: TranscriptTurn[];
}) {
  const mounted = useMounted();
  const influenceById = influenceByAgent(councilInfluence);
  const edges = useMemo(
    () =>
      mounted
        ? buildCouncilWebEdges({ agentStatuses, influenceByAgent: influenceById, running, nodeName, transcript })
        : [],
    [agentStatuses, influenceById, mounted, running, nodeName, transcript],
  );
  const liveEdges = edges.filter((edge) => edge.active).length;
  const statusById = Object.fromEntries(agentStatuses.map((status) => [status.id, status]));

  return (
    <Panel
      id="council-web"
      icon={Network}
      visualIcon="council"
      title="Council"
      action={
        running ? (
          <StatusBadge tone="info" pulse>
            {liveEdges} active
          </StatusBadge>
        ) : (
          <span className="text-[10px] font-medium tracking-wide text-subtle-foreground uppercase">Live graph</span>
        )
      }
      bodyClassName="p-0"
    >
      <div className="council-web-canvas relative w-full overflow-hidden rounded-b-lg">
        <div className="relative mx-auto aspect-[1000/640] w-full max-w-5xl min-h-[300px] sm:min-h-[380px]">
          {mounted ? (
            <>
              <svg
                className="pointer-events-none absolute inset-0 h-full w-full"
                viewBox="0 0 1000 640"
                preserveAspectRatio="xMidYMid meet"
                aria-hidden
              >
                <defs>
                  <linearGradient id="council-edge-glow" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="var(--info)" stopOpacity="0.55" />
                    <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.45" />
                  </linearGradient>
                </defs>
                {edges.map((edge) => (
                  <WebEdgeLayer key={edge.id} edge={edge} />
                ))}
              </svg>

              {WEB_NODE_LAYOUT.map((layout, index) => (
                <WebNodeOrb
                  key={layout.id}
                  layout={layout}
                  layoutIndex={index}
                  memberId={layout.id}
                  agentStatus={statusById[layout.id]}
                  influence={influenceById[layout.id]}
                  healthReady={healthReady}
                  nodeName={nodeName}
                  onSelect={() => onSelectAgent(layout.id)}
                  recommendation={recommendation}
                  running={running}
                  selected={selectedAgentId === layout.id}
                  started={started}
                  transcript={transcript}
                />
              ))}
            </>
          ) : (
            <CouncilWebSkeleton />
          )}
        </div>

        <PopIn show={Boolean(councilInfluence?.summary)} className="border-t border-border/60">
          <p className="px-4 py-2.5 text-center text-[11px] leading-relaxed text-muted-foreground">{councilInfluence?.summary}</p>
        </PopIn>

        {running && isParallelCouncilNode(nodeName) && (
          <p className="border-t border-border/60 px-4 py-2.5 text-center text-[11px] font-medium tracking-wide text-muted-foreground">
            All analysts in session
          </p>
        )}
      </div>
    </Panel>
  );
}

function WebEdgeLayer({ edge }: { edge: CouncilWebEdge }) {
  const from = WEB_NODE_BY_ID[edge.from];
  const to = WEB_NODE_BY_ID[edge.to];
  if (!from || !to) return null;

  const path = webBezierPath(from, to);
  const active = edge.active;
  const weight = edge.weight ?? 25;
  const weightedStroke = 1 + (weight / 100) * 2.2;
  const dur = `${Math.max(0.9, 1.8 - weight / 120)}s`;

  return (
    <g>
      <path
        d={path}
        fill="none"
        stroke={active ? "url(#council-edge-glow)" : "var(--border)"}
        strokeWidth={active ? (edge.kind === "message" ? weightedStroke : weightedStroke * 0.85) : 1}
        strokeOpacity={active ? Math.min(0.95, 0.45 + weight / 140) : 0.28}
        strokeLinecap="round"
        className={active ? "council-edge-flow" : undefined}
      />
      {active && edge.kind === "message" && (
        <>
          <circle r="3.5" fill="var(--info)" opacity="0.85">
            <animateMotion dur={dur} repeatCount="indefinite" path={path} />
          </circle>
          <circle r="2" fill="var(--accent)" opacity="0.65">
            <animateMotion dur={dur} repeatCount="indefinite" path={path} begin="0.45s" />
          </circle>
        </>
      )}
    </g>
  );
}

function WebNodeOrb({
  layout,
  memberId,
  agentStatus,
  influence,
  layoutIndex,
  healthReady,
  nodeName,
  onSelect,
  recommendation,
  running,
  selected,
  started,
  transcript,
}: {
  layout: { x: number; y: number };
  memberId: WebNodeId;
  agentStatus?: AgentStatus;
  influence?: AgentInfluence;
  layoutIndex: number;
  healthReady: boolean;
  nodeName?: string;
  onSelect: () => void;
  recommendation?: DebateState["recommendation"];
  running: boolean;
  selected: boolean;
  started: boolean;
  transcript: TranscriptTurn[];
}) {
  const latestTurn = findLatestTurnForMember(memberId, transcript);
  const active = isAgentActive({ agentStatus, healthReady, memberId, nodeName, running });
  const statusLine = webNodeStatusLine({ agentStatus, active, running, started });
  const shortLabel = WEB_SHORT_LABEL[memberId];
  const influenceValue = resolveInfluenceValue(agentStatus, influence);
  const headline =
    memberId === "cfo" && recommendation?.decision
      ? recommendation.decision
      : latestTurn?.headline ?? agentStatus?.headline;
  const left = `${(layout.x / 1000) * 100}%`;
  const top = `${(layout.y / 640) * 100}%`;
  const reduced = useReducedMotion();
  const orbScale =
    influenceValue && memberId !== "cfo" && memberId !== "reliability"
      ? 0.92 + (influenceValue / 100) * 0.18
      : 1;
  const targetScale = active ? orbScale * 1.06 : selected ? orbScale * 1.03 : orbScale;
  const isInfluenceLeader = influenceValue !== undefined && influenceValue >= 28;

  return (
    <motion.button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`${shortLabel} — ${statusLine}`}
      title={headline ?? undefined}
      data-agent-id={memberId}
      className="absolute z-10 flex w-[108px] -translate-x-1/2 -translate-y-1/2 flex-col items-center text-center focus:outline-none focus-visible:ring-2 focus-visible:ring-info/40 focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
      style={{ left, top }}
      initial={reduced ? false : { opacity: 0, scale: 0.78, y: 10 }}
      animate={{ opacity: 1, scale: targetScale, y: 0 }}
      transition={{ ...springSoft, delay: reduced ? 0 : layoutIndex * 0.05 }}
      whileHover={reduced ? undefined : { scale: targetScale * 1.04 }}
      whileTap={reduced ? undefined : { scale: targetScale * 0.97 }}
    >
      <div
        className={cx(
          "relative grid h-[76px] w-[76px] place-items-center rounded-full border bg-surface/90 shadow-[0_8px_24px_rgba(18,16,14,0.08)] backdrop-blur-md transition-colors duration-300",
          active ? "border-info/50 council-live-glow" : selected ? "border-info/35" : "border-border/80",
          isInfluenceLeader && !active && "council-orb-influence border-info/35",
        )}
      >
        {active && <span className="council-orb-pulse absolute inset-0 rounded-full border border-info/30" aria-hidden />}
        <AgentNodeIcon id={memberId} />
        {active && (
          <motion.span
            className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-info shadow-[0_0_0_3px_var(--surface)]"
            animate={reduced ? undefined : { scale: [1, 1.25, 1], opacity: [1, 0.7, 1] }}
            transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
          />
        )}
        <AnimatePresence>
          {influenceValue && memberId !== "cfo" && memberId !== "reliability" && (
            <motion.span
              key={`influence-${influenceValue}`}
              className="absolute -bottom-1 left-1/2 -translate-x-1/2 rounded-full border border-info/30 bg-background px-1.5 py-0.5 text-[9px] font-bold tabular-nums text-info shadow-sm"
              initial={reduced ? false : { opacity: 0, y: 6, scale: 0.8 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 4, scale: 0.9 }}
              transition={springSnappy}
            >
              {influenceValue}%
            </motion.span>
          )}
        </AnimatePresence>
      </div>

      <span className="mt-2.5 max-w-[104px] truncate text-[12px] font-semibold tracking-tight text-foreground">
        {shortLabel}
      </span>
      <span
        className={cx(
          "mt-0.5 text-[10px] font-medium tracking-wide uppercase",
          active ? "text-info" : "text-subtle-foreground",
        )}
      >
        {influenceValue && memberId !== "cfo" && memberId !== "reliability"
          ? `${influenceValue}% influence`
          : statusLine}
      </span>
      <AnimatePresence>
        {headline && active && (
          <motion.span
            key={headline}
            className="mt-1 line-clamp-2 max-w-[120px] text-center text-[10px] leading-snug text-muted-foreground"
            initial={reduced ? false : { opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 2 }}
            transition={{ duration: 0.24 }}
          >
            {headline}
          </motion.span>
        )}
      </AnimatePresence>
    </motion.button>
  );
}

function AgentNodeIcon({ id }: { id: WebNodeId }) {
  return (
    <span className={cx("agent-node-icon", `agent-node-icon--${id}`)} aria-hidden>
      <Image
        src={AGENT_NODE_ICON_SRC[id]}
        alt=""
        width={192}
        height={192}
        unoptimized
        loading="eager"
        draggable={false}
        className="agent-node-icon__image"
      />
    </span>
  );
}

function CouncilWebSkeleton() {
  return (
    <div className="absolute inset-0 flex items-center justify-center" aria-hidden>
      <div className="grid w-full max-w-md grid-cols-3 gap-6 px-8 opacity-60">
        {Array.from({ length: 6 }).map((_, index) => (
          <div key={index} className="mx-auto flex flex-col items-center gap-2">
            <div className="h-[76px] w-[76px] rounded-full bg-surface-muted" />
            <div className="h-2.5 w-14 rounded bg-surface-muted" />
          </div>
        ))}
      </div>
    </div>
  );
}
