"use client";

import { ArrowUpRight, Compass } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { cx } from "@/components/ui";
import { motionDuration, springSnappy, staggerDelay } from "@/components/motion/variants";
import { Panel } from "./primitives";

// Realistic Northwind Robotics finance cases — a mix of closed-ended (approve/
// reject) and open-ended (strategy) prompts. Clicking a chip FILLS the prompt box
// (it never auto-runs); the operator then hits Run in the console below.
const COUNCIL_CASES: { label: string; prompt: string }[] = [
  { label: "Renew Datadog?", prompt: "Should we renew the Datadog observability contract at its current $240K/yr?" },
  { label: "Series B bridge?", prompt: "Should we approve a $2M Series B bridge round to extend our runway?" },
  { label: "Extend runway 6mo", prompt: "How should we extend Northwind Robotics' runway by 6 months?" },
  { label: "Cut cloud spend", prompt: "Where can we cut cloud spend without slowing product growth?" },
  { label: "Consolidate vendors", prompt: "Should we consolidate our observability vendors onto a single platform?" },
];

export function SteerCouncil({
  onUse,
  running,
  healthReady,
}: {
  onUse: (prompt: string) => void;
  running: boolean;
  healthReady: boolean;
}) {
  const reduced = Boolean(useReducedMotion());
  const disabled = running || !healthReady;
  const hint = !healthReady
    ? "Available once preflight passes."
    : running
      ? "The council is deliberating — try a new case once it concludes."
      : "Pick a case to load the prompt, then press Run below.";

  return (
    <Panel
      title="Steer the council"
      eyebrow="Council case"
      icon={Compass}
      className="shrink-0"
      bodyClassName="space-y-2.5"
    >
      <p className="text-[12px] leading-relaxed text-muted-foreground">{hint}</p>
      <div className="flex flex-wrap gap-1.5">
        {COUNCIL_CASES.map((council, index) => (
          <motion.button
            key={council.label}
            type="button"
            disabled={disabled}
            title={council.prompt}
            aria-label={`Load prompt: ${council.prompt}`}
            onClick={() => onUse(council.prompt)}
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={reduced ? { duration: motionDuration.instant } : { ...springSnappy, delay: staggerDelay(index, 0.035, 0.18) }}
            className={cx(
              "group inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1 text-[12px] font-medium text-muted-foreground transition-colors",
              disabled
                ? "cursor-not-allowed opacity-50"
                : "hover:border-border-strong hover:bg-surface-muted hover:text-foreground",
            )}
          >
            <span className="truncate">{council.label}</span>
            <ArrowUpRight
              className={cx("h-3 w-3 shrink-0 text-subtle-foreground transition-colors", !disabled && "group-hover:text-foreground")}
              strokeWidth={2.25}
            />
          </motion.button>
        ))}
      </div>
    </Panel>
  );
}
