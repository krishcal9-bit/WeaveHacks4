"use client";

import { ArrowDownToLine, Check, XCircle } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { springSnappy } from "@/components/motion/variants";
import { cx } from "@/components/ui";
import {
  buildPhaseSteps,
  timelineLabel,
  timelineTone,
  toneClasses,
  type PhaseStep,
  type TimelineStep,
} from "@/lib/council";
import { SectionLabel } from "./primitives";

function scrollToTarget(id: string) {
  if (typeof document === "undefined") return;
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function PhaseTimeline({ steps }: { steps: TimelineStep[] }) {
  const phases = buildPhaseSteps(steps);
  const active = phases.find((phase) => phase.status === "active");
  const jumpTarget = active ?? [...phases].reverse().find((phase) => phase.status === "complete");

  return (
    <div className="min-w-0">
      <div className="flex items-center justify-between gap-2">
        <SectionLabel>Decision phase</SectionLabel>
        <button
          type="button"
          onClick={() => jumpTarget && scrollToTarget(jumpTarget.target)}
          disabled={!jumpTarget}
          className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-[10px] font-semibold text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground disabled:opacity-40"
        >
          <ArrowDownToLine className="h-3 w-3" strokeWidth={2.25} />
          {active ? "Jump to active" : "Jump to latest"}
        </button>
      </div>
      <ol className="mt-2 flex items-center gap-0 overflow-x-auto pb-1">
        {phases.map((phase, index) => (
          <PhaseNode key={phase.id} step={phase} index={index} last={index === phases.length - 1} onJump={scrollToTarget} />
        ))}
      </ol>
    </div>
  );
}

function PhaseNode({
  step,
  index,
  last,
  onJump,
}: {
  step: PhaseStep;
  index: number;
  last: boolean;
  onJump: (id: string) => void;
}) {
  const reduced = useReducedMotion();
  const tone = toneClasses(timelineTone(step.status));
  const isActive = step.status === "active";
  return (
    <li className="flex min-w-[112px] flex-1 items-center">
      <motion.button
        type="button"
        onClick={() => onJump(step.target)}
        className="group flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1 text-left transition-colors hover:bg-surface-muted"
        animate={isActive && !reduced ? { scale: [1, 1.02, 1] } : { scale: 1 }}
        transition={isActive ? { duration: 1.8, repeat: Infinity, ease: "easeInOut" } : springSnappy}
      >
        <motion.span
          className={cx(
            "grid h-6 w-6 shrink-0 place-items-center rounded-full border text-[11px] font-bold tabular-nums",
            step.status === "complete"
              ? "border-positive bg-positive text-white"
              : step.status === "active"
                ? "border-info bg-info-bg text-info council-live-glow"
                : step.status === "blocked"
                  ? "border-risk bg-risk-bg text-risk"
                  : "border-border-strong bg-surface text-subtle-foreground",
          )}
          initial={step.status === "complete" && !reduced ? { scale: 0.7, opacity: 0 } : false}
          animate={{ scale: 1, opacity: 1 }}
          transition={springSnappy}
        >
          {step.status === "complete" ? (
            <Check className="h-3.5 w-3.5" strokeWidth={3} />
          ) : step.status === "active" ? (
            <motion.span
              className="h-2 w-2 rounded-full bg-info"
              animate={reduced ? undefined : { scale: [1, 1.35, 1], opacity: [1, 0.55, 1] }}
              transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut" }}
            />
          ) : step.status === "blocked" ? (
            <XCircle className="h-3.5 w-3.5" strokeWidth={2.5} />
          ) : (
            index + 1
          )}
        </motion.span>
        <span className="min-w-0">
          <span
            className={cx(
              "block truncate text-[12px] font-semibold leading-tight",
              step.status === "pending" ? "text-muted-foreground" : "text-foreground",
            )}
          >
            {step.label}
          </span>
          <span className={cx("block text-[10px] leading-tight", tone.text)}>{timelineLabel(step.status)}</span>
        </span>
      </motion.button>
      {!last && (
        <span
          aria-hidden="true"
          className={cx(
            "mx-1 h-px min-w-[12px] flex-1 origin-left",
            step.status === "complete" ? "bg-positive/40 council-phase-connector" : "bg-border",
          )}
        />
      )}
    </li>
  );
}
