"use client";

import { memo } from "react";
import { MessageCircleQuestion } from "lucide-react";

// Static, animation-free banner: the question is pasted in immediately (no
// typewriter effect, no enter/exit motion) to keep the Decision Room responsive
// during a live council run.
function CouncilQuestionBannerBase({ question }: { question?: string }) {
  const trimmed = question?.trim() ?? "";
  if (!trimmed) return null;

  return (
    <div
      data-council-question
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
          <p className="mt-1 break-words text-[18px] font-semibold leading-snug text-foreground sm:text-[20px]">
            {trimmed}
          </p>
        </div>
      </div>
    </div>
  );
}

export const CouncilQuestionBanner = memo(CouncilQuestionBannerBase);
