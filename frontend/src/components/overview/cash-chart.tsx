"use client";

import { memo } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtMonthLabel, fmtUSD } from "@/lib/format";
import { useMounted } from "@/lib/use-mounted";
import type { CompanyFinancials } from "@/lib/types";

/*
  Cash position over time, themed by CSS variables so it reskins with the
  ledger. History is solid; the forecast (when present) is dashed with a
  downside band. Chart animation is off — data lands, ink dries.
*/

type ChartPoint = {
  label: string;
  cash?: number;
  base?: number;
  downside?: number;
  net_burn?: number;
};

function buildSeries(company: CompanyFinancials): ChartPoint[] {
  const history: ChartPoint[] = (company.cash_history ?? []).map((point) => ({
    label: fmtMonthLabel(point.month),
    cash: point.cash,
    net_burn: point.net_burn,
  }));
  const last = company.cash_history?.[company.cash_history.length - 1];
  const forecast: ChartPoint[] = (company.cash_forecast ?? []).map((point) => ({
    label: fmtMonthLabel(point.month),
    base: point.base_cash,
    downside: point.downside_cash,
    net_burn: point.net_burn,
  }));
  if (history.length > 0 && forecast.length > 0 && last) {
    // Stitch the forecast lines to the last actual point so they connect.
    history[history.length - 1] = { ...history[history.length - 1], base: last.cash, downside: last.cash };
  }
  return [...history, ...forecast];
}

function ChartTooltip(props: { active?: boolean; label?: string; payload?: Array<{ payload: ChartPoint }> }) {
  if (!props.active || !props.payload?.length) return null;
  const point = props.payload[0].payload;
  const cash = point.cash ?? point.base;
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 shadow-sm">
      <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-subtle-foreground">{props.label}</div>
      <div className="mt-0.5 text-[14px] font-semibold tabular-nums text-foreground">{fmtUSD(cash)}</div>
      {point.downside != null && point.cash == null && (
        <div className="text-[11px] tabular-nums text-risk">downside {fmtUSD(point.downside)}</div>
      )}
      {point.net_burn != null && (
        <div className="text-[11px] tabular-nums text-muted-foreground">net burn {fmtUSD(point.net_burn)}/mo</div>
      )}
    </div>
  );
}

function CashChartBase({ company }: { company: CompanyFinancials }) {
  const mounted = useMounted();
  const data = buildSeries(company);

  if (!mounted || data.length === 0) {
    return <div className="h-[260px] w-full rounded-md bg-surface-muted/40" aria-hidden />;
  }

  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={data} margin={{ top: 10, right: 10, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="atlas-cash-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--positive)" stopOpacity={0.22} />
            <stop offset="100%" stopColor="var(--positive)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="var(--border)" strokeOpacity={0.5} vertical={false} />
        <XAxis
          dataKey="label"
          tickLine={false}
          axisLine={false}
          tick={{ fontSize: 11, fill: "var(--subtle-foreground)" }}
          dy={8}
          interval="preserveStartEnd"
        />
        <YAxis
          tickFormatter={(v: number) => fmtUSD(v, { compact: true })}
          tickLine={false}
          axisLine={false}
          width={56}
          tick={{ fontSize: 11, fill: "var(--subtle-foreground)" }}
          domain={["auto", "auto"]}
        />
        <Tooltip content={<ChartTooltip />} cursor={{ stroke: "var(--border-strong)", strokeWidth: 1 }} />
        <Area
          type="monotone"
          dataKey="cash"
          stroke="var(--positive)"
          strokeWidth={2}
          fill="url(#atlas-cash-fill)"
          isAnimationActive={false}
          connectNulls={false}
          dot={false}
        />
        <Area
          type="monotone"
          dataKey="base"
          stroke="var(--info)"
          strokeWidth={1.75}
          strokeDasharray="5 4"
          fill="transparent"
          isAnimationActive={false}
          connectNulls={false}
          dot={false}
        />
        <Area
          type="monotone"
          dataKey="downside"
          stroke="var(--risk)"
          strokeWidth={1.25}
          strokeDasharray="3 4"
          strokeOpacity={0.8}
          fill="transparent"
          isAnimationActive={false}
          connectNulls={false}
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export const CashChart = memo(CashChartBase);
