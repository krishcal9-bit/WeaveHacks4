"use client";

import { Gavel } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { springSnappy } from "@/components/motion/variants";
import { cx } from "@/components/ui";
import { cfoRulingFlourish, decisionTone, splitCfoRationale, toneClasses } from "@/lib/council";

export function CfoRulingCard({
  decision,
  confidence,
  rationale,
  keyPoints,
  variant = "memo",
}: {
  decision: string;
  confidence?: number;
  rationale?: string;
  keyPoints?: string[];
  variant?: "memo" | "transcript";
}) {
  const reduced = useReducedMotion();
  const tone = decisionTone(decision);
  const colors = toneClasses(tone);
  const { kicker, flourish } = cfoRulingFlourish(decision);
  const { lead, rest } = splitCfoRationale(rationale ?? "");
  const isMemo = variant === "memo";

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
      initial={reduced ? false : { opacity: 0, y: isMemo ? 14 : 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
      data-cfo-tone={tone}
    >
      <div
        className={cx(
          "pointer-events-none absolute inset-x-0 top-0 h-1",
          tone === "positive" && "bg-positive",
          tone === "risk" && "bg-risk",
          tone === "warning" && "bg-warning",
          tone === "info" && "bg-info",
        )}
        aria-hidden
      />

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="cfo-ruling-kicker">{kicker}</p>
          <p className={cx("mt-1 font-display text-[12px] italic leading-snug", colors.text)}>{flourish}</p>
        </div>
        <span className={cx("cfo-ruling-badge inline-flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-1", colors.soft)}>
          <Gavel className="h-3.5 w-3.5" strokeWidth={2.25} />
          <span className="text-[10px] font-bold uppercase tracking-[0.14em]">Final ruling</span>
        </span>
      </div>

      <div className="rule-strong mt-3" />

      <div className="mt-3 flex flex-wrap items-end gap-x-3 gap-y-2">
        <motion.p
          className={cx("cfo-verdict font-display font-semibold tracking-[-0.02em]", colors.text, isMemo ? "text-[clamp(1.75rem,4vw,2.35rem)]" : "text-[clamp(1.45rem,3.5vw,1.9rem)]")}
          initial={reduced ? false : { scale: 0.94, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={springSnappy}
        >
          {decision}
        </motion.p>
        {typeof confidence === "number" && (
          <p className={cx("pb-1 font-display text-[13px] italic tabular-nums", colors.text)}>
            at <span className="font-semibold not-italic underline decoration-2 underline-offset-4">{confidence}%</span> confidence
          </p>
        )}
      </div>

      {(lead || rest) && (
        <div className={cx("mt-3 space-y-2", isMemo ? "text-[15px]" : "text-[13px]")}>
          {lead && (
            <p className={cx("cfo-ruling-lead font-display leading-snug", colors.text)}>
              <span className="italic underline decoration-2 underline-offset-[5px]">{lead}</span>
            </p>
          )}
          {rest && <p className="cfo-ruling-body dropcap leading-relaxed text-foreground">{rest}</p>}
        </div>
      )}

      {keyPoints && keyPoints.length > 0 && (
        <div className="mt-3 rounded-md border border-border/80 bg-background/70 p-2.5">
          <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-subtle-foreground">Guardrails &amp; conditions</p>
          <ul className="mt-2 space-y-1.5">
            {keyPoints.map((point) => (
              <li key={point} className="flex gap-2 text-[12px] leading-relaxed text-foreground">
                <span className={cx("mt-2 h-1.5 w-1.5 shrink-0 rounded-full", colors.dot)} />
                <span className="break-words italic">{point}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </motion.div>
  );
}
