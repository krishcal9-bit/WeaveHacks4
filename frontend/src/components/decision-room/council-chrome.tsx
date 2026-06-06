"use client";

import { AlertTriangle, Loader2, Radio, ShieldAlert, XCircle } from "lucide-react";
import { cx } from "@/components/ui";
import {
  averageReliability,
  getHealthLabel,
  NODE_LABEL,
  sponsorStatusTone,
  toneClasses,
  type HealthView,
  type SponsorView,
  type TimelineStep,
} from "@/lib/council";
import type { LearningReport, ReliabilityScore } from "@/lib/types";
import { PhaseTimeline } from "./phase-timeline";
import { SectionLabel, StatusBadge } from "./primitives";

// --------------------------------------------------------------------------- //
// Council status bar — live sponsor/system signals. Holds the #settings anchor.
// --------------------------------------------------------------------------- //
export function CouncilStatusBar({
  healthReady,
  learningReport,
  nowLabel,
  reliabilityScores,
  sponsorRows,
}: {
  healthReady: boolean;
  learningReport?: LearningReport;
  nowLabel: string;
  reliabilityScores: ReliabilityScore[];
  sponsorRows: SponsorView[];
}) {
  const avgReliability = averageReliability(reliabilityScores);
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
      <StatusBadge tone={avgReliability ? "positive" : "warning"} className="shrink-0">
        Reliability {avgReliability ? `${avgReliability}%` : "pending"}
      </StatusBadge>
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
export function CouncilHeader({
  currentPhase,
  decision,
  health,
  healthReady,
  nodeName,
  running,
  steps,
}: {
  currentPhase: string;
  decision?: string;
  health: HealthView;
  healthReady: boolean;
  nodeName?: string;
  running: boolean;
  steps: TimelineStep[];
}) {
  const liveTone = running ? "info" : healthReady ? "positive" : "warning";
  const liveLabel = running ? "Streaming" : healthReady ? "Live" : "Locked";

  return (
    <section className="border-b border-border bg-surface px-4 py-3 lg:px-5">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(300px,440px)] lg:items-center">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="text-[20px] font-semibold tracking-tight">AI Council Room</h2>
            <StatusBadge tone={liveTone} pulse={running}>
              {liveLabel}
            </StatusBadge>
          </div>
          <div className="mt-1.5">
            <SectionLabel>Decision under debate</SectionLabel>
            <p className="mt-0.5 line-clamp-2 break-words text-[14px] font-semibold leading-snug">
              {decision || "Awaiting a live decision command"}
            </p>
          </div>
          <div className="mt-1 text-[12px] text-muted-foreground">
            <span className="font-semibold text-info">{currentPhase}</span>
            <span className="mx-1.5 text-border-strong">·</span>
            <span>{running ? NODE_LABEL[nodeName ?? ""] ?? "Streaming" : getHealthLabel(health)}</span>
          </div>
        </div>

        <div className="min-w-0 lg:border-l lg:border-border lg:pl-4">
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
