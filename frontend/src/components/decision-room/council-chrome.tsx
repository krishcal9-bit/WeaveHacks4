"use client";

import { memo } from "react";
import { AlertTriangle, Loader2, Radio, ShieldAlert, XCircle } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { AtlasIcon } from "@/components/atlas-icon";
import { cx } from "@/components/ui";
import {
  sponsorStatusTone,
  toneClasses,
  type HealthView,
  type SponsorView,
  type TimelineStep,
} from "@/lib/council";
import { ROSTER_BY_ID } from "@/lib/agents";
import type { CouncilInfluenceReport, LearningReport } from "@/lib/types";
import { PhaseTimeline } from "./phase-timeline";
import { StatusBadge } from "./primitives";

// --------------------------------------------------------------------------- //
// Council status bar — live sponsor/system signals. Holds the #settings anchor.
// --------------------------------------------------------------------------- //
export function CouncilStatusBar({
  councilInfluence,
  healthReady,
  learningReport,
  nowLabel,
  sponsorRows,
}: {
  councilInfluence?: CouncilInfluenceReport;
  healthReady: boolean;
  learningReport?: LearningReport;
  nowLabel: string;
  sponsorRows: SponsorView[];
}) {
  const reduced = useReducedMotion();
  const leader = councilInfluence?.leader;
  const weave = sponsorRows.find((row) => row.id === "weave");
  const redis = sponsorRows.find((row) => row.id === "redis");
  const openai = sponsorRows.find((row) => row.id === "openai");

  return (
    <header
      id="settings"
      className="flex min-h-12 items-center gap-2 overflow-x-auto border-b border-border bg-surface px-4 py-2 lg:px-5"
    >
      <span
        className={cx(
          "inline-flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-semibold",
          healthReady ? toneClasses("positive").soft : toneClasses("warning").soft,
        )}
      >
        <Radio className="h-3.5 w-3.5" strokeWidth={2.25} />
        {healthReady ? "System healthy" : "Preflight checking"}
      </span>
      <SignalChip label="W&B" row={weave} fallback={learningReport?.eval_dataset ?? "Weave"} />
      <SignalChip label="OpenAI" row={openai} fallback={openai?.model ?? "Model"} />
      <SignalChip label="Redis" row={redis} fallback="Memory" />
      <AnimatePresence mode="wait">
        {leader && (
          <motion.span
            key={`${leader.agent_id}-${leader.influence_weight}`}
            initial={reduced ? false : { opacity: 0, x: -8, scale: 0.94 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: 8, scale: 0.96 }}
            transition={{ type: "spring", stiffness: 380, damping: 28 }}
            className="shrink-0"
          >
            <StatusBadge tone="info">
              {ROSTER_BY_ID[leader.agent_id]?.label ?? leader.agent_id} · {leader.influence_weight}% influence
            </StatusBadge>
          </motion.span>
        )}
      </AnimatePresence>
      <span className="ml-auto hidden shrink-0 tabular-nums text-[11px] text-muted-foreground md:block">{nowLabel}</span>
    </header>
  );
}

function SignalChip({ label, row, fallback }: { label: string; row?: SponsorView; fallback: string }) {
  const tone = sponsorStatusTone(row?.status ?? "checking");
  return (
    <span
      className={cx(
        "hidden max-w-[200px] shrink-0 items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-semibold lg:inline-flex",
        toneClasses(tone).soft,
      )}
    >
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current" />
      <span>{label}</span>
      <span className="truncate font-medium opacity-75">{row?.detail || fallback}</span>
    </span>
  );
}

// --------------------------------------------------------------------------- //
// Council header — title, decision under debate, current phase, phase timeline.
// --------------------------------------------------------------------------- //
export const CouncilHeader = memo(CouncilHeaderBase);

function CouncilHeaderBase({
  currentPhase,
  decision,
  healthReady,
  running,
  steps,
}: {
  currentPhase: string;
  decision?: string;
  healthReady: boolean;
  running: boolean;
  steps: TimelineStep[];
}) {
  const liveTone = running ? "info" : healthReady ? "positive" : "warning";
  const liveLabel = running ? "Streaming" : healthReady ? "Live" : "Locked";

  return (
    <section className="border-b border-border bg-surface px-4 py-4 lg:px-6">
      <div className="flex flex-col gap-3.5">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <AtlasIcon name={running ? "council" : healthReady ? "health" : "risk"} size="sm" className="atlas-icon-badge--quiet" />
            <h2 className="font-display text-[23px] font-medium leading-none tracking-[-0.015em]">The Council Chamber</h2>
            <StatusBadge tone={liveTone} pulse={running}>
              {liveLabel}
            </StatusBadge>
            <span className="ml-auto hidden font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-info md:block">
              {currentPhase}
            </span>
          </div>
          {decision ? (
            <p className="headline mt-2.5 line-clamp-2 max-w-[920px] break-words text-[19px] font-medium leading-snug text-foreground lg:text-[21px]">
              {decision}
            </p>
          ) : (
            <p className="mt-2.5 font-serif text-[15px] italic leading-snug text-subtle-foreground">
              No matter before the council — frame a decision below to convene.
            </p>
          )}
          <span className="mt-1.5 block font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-info md:hidden">
            {currentPhase}
          </span>
        </div>

        <div className="min-w-0">
          <div className="gilt-rule mb-3" aria-hidden />
          <PhaseTimeline steps={steps} />
        </div>
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Preflight gate — shown until /api/health is green. Blocks all submissions.
// --------------------------------------------------------------------------- //
export function PreflightPanel({ health, onRefresh }: { health: HealthView; onRefresh: () => void }) {
  const blockers = health.data?.blockers?.length
    ? health.data.blockers
    : health.error
      ? [health.error]
      : ["Awaiting /api/health from the live agent service."];

  return (
    <section className="rounded-lg border border-risk/25 bg-risk-bg px-4 py-3.5 text-risk shadow-sm">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <AtlasIcon name={health.status === "loading" ? "health" : "risk"} size="lg" className="hidden sm:inline-grid" />
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {health.status === "loading" ? (
              <Loader2 className="h-5 w-5 animate-spin" strokeWidth={2.25} />
            ) : (
              <AlertTriangle className="h-5 w-5" strokeWidth={2.25} />
            )}
            <h2 className="text-[15px] font-semibold">
              {health.status === "loading" ? "Strict live preflight is checking" : "Strict live preflight failed"}
            </h2>
          </div>
          <p className="mt-2 max-w-[860px] text-[12px] leading-relaxed text-risk/85">
            Council submissions stay locked until W&B Weave, OpenAI, Redis, CopilotKit, and Cursor readiness all report
            green from the live health endpoint.
          </p>
          <ul className="mt-2.5 grid gap-1.5 text-[12px] leading-relaxed text-risk/90 md:grid-cols-2">
            {blockers.map((blocker) => (
              <li key={blocker} className="flex min-w-0 gap-2">
                <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={2.25} />
                <span className="break-words">{blocker}</span>
              </li>
            ))}
          </ul>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex h-9 shrink-0 items-center justify-center gap-2 rounded-lg border border-risk/25 bg-surface px-4 text-[13px] font-semibold text-risk transition-colors hover:bg-risk-bg"
        >
          {health.refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldAlert className="h-4 w-4" />}
          Recheck preflight
        </button>
      </div>
    </section>
  );
}
