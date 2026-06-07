"use client";

import type { ReactNode } from "react";
import {
  ClipboardCheck,
  Gavel,
  GitBranch,
  ListChecks,
  Scale,
  ShieldCheck,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import {
  EASE_OUT_EXPO,
  motionDuration,
  springSnappy,
  transitionEmphasis,
  transitionReduced,
  transitionReveal,
} from "@/components/motion/variants";
import { cx } from "@/components/ui";
import { cfoRulingFlourish, decisionTone, splitCfoRationale, toneClasses } from "@/lib/council";
import type { CfoAnalystInfluence, OperatorAction, QuestionKind } from "@/lib/types";

export function CfoRulingCard({
  decision,
  confidence,
  ruling,
  rationale,
  tradeoffs,
  analystInfluence,
  conditions,
  dissent,
  runwayImpactSummary,
  operatorActions,
  keyPoints,
  questionKind,
  isVerdict,
  headline,
  answerLabel,
  recommendedActions,
  selectedOptions,
  variant = "memo",
}: {
  decision: string;
  confidence?: number;
  ruling?: string;
  rationale?: string;
  tradeoffs?: string[];
  analystInfluence?: CfoAnalystInfluence[];
  conditions?: string[];
  dissent?: string;
  runwayImpactSummary?: string;
  operatorActions?: OperatorAction[];
  keyPoints?: string[];
  questionKind?: QuestionKind | string;
  isVerdict?: boolean;
  headline?: string;
  answerLabel?: string;
  recommendedActions?: string[];
  selectedOptions?: string[];
  variant?: "memo" | "transcript";
}) {
  const reduced = useReducedMotion();
  const shouldReduce = reduced ?? false;
  // A result is a VERDICT only when explicitly flagged. Open-ended recommendations
  // and multiple-choice selections (isVerdict === false) render neutrally and lead
  // with the human headline, never the raw RECOMMENDATION / SELECTION token.
  const nonVerdict = isVerdict === false;
  const tone = nonVerdict ? "info" : decisionTone(decision);
  const colors = toneClasses(tone);
  const { kicker, flourish } = cfoRulingFlourish(decision);
  const marquee = nonVerdict ? headline?.trim() || decision : decision;
  const badgeLabel = nonVerdict ? answerLabel || "Recommendation" : "Final ruling";
  const isMultipleChoice = questionKind === "multiple_choice";
  const actionItems = recommendedActions?.filter((item) => item && item.trim()) ?? [];
  const optionItems = selectedOptions?.filter((item) => item && item.trim()) ?? [];
  const { lead, rest } = splitCfoRationale(rationale ?? "");
  const isMemo = variant === "memo";
  const chairRuling = ruling || lead;
  const rationaleBody = ruling ? [lead, rest].filter(Boolean).join(" ") : rest;
  const guardrails = conditions?.length ? conditions : keyPoints;
  const influence = analystInfluence?.filter((item) => item.role && typeof item.influence_weight === "number") ?? [];
  const actions = operatorActions?.filter((item) => item.action) ?? [];
  const reveal = (index: number) => (shouldReduce ? 0 : index * (isMemo ? 0.055 : 0.035));

  return (
    <motion.div
      className={cx(
        "cfo-ruling-card relative overflow-hidden rounded-lg border-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.35)]",
        colors.border,
        tone === "positive" && "bg-positive-bg/45",
        tone === "risk" && "bg-risk-bg/50",
        tone === "warning" && "bg-warning-bg/45",
        tone === "info" && "bg-info-bg/35",
        isMemo ? "p-4 sm:p-5" : "p-3.5 sm:p-4",
      )}
      initial={shouldReduce ? false : { opacity: 0, y: isMemo ? 14 : 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={transitionReveal}
      data-cfo-tone={tone}
      data-cfo-ruling-card="true"
    >
      <motion.div
        className={cx(
          "pointer-events-none absolute inset-x-0 top-0 h-1",
          tone === "positive" && "bg-positive",
          tone === "risk" && "bg-risk",
          tone === "warning" && "bg-warning",
          tone === "info" && "bg-info",
        )}
        initial={shouldReduce ? false : { scaleX: 0, originX: 0 }}
        animate={{ scaleX: 1 }}
        transition={{ ...transitionEmphasis, delay: reveal(1) }}
        aria-hidden
      />

      <StagedBlock delay={reveal(0)} reduced={shouldReduce} className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="cfo-ruling-kicker">{kicker}</p>
          <p className={cx("mt-1 font-display text-[12px] italic leading-snug", colors.text)}>{flourish}</p>
        </div>
        <span className={cx("cfo-ruling-badge inline-flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-1", colors.soft)}>
          <Gavel className="h-3.5 w-3.5" strokeWidth={2.25} />
          <span className="text-[10px] font-bold uppercase">{badgeLabel}</span>
        </span>
      </StagedBlock>

      <motion.div
        className="rule-strong mt-3"
        initial={shouldReduce ? false : { scaleX: 0, originX: 0 }}
        animate={{ scaleX: 1 }}
        transition={{ ...transitionReveal, delay: reveal(1.4) }}
      />

      <StagedBlock delay={reveal(2)} reduced={shouldReduce} className="mt-3 flex flex-wrap items-end gap-x-3 gap-y-2">
        <motion.p
          className={cx(
            "cfo-verdict min-w-0 break-words font-display font-semibold",
            colors.text,
            nonVerdict
              ? isMemo
                ? "text-[clamp(1.25rem,2.6vw,1.6rem)] leading-tight"
                : "text-[clamp(1.1rem,2.4vw,1.4rem)] leading-tight"
              : isMemo
                ? "text-[clamp(1.75rem,4vw,2.35rem)]"
                : "text-[clamp(1.45rem,3.5vw,1.9rem)]",
          )}
          initial={shouldReduce ? false : { scale: 0.94, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={springSnappy}
        >
          {marquee}
        </motion.p>
        {typeof confidence === "number" && (
          <p className={cx("pb-1 font-display text-[13px] italic tabular-nums", colors.text)}>
            at <span className="font-semibold not-italic underline decoration-2 underline-offset-4">{confidence}%</span> confidence
          </p>
        )}
      </StagedBlock>

      {nonVerdict && isMultipleChoice && optionItems.length > 0 && (
        <StagedBlock delay={reveal(2.6)} reduced={shouldReduce} className="mt-3 rounded-md border border-border bg-background/80 p-2.5">
          <p className="text-[10px] font-bold uppercase text-subtle-foreground">Selected option(s)</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {optionItems.map((option) => (
              <span
                key={option}
                className={cx("inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-[12px] font-semibold", colors.soft)}
              >
                <span className="break-words">{option}</span>
              </span>
            ))}
          </div>
        </StagedBlock>
      )}

      {typeof confidence === "number" && (
        <StagedBlock delay={reveal(3)} reduced={shouldReduce} className="mt-3">
          <div className="rounded-md border border-border bg-background/70 p-2.5">
            <div className="flex items-center justify-between gap-3">
              <p className="text-[10px] font-bold uppercase text-subtle-foreground">Confidence calibration</p>
              <p className={cx("text-[13px] font-semibold tabular-nums", colors.text)}>{confidence}%</p>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-muted">
              <motion.div
                className={cx(
                  "h-full rounded-full",
                  tone === "positive" && "bg-positive",
                  tone === "risk" && "bg-risk",
                  tone === "warning" && "bg-warning",
                  tone === "info" && "bg-info",
                )}
                initial={false}
                animate={{ width: `${Math.max(0, Math.min(100, confidence))}%` }}
                transition={shouldReduce ? transitionReduced : { ...transitionEmphasis, delay: reveal(3.3) }}
              />
            </div>
          </div>
        </StagedBlock>
      )}

      {chairRuling && (
        <StagedBlock delay={reveal(4)} reduced={shouldReduce} className="mt-3 rounded-md border border-border bg-background/80 p-2.5">
          <p className="text-[10px] font-bold uppercase text-subtle-foreground">Chair ruling</p>
          <p className={cx("mt-1 break-words font-display leading-snug", colors.text, isMemo ? "text-[16px]" : "text-[14px]")}>
            {chairRuling}
          </p>
        </StagedBlock>
      )}

      {runwayImpactSummary && (
        <StagedBlock delay={reveal(5)} reduced={shouldReduce} className="mt-2.5 flex min-w-0 items-start gap-2 rounded-md border border-border bg-background/70 p-2.5">
          <TrendingUp className={cx("mt-0.5 h-4 w-4 shrink-0", colors.text)} strokeWidth={2.25} />
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase text-subtle-foreground">Runway impact</p>
            <p className="mt-0.5 break-words text-[12px] font-semibold tabular-nums text-foreground">{runwayImpactSummary}</p>
          </div>
        </StagedBlock>
      )}

      {rationaleBody && (
        <StagedBlock delay={reveal(6)} reduced={shouldReduce} className={cx("mt-3 space-y-2", isMemo ? "text-[15px]" : "text-[13px]")}>
          <p className="cfo-ruling-body leading-relaxed text-foreground">{rationaleBody}</p>
        </StagedBlock>
      )}

      {nonVerdict && actionItems.length > 0 && (
        <StagedBlock delay={reveal(6.5)} reduced={shouldReduce}>
          <CfoOrderedPanel
            icon={ListChecks}
            title="Recommended course of action"
            items={actionItems}
            toneClass={colors.text}
            className="mt-3"
          />
        </StagedBlock>
      )}

      {(tradeoffs?.length || influence.length > 0) && (
        <StagedBlock delay={reveal(7)} reduced={shouldReduce} className="mt-3 grid gap-2 sm:grid-cols-2">
          {tradeoffs && tradeoffs.length > 0 && (
            <CfoMiniPanel icon={Scale} title="Tradeoffs" items={tradeoffs} dotClass={colors.dot} />
          )}
          {influence.length > 0 && (
            <div className="rounded-md border border-border/80 bg-background/70 p-2.5">
              <div className="flex items-center gap-1.5">
                <GitBranch className={cx("h-3.5 w-3.5", colors.text)} strokeWidth={2.25} />
                <p className="text-[10px] font-bold uppercase text-subtle-foreground">Influence weighed</p>
              </div>
              <ul className="mt-2 space-y-1.5">
                {influence.slice(0, 4).map((item) => (
                  <li key={item.role} className="text-[12px] leading-relaxed text-foreground">
                    <span className="font-semibold tabular-nums">{item.role} {item.influence_weight}%:</span>{" "}
                    <span className="break-words text-muted-foreground">{item.effect_on_ruling}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </StagedBlock>
      )}

      {guardrails && guardrails.length > 0 && (
        <StagedBlock delay={reveal(8)} reduced={shouldReduce}>
          <CfoMiniPanel
            icon={ShieldCheck}
            title="Conditions"
            items={guardrails}
            dotClass={colors.dot}
            className="mt-3"
          />
        </StagedBlock>
      )}

      {dissent && (
        <StagedBlock delay={reveal(9)} reduced={shouldReduce} className="mt-3 rounded-md border border-border/80 bg-background/70 p-2.5">
          <p className="text-[10px] font-bold uppercase text-subtle-foreground">Dissent resolved</p>
          <p className="mt-1 break-words text-[12px] leading-relaxed text-foreground">{dissent}</p>
        </StagedBlock>
      )}

      {actions.length > 0 && (
        <StagedBlock delay={reveal(10)} reduced={shouldReduce}>
          <CfoActionPanel actions={actions} dotClass={colors.dot} />
        </StagedBlock>
      )}
    </motion.div>
  );
}

function CfoMiniPanel({
  icon: Icon,
  title,
  items,
  dotClass,
  className,
}: {
  icon: LucideIcon;
  title: string;
  items: string[];
  dotClass: string;
  className?: string;
}) {
  return (
    <div className={cx("rounded-md border border-border/80 bg-background/70 p-2.5", className)}>
      <div className="flex items-center gap-1.5">
        <Icon className="h-3.5 w-3.5 text-muted-foreground" strokeWidth={2.25} />
        <p className="text-[10px] font-bold uppercase text-subtle-foreground">{title}</p>
      </div>
      <ul className="mt-2 space-y-1.5">
        {items.map((point) => (
          <li key={point} className="flex gap-2 text-[12px] leading-relaxed text-foreground">
            <span className={cx("mt-2 h-1.5 w-1.5 shrink-0 rounded-full", dotClass)} />
            <span className="break-words">{point}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// Prioritized, numbered course-of-action steps — the primary answer body for
// open-ended recommendations. Reuses the card's mini-panel surface styling.
function CfoOrderedPanel({
  icon: Icon,
  title,
  items,
  toneClass,
  className,
}: {
  icon: LucideIcon;
  title: string;
  items: string[];
  toneClass: string;
  className?: string;
}) {
  return (
    <div className={cx("rounded-md border border-border/80 bg-background/75 p-2.5", className)}>
      <div className="flex items-center gap-1.5">
        <Icon className={cx("h-3.5 w-3.5", toneClass)} strokeWidth={2.25} />
        <p className="text-[10px] font-bold uppercase text-subtle-foreground">{title}</p>
      </div>
      <ol className="mt-2 space-y-1.5">
        {items.slice(0, 6).map((item, index) => (
          <li key={`${item}-${index}`} className="flex gap-2 text-[12px] leading-relaxed text-foreground">
            <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border border-border bg-surface text-[10px] font-semibold tabular-nums text-muted-foreground">
              {index + 1}
            </span>
            <span className="min-w-0 break-words">{item}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function CfoActionPanel({ actions, dotClass }: { actions: OperatorAction[]; dotClass: string }) {
  return (
    <div className="mt-3 rounded-md border border-border/80 bg-background/75 p-2.5">
      <div className="flex items-center gap-1.5">
        <ClipboardCheck className="h-3.5 w-3.5 text-muted-foreground" strokeWidth={2.25} />
        <p className="text-[10px] font-bold uppercase text-subtle-foreground">Operator actions</p>
      </div>
      <ul className="mt-2 space-y-1.5">
        {actions.slice(0, 4).map((item, index) => (
          <li key={`${item.action}-${index}`} className="flex gap-2 text-[12px] leading-relaxed text-foreground">
            <span className={cx("mt-2 h-1.5 w-1.5 shrink-0 rounded-full", dotClass)} />
            <span className="min-w-0">
              <span className="break-words">{item.action}</span>
              {(item.owner || item.due || item.priority) && (
                <span className="mt-0.5 block text-[10.5px] font-medium text-muted-foreground">
                  {[item.owner, item.due, item.priority].filter(Boolean).join(" · ")}
                </span>
              )}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function StagedBlock({
  children,
  delay,
  reduced,
  className,
}: {
  children: ReactNode;
  delay: number;
  reduced: boolean;
  className?: string;
}) {
  return (
    <motion.div
      className={className}
      initial={reduced ? false : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: motionDuration.reveal, delay, ease: EASE_OUT_EXPO }}
    >
      {children}
    </motion.div>
  );
}
