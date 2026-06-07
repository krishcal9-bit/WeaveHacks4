"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { MessageCircleQuestion } from "lucide-react";
import { EASE_OUT_EXPO, motionDuration, springSnappy } from "@/components/motion/variants";

// Character-by-character "type in" effect. Dependency-free: it slices the target
// string on an interval. State only ever advances inside the interval callback;
// resets happen by remounting (the caller keys this component on `text`), so the
// effect body never calls setState synchronously.
function TypewriterText({ text, speedMs = 22, reduced = false }: { text: string; speedMs?: number; reduced?: boolean }) {
  const [count, setCount] = useState(() => (reduced ? text.length : 0));
  const intervalRef = useRef<number | null>(null);

  useEffect(() => {
    if (reduced || !text) return;

    intervalRef.current = window.setInterval(() => {
      setCount((prev) => {
        const next = prev + 1;
        if (next >= text.length && intervalRef.current !== null) {
          window.clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
        return next;
      });
    }, speedMs);

    return () => {
      if (intervalRef.current !== null) {
        window.clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [text, speedMs, reduced]);

  const typed = text.slice(0, count);
  const done = count >= text.length;

  return (
    <p className="mt-1 break-words text-[18px] font-semibold leading-snug text-foreground sm:text-[20px]">
      <span>{typed}</span>
      {!done && (
        <motion.span
          aria-hidden
          className="ml-0.5 inline-block h-[1.05em] w-[2px] translate-y-[2px] bg-info align-middle"
          animate={reduced ? undefined : { opacity: [1, 0.15, 1] }}
          transition={{ duration: 0.85, repeat: Infinity, ease: EASE_OUT_EXPO }}
        />
      )}
    </p>
  );
}

export function CouncilQuestionBanner({ question }: { question?: string }) {
  const reduced = Boolean(useReducedMotion());
  const trimmed = question?.trim() ?? "";

  return (
    <AnimatePresence initial={false}>
      {trimmed && (
        <motion.div
          key={trimmed}
          data-council-question
          initial={reduced ? { opacity: 0 } : { opacity: 0, y: -8, scale: 0.99 }}
          animate={reduced ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
          exit={reduced ? { opacity: 0 } : { opacity: 0, y: -6, scale: 0.99 }}
          transition={reduced ? { duration: motionDuration.instant } : springSnappy}
          className="relative overflow-hidden rounded-lg border border-info/35 bg-info-bg/60 px-4 py-3 shadow-[0_8px_24px_rgba(18,16,14,0.06)]"
        >
          <span aria-hidden className="absolute inset-y-0 left-0 w-1 bg-info" />
          <div className="flex items-start gap-3 pl-1.5">
            <span className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-info/40 bg-background text-info">
              <MessageCircleQuestion className="h-4 w-4" strokeWidth={2.25} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-info">
                Question to the council
              </div>
              {/* keyed on `trimmed` so each new question remounts and retypes */}
              <TypewriterText key={trimmed} text={trimmed} reduced={reduced} />
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
