"use client";

import { Users } from "lucide-react";
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
  latestSpeakerId,
  resolveReliabilityValue,
  toneClasses,
} from "@/lib/council";
import type { AgentStatus, DebateState, ReliabilityScore, RosterMember, TranscriptTurn } from "@/lib/types";
import { agentIcon, ReliabilityRing } from "./agent-visuals";
import { Panel, StatusBadge, Waveform } from "./primitives";

export function AgentMatrix({
  agentStatuses,
  healthReady,
  nodeName,
  onSelectAgent,
  recommendation,
  reliabilityScores,
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
  reliabilityScores: ReliabilityScore[];
  running: boolean;
  selectedAgentId: string;
  started: boolean;
  transcript: TranscriptTurn[];
}) {
  const members = COUNCIL_ORDER.map((id) => ROSTER_BY_ID[id]).filter(Boolean) as RosterMember[];
  const statusById = Object.fromEntries(agentStatuses.map((status) => [status.id, status]));
  const scoreById = Object.fromEntries(reliabilityScores.map((score) => [score.agent_id, score]));
  const latestSpeaker = latestSpeakerId(transcript);

  return (
    <Panel
      id="council-matrix"
      icon={Users}
      title="Council"
      action={
        running ? (
          <StatusBadge tone="info" pulse>
            Live council
          </StatusBadge>
        ) : (
          <span className="hidden text-[10px] text-subtle-foreground sm:block">All seats visible</span>
        )
      }
    >
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {members.map((member) => (
          <AgentCard
            key={member.id}
            agentStatus={statusById[member.id]}
            healthReady={healthReady}
            latestSpeaker={latestSpeaker}
            member={member}
            nodeName={nodeName}
            onSelect={() => onSelectAgent(member.id)}
            recommendation={recommendation}
            reliabilityScore={scoreById[member.id]}
            running={running}
            selected={selectedAgentId === member.id}
            started={started}
            transcript={transcript}
          />
        ))}
      </div>
    </Panel>
  );
}

function AgentCard({
  agentStatus,
  healthReady,
  latestSpeaker,
  member,
  nodeName,
  onSelect,
  recommendation,
  reliabilityScore,
  running,
  selected,
  started,
  transcript,
}: {
  agentStatus?: AgentStatus;
  healthReady: boolean;
  latestSpeaker?: string;
  member: RosterMember;
  nodeName?: string;
  onSelect: () => void;
  recommendation?: DebateState["recommendation"];
  reliabilityScore?: ReliabilityScore;
  running: boolean;
  selected: boolean;
  started: boolean;
  transcript: TranscriptTurn[];
}) {
  const latestTurn = findLatestTurnForMember(member.id, transcript);
  const active = isAgentActive({ agentStatus, healthReady, memberId: member.id, nodeName, running });
  const status = getAgentStatus({ active, agentStatus, healthReady, latestSpeaker, latestTurn, member, nodeName, started });
  const statusTone = agentStatusTone(status);
  const snippet = getAgentSnippet({ agentStatus, member, turn: latestTurn, recommendation, healthReady, started });
  const stanceLabel = getAgentStanceLabel(member, latestTurn, recommendation);
  const stanceTone = agentStanceTone(member, latestTurn, recommendation);
  const scoreValue = resolveReliabilityValue(agentStatus, reliabilityScore);

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-controls="agent-inspector"
      aria-pressed={selected}
      data-agent-id={member.id}
      title={member.mandate}
      className={cx(
        "group flex min-w-0 flex-col rounded-lg border p-2.5 text-left transition-all",
        active && "border-info/45 bg-info-bg/35 shadow-[0_0_0_3px_rgba(47,91,183,0.12)]",
        selected && !active && "border-info/40 bg-info-bg/30 shadow-[0_0_0_3px_rgba(47,91,183,0.10)]",
        !selected && !active && "border-border bg-surface hover:border-border-strong hover:bg-surface-quiet",
      )}
    >
      <div className="flex min-w-0 items-start gap-2.5">
        <ReliabilityRing icon={agentIcon(member.id)} value={scoreValue} accentTone={AGENT_TONE[member.id] ?? "info"} active={active} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-[14px] font-semibold leading-tight">{member.label}</span>
            {active && <Waveform active className="shrink-0" />}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <StatusBadge tone={statusTone} pulse={status === "Thinking" || status === "Speaking"}>
              {status}
            </StatusBadge>
            <span className={cx("text-[11px] font-semibold", toneClasses(stanceTone).text)}>{stanceLabel}</span>
          </div>
        </div>
      </div>
      <p className="mt-2 line-clamp-2 min-h-[32px] break-words text-[11px] italic leading-relaxed text-muted-foreground">
        {snippet}
      </p>
    </button>
  );
}
