"use client";

import { useMemo } from "react";
import { motion } from "motion/react";
import { GitBranch, Network } from "lucide-react";
import { cx } from "@/components/ui";
import { AGENT_TONE, COUNCIL_ORDER, ROSTER_BY_ID } from "@/lib/agents";
import {
  agentStanceTone,
  agentStatusTone,
  findLatestTurnForMember,
  getAgentSnippet,
  getAgentStanceLabel,
  getAgentStatus,
  isAgentActive,
  isParallelCouncilNode,
  latestSpeakerId,
  toneClasses,
} from "@/lib/council";
import {
  buildCouncilWebEdges,
  WEB_NODE_BY_ID,
  WEB_NODE_LAYOUT,
  webBezierPath,
  type CouncilWebEdge,
  type WebNodeId,
} from "@/lib/council-web";
import type { AgentStatus, DebateState, TranscriptTurn } from "@/lib/types";
import { AGENT_ICONS } from "./agent-visuals";
import { Panel, StatusBadge, Waveform } from "./primitives";
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
  const edges = useMemo(
    () => buildCouncilWebEdges({ agentStatuses, running, nodeName, transcript }),
    [agentStatuses, running, nodeName, transcript],
  );
  const activeEdgeCount = edges.filter((edge) => edge.active).length;
  const latestSpeaker = latestSpeakerId(transcript);
  const statusById = Object.fromEntries(agentStatuses.map((status) => [status.id, status]));

  return (
    <Panel
      id="council-web"
      icon={Network}
      title="Council web"
      action={
        running ? (
          <StatusBadge tone="info" pulse>
            {activeEdgeCount} live link{activeEdgeCount === 1 ? "" : "s"}
          </StatusBadge>
        ) : (
          <span className="text-[10px] text-subtle-foreground">Collaborative graph</span>
        )
      }
      bodyClassName="p-0"
    >
      <div className="council-web-canvas relative w-full overflow-hidden rounded-b-lg bg-[radial-gradient(circle_at_1px_1px,rgba(120,110,100,0.14)_1px,transparent_0)] [background-size:18px_18px]">
        <div className="relative aspect-[1000/620] w-full min-h-[340px] sm:min-h-[400px]">
          <svg
            className="pointer-events-none absolute inset-0 h-full w-full"
            viewBox="0 0 1000 620"
            preserveAspectRatio="xMidYMid meet"
            aria-hidden
          >
            <defs>
              <linearGradient id="council-edge-active" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="var(--info)" stopOpacity="0.15" />
                <stop offset="50%" stopColor="var(--info)" stopOpacity="0.95" />
                <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.85" />
              </linearGradient>
            </defs>
            {edges.map((edge) => (
              <WebEdgeLayer key={edge.id} edge={edge} />
            ))}
          </svg>

          {WEB_NODE_LAYOUT.map((layout) => {
            const member = ROSTER_BY_ID[layout.id];
            if (!member) return null;
            return (
              <WebNodeCard
                key={layout.id}
                layout={layout}
                memberId={layout.id}
                label={member.label}
                monogram={member.monogram}
                agentStatus={statusById[layout.id]}
                healthReady={healthReady}
                latestSpeaker={latestSpeaker}
                nodeName={nodeName}
                onSelect={() => onSelectAgent(layout.id)}
                recommendation={recommendation}
                running={running}
                selected={selectedAgentId === layout.id}
                started={started}
                transcript={transcript}
              />
            );
          })}
        </div>

        {running && isParallelCouncilNode(nodeName) && (
          <div className="border-t border-border bg-info-bg/30 px-3 py-2 text-[11px] text-info">
            <span className="inline-flex items-center gap-1.5 font-semibold">
              <GitBranch className="h-3.5 w-3.5" strokeWidth={2.25} />
              Full mesh — Treasury, FP&A, Risk, and Procurement are in session together
            </span>
          </div>
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
  const dim = !edge.active;
  const stroke =
    edge.kind === "peer" ? "var(--warning)" : edge.kind === "message" ? "url(#council-edge-active)" : "var(--border-strong)";

  return (
    <g>
      <path
        d={path}
        fill="none"
        stroke={stroke}
        strokeWidth={edge.active ? (edge.kind === "message" ? 2.4 : 1.8) : 1.2}
        strokeOpacity={dim ? 0.35 : edge.kind === "hub" ? 0.7 : 0.9}
        strokeDasharray={edge.kind === "peer" && edge.active ? "6 5" : edge.active ? "none" : "4 6"}
        className={edge.active ? "council-edge-flow" : undefined}
      />
      {edge.active && (
        <>
          <circle r="5" fill="var(--info)" className="council-edge-packet">
            <animateMotion dur={`${edge.kind === "message" ? 1.1 : 1.6}s`} repeatCount="indefinite" path={path} />
          </circle>
          <circle r="3.5" fill="var(--accent)" opacity="0.85">
            <animateMotion dur={`${edge.kind === "message" ? 1.5 : 2.1}s`} repeatCount="indefinite" path={path} />
          </circle>
        </>
      )}
      {edge.active && edge.label && edge.kind === "message" && (
        <text fontSize="10" fill="var(--muted-foreground)" opacity="0.9">
          <textPath href={`#${edge.id}-label`} startOffset="42%" textAnchor="middle">
            {edge.label.length > 28 ? `${edge.label.slice(0, 28)}…` : edge.label}
          </textPath>
        </text>
      )}
      <path id={`${edge.id}-label`} d={path} fill="none" stroke="none" />
    </g>
  );
}

function WebNodeCard({
  layout,
  memberId,
  label,
  monogram,
  agentStatus,
  healthReady,
  latestSpeaker,
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
  label: string;
  monogram: string;
  agentStatus?: AgentStatus;
  healthReady: boolean;
  latestSpeaker?: string;
  nodeName?: string;
  onSelect: () => void;
  recommendation?: DebateState["recommendation"];
  running: boolean;
  selected: boolean;
  started: boolean;
  transcript: TranscriptTurn[];
}) {
  const member = ROSTER_BY_ID[memberId];
  if (!member) return null;

  const latestTurn = findLatestTurnForMember(memberId, transcript);
  const active = isAgentActive({ agentStatus, healthReady, memberId, nodeName, running });
  const status = getAgentStatus({ active, agentStatus, healthReady, latestSpeaker, latestTurn, member, nodeName, started });
  const statusTone = agentStatusTone(status);
  const snippet = getAgentSnippet({ agentStatus, member, turn: latestTurn, recommendation, healthReady, started });
  const stanceLabel = getAgentStanceLabel(member, latestTurn, recommendation);
  const stanceTone = agentStanceTone(member, latestTurn, recommendation);
  const accent = toneClasses(AGENT_TONE[memberId] ?? "neutral");
  const left = `${(layout.x / 1000) * 100}%`;
  const top = `${(layout.y / 620) * 100}%`;

  return (
    <motion.button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      data-agent-id={memberId}
      initial={false}
      animate={{
        scale: active ? 1.03 : 1,
        boxShadow: active
          ? "0 0 0 3px color-mix(in srgb, var(--info) 22%, transparent), 0 10px 28px rgba(18,16,14,0.12)"
          : selected
            ? "0 0 0 2px color-mix(in srgb, var(--info) 18%, transparent)"
            : "0 4px 14px rgba(18,16,14,0.06)",
      }}
      transition={{ type: "spring", stiffness: 420, damping: 32 }}
      className={cx(
        "absolute z-10 w-[min(168px,30vw)] -translate-x-1/2 -translate-y-1/2 rounded-xl border bg-surface p-2 text-left shadow-sm transition-colors",
        active ? "border-info/45" : selected ? "border-info/35" : "border-border hover:border-border-strong",
      )}
      style={{ left, top }}
    >
      <div className="flex items-start gap-2">
        <span
          className={cx(
            "grid h-9 w-9 shrink-0 place-items-center rounded-lg border text-[11px] font-bold",
            accent.soft,
            active && "ring-2 ring-info/30",
          )}
        >
          <SeatIcon id={memberId} className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-1">
            <span className="truncate text-[11px] font-bold leading-tight">{monogram}</span>
            {active && <Waveform active className="shrink-0 scale-90" />}
          </div>
          <div className="truncate text-[12px] font-semibold leading-tight">{label}</div>
          <div className="mt-1 flex flex-wrap gap-1">
            <StatusBadge tone={statusTone} pulse={status === "Thinking" || status === "Speaking"}>
              {status}
            </StatusBadge>
          </div>
        </div>
      </div>
      <p className={cx("mt-1.5 line-clamp-2 text-[10px] font-semibold", toneClasses(stanceTone).text)}>{stanceLabel}</p>
      <p className="mt-0.5 line-clamp-2 text-[10px] italic leading-relaxed text-muted-foreground">{snippet}</p>
      {COUNCIL_ORDER.includes(memberId as (typeof COUNCIL_ORDER)[number]) && memberId !== "cfo" && active && (
        <div className="mt-1.5 flex gap-0.5">
          {Array.from({ length: 3 }).map((_, index) => (
            <span
              key={index}
              className="council-node-pulse h-1 flex-1 rounded-full bg-info/25"
              style={{ animationDelay: `${index * 0.15}s` }}
            />
          ))}
        </div>
      )}
    </motion.button>
  );
}

function SeatIcon({ id, className }: { id: string; className?: string }) {
  const Icon = AGENT_ICONS[id] ?? ServerCog;
  return <Icon className={className} strokeWidth={2} />;
}
