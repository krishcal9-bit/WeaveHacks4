"use client";

import { memo, useMemo } from "react";
import { Check, XCircle } from "lucide-react";
import { cx } from "@/components/ui";
import { buildPhaseSteps, type PhaseStep, type TimelineStep } from "@/lib/council";
import { SectionLabel } from "./primitives";

// Static, animation-free stepper. Each phase shows a checkmark once it is
// finished, a number while pending/active, or an X if blocked — no continuously
// updating fill bar, no motion, no auto-scroll. This keeps the progress bar
// cheap during a live council run (it re-renders only when a phase's status
// changes, and renders no animations).
function PhaseTimelineBase({ steps }: { steps: TimelineStep[] }) {
  const phases = useMemo(() => buildPhaseSteps(steps), [steps]);

  return (
    <div className="min-w-0" data-testid="phase-timeline">
      <SectionLabel>Decision phase</SectionLabel>
      <ol className="room-scroll mt-3 flex items-stretch overflow-x-auto overflow-y-visible px-0.5 pb-1 pt-1">
        {phases.map((phase, index) => (
          <PhaseNode key={phase.id} step={phase} index={index} last={index === phases.length - 1} />
        ))}
      </ol>
    </div>
  );
}

export const PhaseTimeline = memo(PhaseTimelineBase);

const PhaseNode = memo(function PhaseNode({
  step,
  index,
  last,
}: {
  step: PhaseStep;
  index: number;
  last: boolean;
}) {
  const isActive = step.status === "active";
  const isComplete = step.status === "complete";
  const isBlocked = step.status === "blocked";

  return (
    <li className={cx("flex min-w-[150px] items-center", last ? "flex-1" : "flex-[1.15_1_0]")} data-phase-item={step.id}>
      <div
        data-phase-id={step.id}
        data-phase-label={step.label}
        data-phase-status={step.status}
        aria-current={isActive ? "step" : undefined}
        className={cx(
          "relative flex h-[64px] min-w-[150px] flex-1 items-center gap-3 rounded-xl border px-3 text-left",
          isActive
            ? "border-info/35 bg-info-bg/70"
            : isComplete
              ? "border-positive/25 bg-positive-bg/45"
              : isBlocked
                ? "border-risk/25 bg-risk-bg/55"
                : "border-transparent bg-transparent",
        )}
      >
        <span
          className={cx(
            "grid h-9 w-9 shrink-0 place-items-center rounded-full border text-[12px] font-bold tabular-nums",
            isComplete
              ? "border-positive bg-positive text-white"
              : isActive
                ? "border-info bg-info-bg text-info"
                : isBlocked
                  ? "border-risk bg-risk-bg text-risk"
                  : "border-border-strong bg-surface text-subtle-foreground",
          )}
        >
          {isComplete ? (
            <Check className="h-4 w-4" strokeWidth={3} />
          ) : isBlocked ? (
            <XCircle className="h-4 w-4" strokeWidth={2.5} />
          ) : (
            index + 1
          )}
        </span>
        <span className="min-w-0">
          <span
            className={cx(
              "block whitespace-nowrap text-[13px] font-semibold leading-tight",
              step.status === "pending" ? "text-muted-foreground" : "text-foreground",
            )}
          >
            {step.label}
          </span>
          <span
            className={cx(
              "mt-0.5 block whitespace-nowrap text-[11px] leading-[16px]",
              isComplete
                ? "text-positive"
                : isActive
                  ? "text-info"
                  : isBlocked
                    ? "text-risk"
                    : "text-subtle-foreground",
            )}
          >
            {isComplete ? "Done" : isActive ? "In progress" : isBlocked ? "Blocked" : "Pending"}
          </span>
        </span>
      </div>
      {!last && (
        <span
          aria-hidden="true"
          data-phase-connector-status={step.status}
          className={cx(
            "mx-2 h-[3px] w-8 shrink-0 rounded-full sm:w-12",
            isComplete ? "bg-positive" : isBlocked ? "bg-risk" : "bg-border",
          )}
        />
      )}
    </li>
  );
});
