"use client";

import { useMemo } from "react";
import { Network } from "lucide-react";
import { cx } from "@/components/ui";
import { AGENT_TONE } from "@/lib/agents";
import {
  findLatestTurnForMember,
  isAgentActive,
  isParallelCouncilNode,
  toneClasses,
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
import { useMounted } from "@/lib/use-mounted";
import type { AgentStatus, DebateState, TranscriptTurn } from "@/lib/types";
import { AGENT_ICONS } from "./agent-visuals";
import { Panel, StatusBadge } from "./primitives";
import { ServerCog } from "lucide-react";

export function CouncilWeb({
  agentStatuses,
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
  const edges = useMemo(
    () => (mounted ? buildCouncilWebEdges({ agentStatuses, running, nodeName, transcript }) : []),
    [agentStatuses, mounted, running, nodeName, transcript],
  );
  const liveEdges = edges.filter((edge) => edge.active).length;
  const statusById = Object.fromEntries(agentStatuses.map((status) => [status.id, status]));

  return (
    <Panel
      id="council-web"
      icon={Network}
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

              {WEB_NODE_LAYOUT.map((layout) => (
                <WebNodeOrb
                  key={layout.id}
                  layout={layout}
                  memberId={layout.id}
                  agentStatus={statusById[layout.id]}
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

  return (
    <g>
      <path
        d={path}
        fill="none"
        stroke={active ? "url(#council-edge-glow)" : "var(--border)"}
        strokeWidth={active ? (edge.kind === "message" ? 2 : 1.5) : 1}
        strokeOpacity={active ? 0.85 : 0.28}
        strokeLinecap="round"
        className={active ? "council-edge-flow" : undefined}
      />
      {active && edge.kind === "message" && (
        <circle r="4" fill="var(--info)" opacity="0.9">
          <animateMotion dur="1.35s" repeatCount="indefinite" path={path} />
        </circle>
      )}
    </g>
  );
}

function WebNodeOrb({
  layout,
  memberId,
  agentStatus,
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
  const accent = toneClasses(AGENT_TONE[memberId] ?? "neutral");
  const shortLabel = WEB_SHORT_LABEL[memberId];
  const headline =
    memberId === "cfo" && recommendation?.decision
      ? recommendation.decision
      : latestTurn?.headline ?? agentStatus?.headline;
  const left = `${(layout.x / 1000) * 100}%`;
  const top = `${(layout.y / 640) * 100}%`;

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`${shortLabel} — ${statusLine}`}
      title={headline ?? undefined}
      data-agent-id={memberId}
      className={cx(
        "absolute z-10 flex w-[108px] -translate-x-1/2 -translate-y-1/2 flex-col items-center text-center transition-transform duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-info/40 focus-visible:ring-offset-2 focus-visible:ring-offset-surface",
        active && "scale-[1.04]",
        !active && selected && "scale-[1.02]",
      )}
      style={{ left, top }}
    >
      <div
        className={cx(
          "relative grid h-[76px] w-[76px] place-items-center rounded-full border bg-surface/90 shadow-[0_8px_24px_rgba(18,16,14,0.08)] backdrop-blur-md transition-colors",
          active ? "border-info/50" : selected ? "border-info/35" : "border-border/80",
        )}
      >
        {active && <span className="council-orb-pulse absolute inset-0 rounded-full border border-info/30" aria-hidden />}
        <span className={cx("grid h-10 w-10 place-items-center rounded-full", accent.soft)}>
          <SeatIcon id={memberId} className="h-[18px] w-[18px]" />
        </span>
        {active && (
          <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-info shadow-[0_0_0_3px_var(--surface)]" />
        )}
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
        {statusLine}
      </span>
      {headline && active && (
        <span className="mt-1 line-clamp-2 max-w-[120px] text-center text-[10px] leading-snug text-muted-foreground">
          {headline}
        </span>
      )}
    </button>
  );
}

function SeatIcon({ id, className }: { id: string; className?: string }) {
  const Icon = AGENT_ICONS[id] ?? ServerCog;
  return <Icon className={className} strokeWidth={1.85} />;
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
