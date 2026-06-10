"use client";

import Link from "next/link";
import { memo, type ReactNode } from "react";
import { ArrowUpRight } from "lucide-react";
import { cx } from "@/components/ui";
import { fmtUSD, titleCase, truncate } from "@/lib/format";
import type { CompanyFinancials, DecisionEvent } from "@/lib/types";

/*
  Editorial sections for the Executive Overview: a folio number, a serif title,
  and a hairline — the vocabulary of a section front. Bars are static widths
  (no width animations: they'd trigger layout).
*/

export function Section({
  folio,
  title,
  hint,
  action,
  children,
  className,
}: {
  folio: string;
  title: string;
  hint?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("command-surface flex min-w-0 flex-col p-4 md:p-5", className)}>
      <header className="flex items-baseline gap-2.5">
        <span className="folio text-[11px] font-semibold">{folio}</span>
        <h2 className="font-display text-[17px] font-medium leading-tight tracking-[-0.01em] text-foreground">
          {title}
        </h2>
        {hint && <span className="hidden truncate text-[11px] text-subtle-foreground sm:inline">{hint}</span>}
        {action && <span className="ml-auto shrink-0">{action}</span>}
      </header>
      <div className="rule-hair mt-3" aria-hidden />
      <div className="mt-3 min-w-0 flex-1">{children}</div>
    </section>
  );
}

function BarRow({
  label,
  value,
  max,
  display,
  tone = "neutral",
}: {
  label: string;
  value: number;
  max: number;
  display: string;
  tone?: "neutral" | "info" | "positive";
}) {
  const width = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1">
      <div className="min-w-0">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-[12px] font-medium text-foreground">{label}</span>
        </div>
        <div className="mt-1 h-[5px] w-full overflow-hidden rounded-full bg-surface-muted">
          <span
            className={cx(
              "block h-full rounded-full",
              tone === "info" ? "bg-info/70" : tone === "positive" ? "bg-positive/70" : "bg-border-strong",
            )}
            style={{ width: `${width}%` }}
          />
        </div>
      </div>
      <span className="text-[12px] font-semibold tabular-nums text-muted-foreground">{display}</span>
    </div>
  );
}

function OpexBreakdownBase({ company }: { company: CompanyFinancials }) {
  const opex = (company.opex_monthly ?? {}) as unknown as Record<string, number>;
  const rows = Object.entries(opex)
    .filter(([, value]) => typeof value === "number" && value > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);
  const max = rows[0]?.[1] ?? 0;

  if (rows.length === 0) {
    return <p className="text-[12px] text-muted-foreground">No operating spend recorded yet.</p>;
  }

  return (
    <div className="space-y-3">
      {rows.map(([category, value]) => (
        <BarRow key={category} label={titleCase(category)} value={value} max={max} display={`${fmtUSD(value, { compact: true })}/mo`} />
      ))}
    </div>
  );
}

export const OpexBreakdown = memo(OpexBreakdownBase);

function PipelinePanelBase({ company }: { company: CompanyFinancials }) {
  const stages = (company.pipeline_by_stage ?? []) as Array<{
    stage: string;
    opportunities?: number;
    arr?: number;
    weighted_arr?: number;
  }>;
  const max = Math.max(...stages.map((s) => s.weighted_arr ?? 0), 0);

  if (stages.length === 0) {
    return <p className="text-[12px] text-muted-foreground">No pipeline data uploaded yet.</p>;
  }

  return (
    <div className="space-y-3">
      {stages.map((stage) => (
        <BarRow
          key={stage.stage}
          label={`${stage.stage}${stage.opportunities ? ` · ${stage.opportunities}` : ""}`}
          value={stage.weighted_arr ?? 0}
          max={max}
          display={fmtUSD(stage.weighted_arr, { compact: true })}
          tone="info"
        />
      ))}
    </div>
  );
}

export const PipelinePanel = memo(PipelinePanelBase);

function decisionTone(decision?: string): string {
  const value = String(decision ?? "").toUpperCase();
  if (value.includes("APPROVE") || value.includes("YES")) return "border-positive/30 bg-positive-bg text-positive";
  if (value.includes("REJECT") || value.includes("NO")) return "border-risk/30 bg-risk-bg text-risk";
  if (value.includes("CONDITIONAL")) return "border-warning/30 bg-warning-bg text-warning";
  return "border-info/30 bg-info-bg text-info";
}

function RecentRulingsBase({ decisions }: { decisions: DecisionEvent[] }) {
  if (decisions.length === 0) {
    return (
      <div className="flex h-full flex-col items-start justify-center gap-2 py-4">
        <p className="text-[12px] leading-relaxed text-muted-foreground">
          The council hasn&apos;t ruled yet. Convene it on a real decision and the ruling lands here.
        </p>
        <Link
          href="/decisions"
          className="inline-flex items-center gap-1 text-[12px] font-semibold text-accent transition-opacity hover:opacity-80"
        >
          Open the Council Chamber <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={2.25} />
        </Link>
      </div>
    );
  }

  return (
    <ol className="space-y-3">
      {decisions.slice(0, 6).map((event) => (
        <li key={event._id} className="group min-w-0">
          <Link href="/activity" className="block min-w-0">
            <div className="flex items-start justify-between gap-3">
              <p className="min-w-0 flex-1 font-serif text-[13.5px] font-medium leading-snug text-foreground transition-colors group-hover:text-accent">
                {truncate(event.title, 96)}
              </p>
              <span
                className={cx(
                  "mt-0.5 shrink-0 rounded-md border px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-[0.08em]",
                  decisionTone(event.decision),
                )}
              >
                {String(event.answer_label ?? event.decision ?? "ruling").slice(0, 22)}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-2 text-[10.5px] text-subtle-foreground">
              {typeof event.confidence === "number" && <span className="tabular-nums">{event.confidence}% confidence</span>}
              {event.decision_type ? <span className="truncate">{titleCase(String(event.decision_type))}</span> : null}
            </div>
          </Link>
        </li>
      ))}
    </ol>
  );
}

export const RecentRulings = memo(RecentRulingsBase);
