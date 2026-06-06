"use client";

import { useState } from "react";
import { Activity, ChevronDown, ExternalLink } from "lucide-react";
import { cx } from "@/components/ui";
import { COUNCIL_ORDER, ROSTER_BY_ID } from "@/lib/agents";
import { averageReliability, reliabilityColor, reliabilityTone, toneClasses } from "@/lib/council";
import { titleCase } from "@/lib/format";
import type { LearningReport, ReliabilityScore, TraceSummary } from "@/lib/types";
import { EmptyState, Panel, SectionLabel, SkeletonText, StatusBadge } from "./primitives";

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

  return (
    <Panel
      id="evals"
      icon={Activity}
      eyebrow="W&B self-improvement"
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
            <div className="mb-2 text-[12px] font-semibold text-info">Reliability auditor scores the council after the CFO rules.</div>
            <SkeletonText lines={3} />
          </div>
        ) : (
          <EmptyState icon={Activity}>
            {started
              ? "The reliability auditor will score each agent and gate prompt promotion."
              : "Per-agent reliability scores and the W&B promotion gate appear after a run."}
          </EmptyState>
        )
      ) : (
        <>
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

function DetailNote({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-background px-2.5 py-1.5">
      <div className="text-[10px] font-semibold uppercase tracking-[0.06em] text-subtle-foreground">{label}</div>
      <div className="mt-0.5 break-words text-[11px] leading-relaxed text-foreground">{value}</div>
    </div>
  );
}
