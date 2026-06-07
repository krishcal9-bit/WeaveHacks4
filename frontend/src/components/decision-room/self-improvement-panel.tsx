"use client";

import { useMemo } from "react";
import { RefreshCw, Sparkles, TrendingDown, TrendingUp } from "lucide-react";
import { cx } from "@/components/ui";
import { ROSTER_BY_ID } from "@/lib/agents";
import { COUNCIL_ANALYST_IDS, reliabilityColor, reliabilityTone, toneClasses } from "@/lib/council";
import type { AgentImprovementSeat, AgentImprovementState, ReliabilityScore } from "@/lib/types";
import { EmptyState, Panel, SectionLabel, SkeletonText, StatusBadge } from "./primitives";

const SUBAGENTS = COUNCIL_ANALYST_IDS as readonly string[];

export function SelfImprovementPanel({
  agentImprovements,
  reliabilityScores,
  running,
  started,
}: {
  agentImprovements?: AgentImprovementState;
  reliabilityScores: ReliabilityScore[];
  running: boolean;
  started: boolean;
}) {
  const round = agentImprovements?.round ?? 0;
  const agents = agentImprovements?.agents ?? {};
  const rounds = agentImprovements?.rounds ?? [];
  const latestRound = rounds.length ? rounds[rounds.length - 1] : undefined;
  const lastReplaced =
    agentImprovements?.last_replaced ?? agentImprovements?.last_improved ?? latestRound?.replaced ?? latestRound?.improved;
  const latestDirective = lastReplaced ? agents[lastReplaced]?.directive : undefined;
  const latestPromptDirective =
    (lastReplaced ? agents[lastReplaced]?.prompt_improvement_directive : undefined) ?? latestRound?.prompt_improvement_directive;
  const latestReplayCases =
    (lastReplaced ? agents[lastReplaced]?.replay_cases : undefined) ?? latestRound?.replay_cases ?? [];
  const liveScoreById = useMemo(
    () => Object.fromEntries(reliabilityScores.map((s) => [s.agent_id, s.reliability])),
    [reliabilityScores],
  );

  const hasHistory = round > 0 || rounds.length > 0;

  return (
    <Panel
      id="self-improvement"
      visualIcon="memory"
      title="Weave-driven council evolution"
      action={
        hasHistory ? (
          <StatusBadge tone="info">Round {round || rounds.length}</StatusBadge>
        ) : undefined
      }
    >
      {!hasHistory ? (
        running ? (
          <div>
            <div className="mb-2 text-[12px] font-semibold text-info">
              The weakest sub-agent is retired and replaced from its W&B Weave trace after the CFO rules.
            </div>
            <SkeletonText lines={3} />
          </div>
        ) : (
          <EmptyState icon={Sparkles} visualIcon="memory">
            {started
              ? "Five agents stay fixed (CFO + four sub-agents). Each round, W&B Weave scores reliability, the CFO weights input by those scores, and the least-reliable sub-agent is replaced with a new incarnation."
              : "After each round, the least-reliable sub-agent is retired and replaced from its W&B Weave reliability trace. Scores fluctuate every decision."}
          </EmptyState>
        )
      ) : (
        <>
          {latestRound && (
            <p className="break-words text-[12px] leading-relaxed text-muted-foreground">
              Round <span className="font-semibold text-foreground">{latestRound.round}</span>: replaced{" "}
              <span className="font-semibold text-foreground">
                {ROSTER_BY_ID[latestRound.replaced ?? latestRound.improved ?? ""]?.label ??
                  latestRound.replaced_label ??
                  latestRound.improved_label ??
                  latestRound.replaced ??
                  latestRound.improved}
              </span>{" "}
              (retired at {latestRound.prior_reliability ?? "—"}%)
              {latestRound.generation ? ` → generation ${latestRound.generation}` : ""}
              {latestRound.focus ? ` — targets ${latestRound.focus}` : ""}.
            </p>
          )}

          <div className="mt-3 grid gap-2">
            {SUBAGENTS.map((id) => (
              <AgentTrendRow
                key={id}
                id={id}
                seat={agents[id]}
                liveReliability={liveScoreById[id]}
                replacedThisRound={lastReplaced === id}
              />
            ))}
          </div>

          {latestDirective && (
            <div className="mt-3 rounded-md border border-info/35 bg-info-bg/20 px-2.5 py-2">
              <SectionLabel>Replacement directive</SectionLabel>
              <p className="mt-1 break-words text-[11px] leading-relaxed text-foreground">{latestDirective}</p>
            </div>
          )}

          {(latestPromptDirective || latestReplayCases.length > 0) && (
            <div className="mt-2 rounded-md border border-border bg-background px-2.5 py-2">
              <SectionLabel>Auditor replay input</SectionLabel>
              {latestPromptDirective && (
                <p className="mt-1 break-words text-[11px] leading-relaxed text-foreground">{latestPromptDirective}</p>
              )}
              {latestReplayCases.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {latestReplayCases.slice(0, 3).map((item) => (
                    <span key={item} className="rounded border border-border bg-surface px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                      {item}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </Panel>
  );
}

function AgentTrendRow({
  id,
  seat,
  liveReliability,
  replacedThisRound,
}: {
  id: string;
  seat?: AgentImprovementSeat;
  liveReliability?: number;
  replacedThisRound: boolean;
}) {
  const label = ROSTER_BY_ID[id]?.label ?? seat?.label ?? id;
  const history = seat?.reliability_history ?? [];
  const values = history.map((p) => p.reliability);
  const latest = values.length ? values[values.length - 1] : liveReliability;
  const previous = values.length > 1 ? values[values.length - 2] : undefined;
  const delta = typeof latest === "number" && typeof previous === "number" ? latest - previous : undefined;
  const tone = toneClasses(reliabilityTone(latest));

  return (
    <div
      className={cx(
        "rounded-md border px-2.5 py-2",
        replacedThisRound ? "border-info/40 bg-info-bg/20" : "border-border bg-background",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-[12px] font-semibold">{label}</span>
          {replacedThisRound && (
            <StatusBadge tone="info">
              <RefreshCw className="mr-0.5 inline h-3 w-3" strokeWidth={2.5} />
              Replaced
            </StatusBadge>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {typeof delta === "number" && delta !== 0 && (
            <span
              className={cx(
                "inline-flex items-center gap-0.5 text-[10px] font-bold tabular-nums",
                delta > 0 ? "text-positive" : "text-risk",
              )}
            >
              {delta > 0 ? <TrendingUp className="h-3 w-3" strokeWidth={2.5} /> : <TrendingDown className="h-3 w-3" strokeWidth={2.5} />}
              {delta > 0 ? "+" : ""}
              {delta}
            </span>
          )}
          {typeof latest === "number" && (
            <span className={cx("w-9 text-right text-[12px] font-bold tabular-nums", tone.text)}>{latest}%</span>
          )}
        </div>
      </div>

      <div className="mt-1.5 flex items-center gap-2">
        <Sparkline values={values} className="h-6 flex-1" />
        <span className="shrink-0 rounded border border-border bg-surface px-1.5 py-0.5 font-mono text-[9px] font-medium text-muted-foreground">
          {seat?.version_label ?? "—"}
          {seat?.generation ? ` · g${seat.generation}` : ""}
        </span>
      </div>

      {replacedThisRound && seat?.prompt_improvement_directive && (
        <p className="mt-1.5 break-words text-[10px] leading-relaxed text-muted-foreground">
          {seat.prompt_improvement_directive}
        </p>
      )}
    </div>
  );
}

function Sparkline({ values, className = "" }: { values: number[]; className?: string }) {
  const width = 120;
  const height = 24;
  if (values.length === 0) {
    return (
      <svg viewBox={`0 0 ${width} ${height}`} className={className} preserveAspectRatio="none" aria-hidden>
        <line x1={0} y1={height / 2} x2={width} y2={height / 2} stroke="var(--border)" strokeDasharray="3 3" strokeWidth={1} />
      </svg>
    );
  }

  const latest = values[values.length - 1];
  const color = reliabilityColor(latest);
  const pad = 2;
  const span = width - pad * 2;
  const points = values.map((value, index) => {
    const x = values.length === 1 ? pad + span : pad + (span * index) / (values.length - 1);
    const y = height - pad - (Math.max(0, Math.min(100, value)) / 100) * (height - pad * 2);
    return { x, y };
  });
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
  const last = points[points.length - 1];

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className={className} preserveAspectRatio="none" aria-hidden>
      <line x1={0} y1={height / 2} x2={width} y2={height / 2} stroke="var(--border)" strokeWidth={0.75} />
      {points.length > 1 && <path d={path} fill="none" stroke={color} strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" />}
      <circle cx={last.x} cy={last.y} r={2.2} fill={color} />
    </svg>
  );
}
