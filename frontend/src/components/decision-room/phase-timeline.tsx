"use client";

import { useEffect, useMemo, useRef } from "react";
import { ArrowDownToLine, Check, XCircle } from "lucide-react";
import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from "motion/react";
import { motionDuration, springSnappy, springSoft, transitionFade } from "@/components/motion/variants";
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

function scrollToTarget(id: string, reduced: boolean) {
  if (typeof document === "undefined") return;
  document.getElementById(id)?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
}

function scrollRailToPhase(rail: HTMLOListElement | null, phaseId: string, reduced: boolean) {
  const phaseButton = rail?.querySelector<HTMLElement>(`[data-phase-id="${phaseId}"]`);
  if (!rail || !phaseButton) return;
  const railRect = rail.getBoundingClientRect();
  const phaseRect = phaseButton.getBoundingClientRect();
  const left = rail.scrollLeft + phaseRect.left - railRect.left - rail.clientWidth / 2 + phaseRect.width / 2;
  rail.scrollTo({ left: Math.max(0, left), behavior: reduced ? "auto" : "smooth" });
}

export function PhaseTimeline({ steps }: { steps: TimelineStep[] }) {
  const prefersReducedMotion = useReducedMotion();
  const reduced = Boolean(prefersReducedMotion);
  const railRef = useRef<HTMLOListElement>(null);
  const phases = useMemo(() => buildPhaseSteps(steps), [steps]);
  const active = phases.find((phase) => phase.status === "active");
  const jumpTarget = active ?? [...phases].reverse().find((phase) => phase.status === "complete");
  const activePhaseId = active?.id ?? jumpTarget?.id;

  useEffect(() => {
    if (!activePhaseId) return;
    scrollRailToPhase(railRef.current, activePhaseId, reduced);
  }, [activePhaseId, reduced]);

  function jumpToPhase(phase: PhaseStep) {
    scrollRailToPhase(railRef.current, phase.id, reduced);
    scrollToTarget(phase.target, reduced);
  }

  return (
    <div className="min-w-0" data-testid="phase-timeline">
      <div className="flex items-center justify-between gap-2">
        <SectionLabel>Decision phase</SectionLabel>
        <button
          type="button"
          data-phase-jump-button
          onClick={() => jumpTarget && jumpToPhase(jumpTarget)}
          disabled={!jumpTarget}
          className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-[10px] font-semibold text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground disabled:opacity-40"
        >
          <ArrowDownToLine className="h-3 w-3" strokeWidth={2.25} />
          {active ? "Jump to active" : "Jump to latest"}
        </button>
      </div>
      <LayoutGroup id="phase-timeline">
        <ol ref={railRef} className="room-scroll mt-3 flex items-stretch overflow-x-auto overflow-y-visible px-0.5 pb-1 pt-1">
          {phases.map((phase, index) => (
            <PhaseNode
              key={phase.id}
              step={phase}
              index={index}
              last={index === phases.length - 1}
              reduced={reduced}
              onJump={jumpToPhase}
            />
          ))}
        </ol>
      </LayoutGroup>
    </div>
  );
}

