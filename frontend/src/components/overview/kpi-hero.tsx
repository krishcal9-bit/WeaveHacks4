"use client";

import { memo } from "react";
import { cx } from "@/components/ui";
import { fmtDate, fmtPct, fmtUSD } from "@/lib/format";
import { useCountUp } from "@/lib/use-count-up";
import type { CompanyFinancials } from "@/lib/types";

/*
  The masthead stat strip of the Executive Overview: four serif numerals over
  hairline rules, like the front page of a financial paper. Numerals count up
  once when data lands (event-driven rAF), then sit still.
*/

function Figure({
  label,
  value,
  format,
  hint,
  tone,
}: {
  label: string;
  value: number | null | undefined;
  format: (n: number) => string;
  hint?: string;
  tone?: "positive" | "risk" | "warning";
}) {
  const display = useCountUp(value ?? null);
  return (
    <div className="min-w-0 border-l border-border pl-4 first:border-l-0 first:pl-0">
      <div className="font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-subtle-foreground">
        {label}
      </div>
      <div
        className={cx(
          "figure-display mt-1.5 truncate text-[30px] font-medium leading-none sm:text-[36px]",
          tone === "positive" ? "text-positive" : tone === "risk" ? "text-risk" : tone === "warning" ? "text-warning" : "text-foreground",
        )}
      >
        {display == null ? "—" : format(display)}
      </div>
      {hint && <div className="mt-1.5 truncate text-[11px] text-muted-foreground">{hint}</div>}
    </div>
  );
}

function KpiHeroBase({ company }: { company: CompanyFinancials }) {
  const runway = company.runway_months;
  const runwayTone = runway != null ? (runway < 9 ? "risk" : runway < 14 ? "warning" : "positive") : undefined;
  const growth = company.mrr_growth_mom;

  return (
    <section className="command-surface command-surface--feature relative overflow-hidden px-5 py-5 md:px-7 md:py-6">
      <div
        aria-hidden
        className="pointer-events-none absolute -right-32 -top-44 h-[360px] w-[520px]"
        style={{
          background: "radial-gradient(closest-side, color-mix(in srgb, var(--gilt) 8%, transparent), transparent 72%)",
        }}
      />

      <div className="relative flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="kicker kicker--gilt">Executive Overview</span>
        <span className="ml-auto font-mono text-[10px] uppercase tracking-[0.14em] text-subtle-foreground">
          As of {fmtDate(company.updated) ?? "today"}
        </span>
      </div>

      <div className="relative mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="headline text-[30px] font-medium text-foreground sm:text-[36px]">{company.name}</h1>
        <span className="rounded-md border border-border bg-background px-2 py-0.5 text-[11px] font-semibold text-muted-foreground">
          {company.stage}
        </span>
        {typeof company.headcount === "number" && (
          <span className="text-[12px] text-subtle-foreground">{company.headcount} people</span>
        )}
      </div>

      <div className="gilt-rule relative mt-4" aria-hidden />

      <div className="relative mt-4 grid grid-cols-2 gap-x-4 gap-y-5 lg:grid-cols-4">
        <Figure
          label="Cash on hand"
          value={company.cash_on_hand}
          format={(n) => fmtUSD(n, { compact: true })}
          hint={`Gross burn ${fmtUSD(company.monthly_gross_burn, { compact: true })}/mo`}
        />
        <Figure
          label="Runway"
          value={company.runway_months}
          format={(n) => `${n.toFixed(1)} mo`}
          tone={runwayTone}
          hint={runway != null && runway < 12 ? "Below 12-month comfort line" : "Computed from live ledger"}
        />
        <Figure
          label="Net burn / mo"
          value={company.monthly_net_burn}
          format={(n) => fmtUSD(n, { compact: true })}
          hint={`Revenue ${fmtUSD(company.monthly_revenue, { compact: true })}/mo`}
        />
        <Figure
          label="ARR"
          value={company.arr}
          format={(n) => fmtUSD(n, { compact: true })}
          tone={growth != null && growth > 0 ? "positive" : undefined}
          hint={
            growth != null
              ? `${growth >= 0 ? "+" : ""}${fmtPct(growth, 1)} MoM · ${fmtPct(company.gross_margin, 0)} gross margin`
              : undefined
          }
        />
      </div>
    </section>
  );
}

export const KpiHero = memo(KpiHeroBase);
