"use client";

import type { ReactNode } from "react";
import { Database, ExternalLink, ScrollText, Wrench } from "lucide-react";
import { cx } from "@/components/ui";
import { AGENT_TONE } from "@/lib/agents";
import {
  agentStanceTone,
  findLatestTurnForMember,
  getAgentSnippet,
  getAgentStanceLabel,
  isAgentActive,
  NODE_LABEL,
  reliabilityColor,
  reliabilityDimensionsFromScore,
  reliabilityTone,
  resolveReliabilityValue,
  toneClasses,
} from "@/lib/council";
import { titleCase } from "@/lib/format";
import type {
  AgentStatus,
  DebateState,
  LearningReport,
  RedisActivity,
  ReliabilityScore,
  RosterMember,
  TranscriptTurn,
} from "@/lib/types";
import { agentIcon, ReliabilityRing } from "./agent-visuals";
import { Panel, SectionLabel, StatusBadge } from "./primitives";

export function AgentInspector({
  member,
  agentStatus,
  reliabilityScore,
  transcript,
  recommendation,
  redisActivity,
  learningReport,
  nodeName,
  running,
  healthReady,
  started,
  className = "",
  bodyClassName = "",
}: {
  member: RosterMember;
  agentStatus?: AgentStatus;
  reliabilityScore?: ReliabilityScore;
  transcript: TranscriptTurn[];
  recommendation?: DebateState["recommendation"];
  redisActivity: RedisActivity[];
  learningReport?: LearningReport;
  nodeName?: string;
  running: boolean;
  healthReady: boolean;
  started: boolean;
  className?: string;
  bodyClassName?: string;
}) {
  const turn = findLatestTurnForMember(member.id, transcript);
  const active = isAgentActive({ agentStatus, healthReady, memberId: member.id, nodeName, running });
  const snippet = getAgentSnippet({ agentStatus, member, turn, recommendation, healthReady, started });
  const stanceLabel = getAgentStanceLabel(member, turn, recommendation);
  const stanceTone = agentStanceTone(member, turn, recommendation);
  const scoreValue = resolveReliabilityValue(agentStatus, reliabilityScore);
  const accent = AGENT_TONE[member.id] ?? "info";

  const dimensions = reliabilityScore?.agent_id
    ? reliabilityDimensionsFromScore(reliabilityScore)
    : (agentStatus?.reliability_dimensions as Record<string, number | undefined> | undefined);
  const weaknesses = reliabilityScore?.known_weaknesses ?? agentStatus?.known_weaknesses ?? [];
  const promptAdjustment = reliabilityScore?.prompt_adjustment ?? agentStatus?.prompt_adjustment;
  const promptDirective = reliabilityScore?.prompt_improvement_directive ?? agentStatus?.prompt_improvement_directive;
  const replayCases = reliabilityScore?.replay_cases ?? agentStatus?.replay_cases ?? [];
  const promotionGate = reliabilityScore?.promotion_gate ?? agentStatus?.promotion_gate ?? learningReport?.promotion_gate;
  const rationale = reliabilityScore?.rationale ?? agentStatus?.reliability_rationale;

  const uds = agentStatus?.uds;
  const tools = uds?.tools ?? [];
  const redisKeys = uds?.redis_keys ?? [];
  const latestRedis = redisActivity.at(-1);
  const forecastNotes = [
    ...(turn?.forecast_assumptions ?? []),
    ...(turn?.scenario_sensitivities ?? []),
    ...(turn?.plan_vs_actual_deltas ?? []),
  ];
  const controlNotes = [
    ...(turn?.control_findings ?? []),
    ...(turn?.missing_evidence_requests ?? []),
    ...(turn?.approval_or_policy_blockers ?? []),
  ];
  const negotiationLevers = turn?.negotiation_levers ?? [];

  return (
    <Panel
      id="agent-inspector"
      icon={agentIcon(member.id)}
      visualIcon="council"
      title={member.label}
      className={className}
      bodyClassName={bodyClassName}
      action={
        <StatusBadge tone={active ? "info" : scoreValue ? reliabilityTone(scoreValue) : "neutral"} pulse={active}>
          {active ? NODE_LABEL[nodeName ?? ""]?.split(" ")[0] ?? "Active" : (agentStatus?.status ?? "Waiting")}
        </StatusBadge>
      }
    >
      <div className="flex items-start gap-3">
        <ReliabilityRing icon={agentIcon(member.id)} value={scoreValue} accentTone={accent} active={active} size="lg" />
        <div className="min-w-0 flex-1">
          <div className="text-[12px] font-medium text-subtle-foreground">{member.role}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <SectionLabel>{member.id === "reliability" ? "Mode" : "Stance"}</SectionLabel>
            <span className={cx("text-[12px] font-semibold", toneClasses(stanceTone).text)}>{stanceLabel}</span>
          </div>
          {member.mandate && <p className="mt-1.5 break-words text-[12px] leading-relaxed text-muted-foreground">{member.mandate}</p>}
        </div>
      </div>

      <div className="mt-3 rounded-md border border-border bg-background p-2.5">
        <SectionLabel>Latest position</SectionLabel>
        {turn?.role_specific_lens && (
          <p className="mt-1 break-words text-[11px] font-semibold text-subtle-foreground">{turn.role_specific_lens}</p>
        )}
        <p className="mt-1 break-words text-[13px] leading-relaxed text-foreground">{snippet}</p>
        {turn?.key_points && turn.key_points.length > 0 && (
          <ul className="mt-2 grid gap-1">
            {turn.key_points.slice(0, 4).map((point) => (
              <li key={point} className="flex gap-1.5 text-[11px] leading-relaxed text-muted-foreground">
                <span className={cx("mt-1.5 h-1 w-1 shrink-0 rounded-full", toneClasses(accent).dot)} />
                <span className="break-words">{point}</span>
              </li>
            ))}
          </ul>
        )}
        {forecastNotes.length > 0 && (
          <div className="mt-2 rounded border border-warning/20 bg-warning-bg/20 px-2 py-1.5">
            <SectionLabel>Forecast check</SectionLabel>
            <ul className="mt-1 grid gap-1">
              {forecastNotes.slice(0, 4).map((note) => (
                <li key={note} className="break-words text-[11px] leading-relaxed text-muted-foreground">
                  {note}
                </li>
              ))}
            </ul>
          </div>
        )}
        {controlNotes.length > 0 && (
          <div className="mt-2 rounded border border-risk/20 bg-risk-bg/20 px-2 py-1.5">
            <SectionLabel>Controls check</SectionLabel>
            <ul className="mt-1 grid gap-1">
              {controlNotes.slice(0, 4).map((note) => (
                <li key={note} className="break-words text-[11px] leading-relaxed text-muted-foreground">
                  {note}
                </li>
              ))}
            </ul>
          </div>
        )}
        {negotiationLevers.length > 0 && (
          <div className="mt-2 rounded border border-positive/20 bg-positive-bg/20 px-2 py-1.5">
            <SectionLabel>Negotiation levers</SectionLabel>
            <ul className="mt-1 grid gap-1">
              {negotiationLevers.slice(0, 4).map((lever) => (
                <li key={lever} className="break-words text-[11px] leading-relaxed text-muted-foreground">
                  {lever}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Reliability scorecard */}
      <div className="mt-3">
        <div className="flex items-center justify-between gap-2">
          <SectionLabel>W&B reliability</SectionLabel>
          <span
            className="rounded-full border px-2 py-0.5 text-[12px] font-bold tabular-nums"
            style={{
              borderColor: scoreValue ? reliabilityColor(scoreValue) : "var(--border)",
              color: scoreValue ? reliabilityColor(scoreValue) : "var(--muted-foreground)",
            }}
          >
            {scoreValue ? `${scoreValue}%` : "Pending"}
          </span>
        </div>
        {rationale && <p className="mt-1.5 break-words text-[12px] leading-relaxed text-muted-foreground">{rationale}</p>}

        {dimensions && Object.values(dimensions).some((value) => typeof value === "number") && (
          <div className="mt-2 grid gap-1.5">
            {Object.entries(dimensions)
              .filter(([, value]) => typeof value === "number")
              .map(([key, value]) => (
                <DimensionBar key={key} label={titleCase(key)} value={value as number} />
              ))}
          </div>
        )}

        {(weaknesses.length > 0 || promptAdjustment || promptDirective || replayCases.length > 0 || promotionGate) && (
          <div className="mt-2.5 grid gap-1.5">
            {weaknesses.length > 0 && (
              <InspectorNote label="Known weaknesses" value={weaknesses.slice(0, 3).join(" · ")} />
            )}
            {replayCases.length > 0 && <InspectorNote label="Replay cases" value={replayCases.slice(0, 3).join(" · ")} />}
            {promptDirective && <InspectorNote label="Self-improvement directive" value={promptDirective} />}
            {promptAdjustment && <InspectorNote label="Prompt adjustment" value={promptAdjustment} />}
            {promotionGate && (
              <InspectorNote
                label="Promotion gate"
                value={promotionGate}
                action={
                  learningReport?.weave_url ? (
                    <a
                      href={learningReport.weave_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex shrink-0 items-center gap-1 text-[11px] font-semibold text-info"
                    >
                      Weave
                      <ExternalLink className="h-3 w-3" strokeWidth={2.25} />
                    </a>
                  ) : undefined
                }
              />
            )}
          </div>
        )}
      </div>

      {/* Grounding — only what the backend actually reported */}
      {(tools.length > 0 || redisKeys.length > 0 || latestRedis) && (
        <div className="mt-3 rounded-md border border-border bg-background p-2.5">
          <SectionLabel>Grounding</SectionLabel>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {tools.map((tool) => (
              <span key={tool} className="inline-flex items-center gap-1 rounded border border-border bg-surface px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                <Wrench className="h-3 w-3" strokeWidth={2} />
                {tool}
              </span>
            ))}
            {redisKeys.map((key) => (
              <span key={key} className="inline-flex items-center gap-1 rounded border border-info/20 bg-info-bg px-1.5 py-0.5 text-[10px] font-medium text-info">
                <Database className="h-3 w-3" strokeWidth={2} />
                <span className="max-w-[160px] truncate">{key}</span>
              </span>
            ))}
          </div>
          {latestRedis && tools.length === 0 && redisKeys.length === 0 && (
            <p className="mt-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <ScrollText className="h-3 w-3 shrink-0" strokeWidth={2} />
              <span className="truncate">{latestRedis.label}: {latestRedis.detail}</span>
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}

function DimensionBar({ label, value }: { label: string; value: number }) {
  const tone = toneClasses(reliabilityTone(value));
  return (
    <div className="flex items-center gap-2">
      <span className="w-[120px] shrink-0 truncate text-[10px] text-muted-foreground">{label}</span>
      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-muted">
        <span className={cx("block h-full rounded-full", tone.dot)} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
      </span>
      <span className="w-8 shrink-0 text-right text-[10px] font-semibold tabular-nums">{value}%</span>
    </div>
  );
}

function InspectorNote({ label, value, action }: { label: string; value: string; action?: ReactNode }) {
  return (
    <div className="rounded-md border border-border bg-background px-2.5 py-1.5">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] font-semibold uppercase tracking-[0.06em] text-subtle-foreground">{label}</div>
        {action}
      </div>
      <div className="mt-0.5 break-words text-[11px] leading-relaxed text-foreground">{value}</div>
    </div>
  );
}
