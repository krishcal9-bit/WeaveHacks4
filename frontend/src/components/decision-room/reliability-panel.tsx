"use client";

import { useState } from "react";
import { Activity, ChevronDown, ExternalLink } from "lucide-react";
import { cx } from "@/components/ui";
import { COUNCIL_ORDER, ROSTER_BY_ID } from "@/lib/agents";
import { averageReliability, reliabilityColor, reliabilityTone, toneClasses } from "@/lib/council";
import { titleCase } from "@/lib/format";
import type { LearningReport, PromptVersion, ReliabilityScore, TraceSummary } from "@/lib/types";
import { EmptyState, Panel, SectionLabel, SkeletonText, StatusBadge } from "./primitives";

const SCORE_DIMENSIONS = [
  "evidence_grounding",
  "forecast_calibration",
  "policy_compliance",
  "debate_value",
  "outcome_accuracy",
  "confidence_calibration",
  "trace_quality",
] as const;

export function ReliabilityPanel({
  reliabilityScores,
  learningReport,
  traceSummary,
  running,
  started,
}: {
  reliabilityScores: ReliabilityScore[];
  learningReport?: LearningReport;
  traceSummary?: TraceSummary;
  running: boolean;
  started: boolean;
}) {
  const [showDetails, setShowDetails] = useState(false);
  const avg = averageReliability(reliabilityScores);
  const scoreById = Object.fromEntries(reliabilityScores.map((score) => [score.agent_id, score]));
  const scoredAgents = COUNCIL_ORDER.filter((id) => id !== "reliability" && scoreById[id]);
  const weaveUrl = learningReport?.weave_url ?? traceSummary?.weave_url ?? undefined;
  const formula = learningReport?.score_formula;
  const promptVersions = learningReport?.prompt_versions ?? [];

  return (
    <Panel
      id="evals"
      icon={Activity}
      visualIcon="health"
      title="Council reliability"
      action={
        typeof avg === "number" ? (
          <StatusBadge tone={reliabilityTone(avg)}>{avg}% avg</StatusBadge>
        ) : undefined
      }
    >
      {reliabilityScores.length === 0 ? (
        running ? (
          <div>
            <div className="mb-2 text-[12px] font-semibold text-info">Reliability Auditor builds evaluator scorecards after the CFO rules.</div>
            <SkeletonText lines={3} />
          </div>
        ) : (
          <EmptyState icon={Activity} visualIcon="health">
            {started
              ? "Reliability will audit each agent's grounding, calibration, policy compliance, debate value, trace quality, and weaknesses."
              : "Per-agent scorecards, replay cases, and the W&B promotion gate appear after a run."}
          </EmptyState>
        )
      ) : (
        <>
          {learningReport?.audit_scope && (
            <div className="mb-2 rounded-md border border-info/25 bg-info-bg/15 px-2.5 py-1.5 text-[11px] font-semibold leading-relaxed text-info">
              {learningReport.audit_scope}
            </div>
          )}

          {learningReport?.summary && (
            <p className="break-words text-[12px] leading-relaxed text-muted-foreground">{learningReport.summary}</p>
          )}

          <div className="mt-2.5 grid gap-1.5">
            {scoredAgents.map((id) => (
              <AgentScoreBar key={id} label={ROSTER_BY_ID[id]?.label ?? id} value={scoreById[id].reliability} />
            ))}
          </div>

          <button
            type="button"
            onClick={() => setShowDetails((value) => !value)}
            aria-expanded={showDetails}
            className="mt-3 inline-flex items-center gap-1 text-[11px] font-semibold text-muted-foreground transition-colors hover:text-foreground"
          >
            <ChevronDown className={cx("h-3.5 w-3.5 transition-transform", !showDetails && "-rotate-90")} strokeWidth={2.25} />
            {showDetails ? "Hide eval details" : "Show eval details"}
          </button>

          {showDetails && (
            <div className="mt-2 grid gap-2">
              {formula && Object.keys(formula).length > 0 && (
                <div className="rounded-md border border-border bg-background p-2.5">
                  <SectionLabel>Score formula</SectionLabel>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {Object.entries(formula).map(([key, weight]) => (
                      <span key={key} className="rounded border border-border bg-surface px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                        {titleCase(key)} <span className="font-semibold text-foreground">{Math.round(weight * 100)}%</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {learningReport?.promotion_gate && (
                <DetailNote label="Promotion gate" value={learningReport.promotion_gate} />
              )}
              {learningReport?.eval_dataset && <DetailNote label="Eval dataset" value={learningReport.eval_dataset} />}
              {learningReport?.replay_plan && learningReport.replay_plan.length > 0 && (
                <DetailNote label="Replay plan" value={learningReport.replay_plan.slice(0, 3).join(" · ")} />
              )}
              {learningReport?.prompt_improvement_directives && learningReport.prompt_improvement_directives.length > 0 && (
                <DetailNote label="Prompt directives" value={learningReport.prompt_improvement_directives.slice(0, 3).join(" · ")} />
              )}
              {promptVersions.length > 0 && <PromptGateList promptVersions={promptVersions} />}

              <div className="grid gap-2">
                {scoredAgents.map((id) => (
                  <AgentAuditCard key={id} label={ROSTER_BY_ID[id]?.label ?? id} score={scoreById[id]} />
                ))}
              </div>
            </div>
          )}

          {weaveUrl && (
            <a
              href={weaveUrl}
              target="_blank"
              rel="noreferrer"
              className="mt-2.5 inline-flex items-center gap-1 text-[11px] font-semibold text-info"
            >
              Open in W&B Weave
              <ExternalLink className="h-3 w-3" strokeWidth={2.25} />
            </a>
          )}
        </>
      )}
    </Panel>
  );
}

function PromptGateList({ promptVersions }: { promptVersions: PromptVersion[] }) {
  return (
    <div className="rounded-md border border-border bg-background p-2.5">
      <SectionLabel>Role prompt gates</SectionLabel>
      <div className="mt-2 grid gap-2">
        {promptVersions.map((version) => {
          const roleId = version.agent ?? version.role;
          const label = ROSTER_BY_ID[roleId]?.label ?? titleCase(roleId);
          const activeHash = version.active_prompt_hash ?? version.prompt_hash;
          const dimensions = version.reliability_dimensions ?? [];

          return (
            <div key={roleId} className="rounded border border-border bg-surface px-2 py-1.5">
              <div className="flex min-w-0 items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-[11px] font-semibold text-foreground">{label}</div>
                  <div className="mt-0.5 truncate text-[10px] font-medium tabular-nums text-muted-foreground">
                    {version.current ?? version.version} → {version.candidate ?? "candidate pending"}
                  </div>
                </div>
                {version.gate_metric && (
                  <span className="shrink-0 rounded bg-info-bg/25 px-1.5 py-0.5 text-[9.5px] font-bold text-info">
                    {version.gate_metric.replaceAll("_", " ")}
                  </span>
                )}
              </div>

              <div className="mt-1.5 flex flex-wrap gap-1">
                <HashPill label="active" value={activeHash} />
                <HashPill label="candidate" value={version.candidate_prompt_hash} />
                {version.replay_set && (
                  <span className="rounded border border-border bg-background px-1.5 py-0.5 text-[9.5px] font-medium text-muted-foreground">
                    {version.replay_set}
                  </span>
                )}
              </div>

              {dimensions.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {dimensions.slice(0, 3).map((dimension) => (
                    <span key={dimension} className="rounded border border-border bg-background px-1.5 py-0.5 text-[9.5px] font-medium text-muted-foreground">
                      {dimension.replaceAll("_", " ")}
                    </span>
                  ))}
                </div>
              )}

              {version.promotion_gate && (
                <div className="mt-1 line-clamp-2 text-[10.5px] leading-relaxed text-muted-foreground" title={version.promotion_gate}>
                  {version.promotion_gate}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function HashPill({ label, value }: { label: string; value?: string }) {
  if (!value) return null;
  return (
    <span className="rounded border border-border bg-background px-1.5 py-0.5 font-mono text-[9.5px] font-semibold text-subtle-foreground">
      {label}:{value}
    </span>
  );
}

function AgentScoreBar({ label, value }: { label: string; value: number }) {
  const tone = toneClasses(reliabilityTone(value));
  return (
    <div className="flex items-center gap-2">
      <span className="w-[120px] shrink-0 truncate text-[11px] font-medium">{label}</span>
      <span className="h-2 flex-1 overflow-hidden rounded-full bg-surface-muted">
        <span className="block h-full rounded-full" style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: reliabilityColor(value) }} />
      </span>
      <span className={cx("w-9 shrink-0 text-right text-[11px] font-bold tabular-nums", tone.text)}>{value}%</span>
    </div>
  );
}

function AgentAuditCard({ label, score }: { label: string; score: ReliabilityScore }) {
  const replayCases = score.replay_cases ?? [];
  const weaknesses = score.known_weaknesses ?? [];
  const directive = score.prompt_improvement_directive ?? score.prompt_adjustment;

  return (
    <div className="rounded-md border border-border bg-background p-2.5">
      <div className="flex items-center justify-between gap-2">
        <SectionLabel>{label}</SectionLabel>
        <span className={cx("text-[11px] font-bold tabular-nums", toneClasses(reliabilityTone(score.reliability)).text)}>
          {score.reliability}%
        </span>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-1.5">
        {SCORE_DIMENSIONS.map((dimension) => {
          const value = score[dimension];
          if (typeof value !== "number") return null;
          return (
            <div key={dimension} className="flex min-w-0 items-center justify-between gap-2 rounded border border-border bg-surface px-1.5 py-1">
              <span className="truncate text-[10px] font-medium text-muted-foreground">{titleCase(dimension)}</span>
              <span className={cx("text-[10px] font-bold tabular-nums", toneClasses(reliabilityTone(value)).text)}>{value}</span>
            </div>
          );
        })}
      </div>

      {score.rationale && <p className="mt-2 break-words text-[11px] leading-relaxed text-muted-foreground">{score.rationale}</p>}
      {weaknesses.length > 0 && <DetailNote label="Known weaknesses" value={weaknesses.slice(0, 2).join(" · ")} />}
      {replayCases.length > 0 && <DetailNote label="Replay cases" value={replayCases.slice(0, 2).join(" · ")} />}
      {directive && <DetailNote label="Self-improvement directive" value={directive} />}
    </div>
  );
}

function DetailNote({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-background px-2.5 py-1.5">
      <div className="text-[10px] font-semibold uppercase tracking-[0.06em] text-subtle-foreground">{label}</div>
      <div className="mt-0.5 break-words text-[11px] leading-relaxed text-foreground">{value}</div>
    </div>
  );
}
