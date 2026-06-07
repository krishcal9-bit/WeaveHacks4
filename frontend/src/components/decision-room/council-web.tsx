"use client";

import { useMemo } from "react";
import Image from "next/image";
import { Network } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { PopIn } from "@/components/motion/presence";
import { springSoft, springSnappy, transitionFade } from "@/components/motion/variants";
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
  const reduced = useReducedMotion();
  const shouldReduce = reduced ?? false;
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
      className="max-sm:w-[calc(100vw-2rem)] max-sm:max-w-[calc(100vw-2rem)]"
    >
      <div className="council-web-canvas relative w-full overflow-hidden rounded-b-lg max-sm:max-w-[calc(100vw-2rem)]">
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
                  <WebEdgeLayer key={edge.id} edge={edge} reduced={shouldReduce} />
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

function WebEdgeLayer({ edge, reduced }: { edge: CouncilWebEdge; reduced: boolean }) {
  const from = WEB_NODE_BY_ID[edge.from];
  const to = WEB_NODE_BY_ID[edge.to];
  if (!from || !to) return null;

  const path = webBezierPath(from, to);
  const active = edge.active;
  const weight = edge.weight ?? 25;
  const weightedStroke = 1 + (weight / 100) * 2.2;
  const dur = `${Math.max(0.9, 1.8 - weight / 120)}s`;
  const labelX = (from.x + to.x) / 2;
  const labelY = (from.y + to.y) / 2 - 8;
  const tone = edge.kind === "message" ? "var(--info)" : "var(--accent)";
  const showMotion = active && !reduced;

  return (
    <g className={active ? "council-edge-active" : undefined}>
      <motion.path
        d={path}
        fill="none"
        stroke={active ? "url(#council-edge-glow)" : "var(--border)"}
        strokeWidth={active ? (edge.kind === "message" ? weightedStroke : weightedStroke * 0.85) : 1}
        strokeOpacity={active ? Math.min(0.95, 0.45 + weight / 140) : 0.28}
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
        initial={false}
        animate={{ strokeWidth: active ? (edge.kind === "message" ? weightedStroke : weightedStroke * 0.85) : 1 }}
        transition={{ duration: reduced ? 0 : 0.28 }}
        className={active ? "council-edge-flow" : undefined}
      />
      {showMotion && (
        <path
          d={path}
          fill="none"
          stroke={tone}
          strokeWidth={Math.max(2, weightedStroke * 0.9)}
          strokeOpacity={edge.kind === "message" ? 0.72 : 0.44}
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
          className="council-edge-runner"
        />
      )}
      {showMotion && edge.kind === "message" && (
        <>
          <circle r="3.5" fill="var(--info)" opacity="0.85">
            <animateMotion dur={dur} repeatCount="indefinite" path={path} />
          </circle>
          <circle r="2" fill="var(--accent)" opacity="0.65">
            <animateMotion dur={dur} repeatCount="indefinite" path={path} begin="0.45s" />
          </circle>
        </>
      )}
      {active && edge.label && (
        <motion.g
          aria-hidden
          initial={reduced ? false : { opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: reduced ? 0 : 0.2 }}
        >
          <text
            x={labelX}
            y={labelY}
            textAnchor="middle"
            className="text-[18px] font-semibold"
            stroke="var(--background)"
            strokeWidth="7"
            paintOrder="stroke"
          >
            {edge.label}
          </text>
          <text
            x={labelX}
            y={labelY}
            textAnchor="middle"
            className="text-[18px] font-semibold"
            fill="var(--info)"
          >
            {edge.label}
          </text>
        </motion.g>
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
  const shouldReduce = reduced ?? false;
  const backendStatus = String(agentStatus?.status ?? "").toLowerCase();
  const speaking = active && backendStatus === "speaking";
  const orbScale =
    influenceValue && memberId !== "cfo" && memberId !== "reliability"
      ? 0.92 + (influenceValue / 100) * 0.18
      : 1;
  const targetScale = active ? orbScale * 1.06 : selected ? orbScale * 1.03 : orbScale;
  const isInfluenceLeader = influenceValue !== undefined && influenceValue >= 28;
  const showInfluence = Boolean(influenceValue && memberId !== "cfo" && memberId !== "reliability");

  return (
    <motion.button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`${shortLabel} — ${statusLine}`}
      title={headline ?? undefined}
      data-agent-id={memberId}
      data-active={active ? "true" : "false"}
      data-selected={selected ? "true" : "false"}
      data-speaking={speaking ? "true" : "false"}
      className="absolute z-10 flex h-[116px] w-[82px] -translate-x-1/2 -translate-y-1/2 flex-col items-center text-center focus:outline-none focus-visible:ring-2 focus-visible:ring-info/40 focus-visible:ring-offset-2 focus-visible:ring-offset-surface sm:h-[148px] sm:w-[116px]"
      style={{ left, top }}
      initial={reduced ? false : { opacity: 0, scale: 0.78, y: 10 }}
      animate={{ opacity: 1, scale: targetScale, y: 0 }}
      transition={{ ...springSoft, delay: reduced ? 0 : layoutIndex * 0.05 }}
      whileHover={reduced ? undefined : { scale: targetScale * 1.04 }}
      whileTap={reduced ? undefined : { scale: targetScale * 0.97 }}
    >
      <div
        className={cx(
          "council-node-orb relative isolate grid h-[56px] w-[56px] place-items-center rounded-full border bg-surface/90 shadow-[0_8px_24px_rgba(18,16,14,0.08)] backdrop-blur-md transition-colors duration-300 sm:h-[76px] sm:w-[76px]",
          active ? "council-node-orb--active border-info/50" : selected ? "council-node-orb--selected border-info/35" : "border-border/80",
          speaking && "council-node-orb--speaking",
          isInfluenceLeader && !active && "council-orb-influence border-info/35",
        )}
      >
        {shouldReduce ? (
          selected && <span className="council-selected-ring absolute -inset-1.5 rounded-full sm:-inset-2" aria-hidden />
        ) : (
          <AnimatePresence>
            {selected && (
              <motion.span
                layoutId="council-selected-agent-ring"
                className="council-selected-ring absolute -inset-1.5 rounded-full sm:-inset-2"
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.94 }}
                transition={springSnappy}
                aria-hidden
              />
            )}
          </AnimatePresence>
        )}
        {active && <span className="council-orb-pulse absolute -inset-0.5 rounded-full border border-info/30 sm:-inset-1" aria-hidden />}
        {active && <span className="council-activation-glow absolute -inset-2 rounded-full sm:-inset-4" aria-hidden />}
        <AgentNodeIcon id={memberId} />
        {active && (
          <motion.span
            className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-info shadow-[0_0_0_3px_var(--surface)]"
            animate={shouldReduce ? undefined : { scale: [1, 1.25, 1], opacity: [1, 0.7, 1] }}
            transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
          />
        )}
        <AnimatePresence initial={false}>
          {showInfluence && (
            <motion.span
              key={`influence-${memberId}`}
              layout
              className="absolute -bottom-1 left-1/2 min-w-8 -translate-x-1/2 overflow-hidden rounded-full border border-info/30 bg-background px-1.5 py-0.5 text-[8px] font-bold tabular-nums text-info shadow-sm sm:min-w-10 sm:text-[9px]"
              initial={reduced ? false : { opacity: 0, y: 6, scale: 0.8 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 4, scale: 0.9 }}
              transition={springSnappy}
            >
              <AnimatePresence mode="popLayout" initial={false}>
                <motion.span
                  key={influenceValue}
                  className="block"
                  initial={shouldReduce ? false : { y: -8, opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  exit={{ y: 8, opacity: 0 }}
                  transition={{ duration: shouldReduce ? 0 : 0.18 }}
                >
                  {influenceValue}%
                </motion.span>
              </AnimatePresence>
            </motion.span>
          )}
        </AnimatePresence>
      </div>

      <span className="mt-1.5 max-w-[82px] truncate text-[11px] font-semibold tracking-tight text-foreground sm:mt-2.5 sm:max-w-[104px] sm:text-[12px]">
        {shortLabel}
      </span>
      <span
        className={cx(
          "mt-0.5 min-h-3.5 max-w-[86px] truncate text-[9px] font-medium tracking-wide uppercase sm:max-w-[116px] sm:text-[10px]",
          active ? "text-info" : "text-subtle-foreground",
        )}
      >
        {showInfluence
          ? `${influenceValue}% influence`
          : statusLine}
      </span>
      <AnimatePresence initial={false}>
        {headline && active && (
          <motion.span
            key={headline}
            className="mt-1 hidden min-h-6 max-w-[120px] text-center text-[10px] leading-snug text-muted-foreground sm:line-clamp-2"
            initial={reduced ? false : { opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 2 }}
            transition={transitionFade}
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