function PhaseNode({
  step,
  index,
  last,
  reduced,
  onJump,
}: {
  step: PhaseStep;
  index: number;
  last: boolean;
  reduced: boolean;
  onJump: (phase: PhaseStep) => void;
}) {
  const tone = toneClasses(timelineTone(step.status));
  const isActive = step.status === "active";
  const isComplete = step.status === "complete";
  const isBlocked = step.status === "blocked";

  return (
    <li className={cx("flex min-w-[188px] items-center", last ? "flex-1" : "flex-[1.15_1_0]")} data-phase-item={step.id}>
      <motion.button
        layout
        type="button"
        onClick={() => onJump(step)}
        data-phase-id={step.id}
        data-phase-label={step.label}
        data-phase-status={step.status}
        aria-current={isActive ? "step" : undefined}
        className={cx(
          "group relative flex h-[74px] min-w-[174px] flex-1 items-center gap-3 rounded-xl border px-3 text-left transition-colors",
          isActive
            ? "border-info/35 bg-info-bg/70 text-info shadow-sm"
            : isComplete
              ? "border-positive/25 bg-positive-bg/45"
              : isBlocked
                ? "border-risk/25 bg-risk-bg/55"
                : "border-transparent bg-transparent hover:border-border hover:bg-surface-muted",
        )}
        initial={false}
        animate={{
          y: isActive && !reduced ? -1 : 0,
          scale: isActive && !reduced ? 1.012 : 1,
        }}
        transition={springSoft}
      >
        {isActive && !reduced && (
          <motion.span
            aria-hidden="true"
            className="council-phase-node-glow absolute inset-0 rounded-lg"
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.98 }}
            transition={springSoft}
          />
        )}
        <motion.span
          layout
          className={cx(
            "relative z-10 grid h-9 w-9 shrink-0 place-items-center rounded-full border text-[12px] font-bold tabular-nums",
            isComplete
              ? "border-positive bg-positive text-white"
              : isActive
                ? "border-info bg-info-bg text-info"
                : isBlocked
                  ? "border-risk bg-risk-bg text-risk"
                  : "border-border-strong bg-surface text-subtle-foreground",
          )}
          animate={{
            boxShadow: isActive
              ? "0 0 0 4px color-mix(in srgb, var(--info) 12%, transparent)"
              : "0 0 0 0 color-mix(in srgb, var(--info) 0%, transparent)",
          }}
          transition={springSoft}
        >
          <AnimatePresence mode="wait" initial={false}>
            <motion.span
              key={step.status}
              className="grid h-full w-full place-items-center"
              initial={reduced ? false : { opacity: 0, scale: 0.72, rotate: isComplete ? -18 : 0 }}
              animate={{ opacity: 1, scale: 1, rotate: 0 }}
              exit={reduced ? undefined : { opacity: 0, scale: 0.72, rotate: isComplete ? 18 : 0 }}
              transition={springSnappy}
            >
              {isComplete ? (
                <Check className="h-4 w-4" strokeWidth={3} />
              ) : isActive ? (
                <motion.span
                  className="h-2.5 w-2.5 rounded-full bg-info"
                  animate={reduced ? undefined : { scale: [1, 1.32, 1], opacity: [1, 0.58, 1] }}
                  transition={{ duration: 1.35, repeat: Infinity, ease: "easeInOut" }}
                />
              ) : isBlocked ? (
                <XCircle className="h-4 w-4" strokeWidth={2.5} />
              ) : (
                index + 1
              )}
            </motion.span>
          </AnimatePresence>
        </motion.span>
        <span className="relative z-10 min-w-0">
          <span
            className={cx(
              "block whitespace-nowrap text-[13px] font-semibold leading-tight",
              step.status === "pending" ? "text-muted-foreground" : "text-foreground",
            )}
          >
            {step.label}
          </span>
          <span className="relative mt-1 block h-[16px] overflow-visible">
            <AnimatePresence mode="wait" initial={false}>
              <motion.span
                key={step.status}
                className={cx("absolute inset-x-0 top-0 whitespace-nowrap text-[11px] leading-[16px]", tone.text)}
                initial={reduced ? false : { opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduced ? undefined : { opacity: 0, y: -4 }}
                transition={transitionFade}
              >
                {timelineLabel(step.status)}
              </motion.span>
            </AnimatePresence>
          </span>
        </span>
      </motion.button>
      {!last && <PhaseConnector status={step.status} reduced={reduced} />}
    </li>
  );
}

function PhaseConnector({ status, reduced }: { status: PhaseStep["status"]; reduced: boolean }) {
  const fillWidth = status === "complete" ? "100%" : status === "active" ? "58%" : status === "blocked" ? "42%" : "0%";
  const fillClass =
    status === "blocked"
      ? "bg-risk"
      : status === "active"
        ? "bg-info"
        : status === "complete"
          ? "bg-positive"
          : "bg-transparent";

  return (
    <span
      aria-hidden="true"
      data-phase-connector-status={status}
      className="relative mx-2 h-[4px] w-12 shrink-0 overflow-hidden rounded-full bg-border sm:w-16 lg:w-20"
    >
      <motion.span
        className={cx("absolute inset-y-0 left-0 rounded-full", fillClass)}
        initial={false}
        animate={{ width: fillWidth }}
        transition={reduced ? { duration: motionDuration.instant } : springSoft}
      />
      {status === "active" && !reduced && <span className="council-phase-connector-flow absolute inset-y-0 left-0 rounded-full" />}
    </span>
  );
}
