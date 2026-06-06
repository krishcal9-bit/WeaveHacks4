"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fmtMonthLabel, fmtUSD } from "@/lib/format";

// Concrete palette (Recharts needs color strings, not CSS classes) — mirrors globals.css tokens.
const COLOR = {
  actual: "#1b2330", // charcoal — booked actuals
  base: "#2f5bb7", // info — base-case forecast
  downside: "#b42318", // risk — downside scenario
  buffer: "#b54708", // warning — policy floor
  axis: "#8a93a1",
  grid: "#eef0f3",
  zero: "#d3d8df",
} as const;

type Tone = "neutral" | "info" | "positive" | "warning" | "risk";

const TONE_STROKE: Record<Tone, string> = {
  neutral: "#1b2330",
  info: "#2f5bb7",
  positive: "#18794e",
  warning: "#b54708",
  risk: "#b42318",
};

interface HistoryPoint {
  month: string;
  cash: number;
  net_burn: number;
}
interface ForecastPoint {
  month: string;
  base_cash: number;
  downside_cash: number;
  net_burn: number;
  weighted_pipeline_arr?: number;
}

interface ForecastRow {
  month: string;
  label: string;
  actual: number | null;
  base: number | null;
  downside: number | null;
}

function ForecastTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  label?: string;
  payload?: Array<{ dataKey?: string | number; value?: number | null; payload: ForecastRow }>;
}) {
  if (!active || !payload?.length) return null;
  const seen = new Set<string>();
  const rows = payload
    .filter((p) => {
      const key = String(p.dataKey ?? "");
      if (p.value == null || seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .map((p) => {
      const key = String(p.dataKey ?? "");
      const meta =
        key === "actual"
          ? { name: "Actual cash", color: COLOR.actual }
          : key === "base"
            ? { name: "Base case", color: COLOR.base }
            : { name: "Downside", color: COLOR.downside };
      return { ...meta, value: p.value as number };
    });
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 shadow-sm">
      <div className="text-[11px] font-medium text-muted-foreground">{label}</div>
      <div className="mt-1 space-y-0.5">
        {rows.map((r) => (
          <div key={r.name} className="flex items-center gap-2 text-[12px] tabular-nums">
            <span className="h-2 w-2 rounded-full" style={{ background: r.color }} />
            <span className="text-subtle-foreground">{r.name}</span>
            <span className="ml-auto font-semibold text-foreground">{fmtUSD(r.value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Cash & runway forecast — booked actuals flow into base/downside scenario overlays.
 * Reference lines mark the minimum cash buffer, the zero line, and the "now" boundary.
 * Fixed height keeps the panel stable as live data arrives.
 */
export function CashForecastChart({
  history,
  forecast,
  minCashBuffer,
  height = 264,
}: {
  history: HistoryPoint[];
  forecast: ForecastPoint[];
  minCashBuffer?: number;
  height?: number;
}) {
  const rows: ForecastRow[] = [
    ...history.map((h) => ({
      month: h.month,
      label: fmtMonthLabel(h.month),
      actual: h.cash,
      base: null as number | null,
      downside: null as number | null,
    })),
    ...forecast.map((f) => ({
      month: f.month,
      label: fmtMonthLabel(f.month),
      actual: null as number | null,
      base: f.base_cash,
      downside: f.downside_cash,
    })),
  ];
  // Anchor the scenario lines to the last actual so the curves connect at "now".
  const anchor = history.length - 1;
  if (anchor >= 0 && forecast.length > 0) {
    rows[anchor].base = history[anchor].cash;
    rows[anchor].downside = history[anchor].cash;
  }
  const boundaryLabel = history.length ? fmtMonthLabel(history[history.length - 1].month) : undefined;
  const hasNegative = forecast.some((f) => f.downside_cash < 0);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 10, right: 10, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="cashFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={COLOR.actual} stopOpacity={0.12} />
            <stop offset="100%" stopColor={COLOR.actual} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={COLOR.grid} vertical={false} />
        <XAxis
          dataKey="label"
          tickLine={false}
          axisLine={false}
          tick={{ fontSize: 11, fill: COLOR.axis }}
          dy={8}
          interval="preserveStartEnd"
          minTickGap={24}
        />
        <YAxis
          tickFormatter={(v: number) => fmtUSD(v, { compact: true })}
          tickLine={false}
          axisLine={false}
          width={52}
          tick={{ fontSize: 11, fill: COLOR.axis }}
        />
        <Tooltip content={<ForecastTooltip />} cursor={{ stroke: COLOR.zero, strokeWidth: 1 }} />
        {minCashBuffer != null && (
          <ReferenceLine
            y={minCashBuffer}
            stroke={COLOR.buffer}
            strokeDasharray="5 4"
            strokeWidth={1}
            label={{
              value: `Min cash ${fmtUSD(minCashBuffer, { compact: true })}`,
              position: "insideTopRight",
              fontSize: 10,
              fill: COLOR.buffer,
            }}
          />
        )}
        {hasNegative && <ReferenceLine y={0} stroke={COLOR.zero} strokeWidth={1} />}
        {boundaryLabel && (
          <ReferenceLine
            x={boundaryLabel}
            stroke={COLOR.axis}
            strokeDasharray="3 3"
            strokeWidth={1}
            label={{ value: "now", position: "top", fontSize: 10, fill: COLOR.axis }}
          />
        )}
        <Area
          type="monotone"
          dataKey="actual"
          stroke={COLOR.actual}
          strokeWidth={2}
          fill="url(#cashFill)"
          connectNulls={false}
          dot={false}
          activeDot={{ r: 3 }}
          isAnimationActive={false}
        />
        <Line
          type="monotone"
          dataKey="base"
          stroke={COLOR.base}
          strokeWidth={2}
          connectNulls={false}
          dot={false}
          activeDot={{ r: 3 }}
          isAnimationActive={false}
        />
        <Line
          type="monotone"
          dataKey="downside"
          stroke={COLOR.downside}
          strokeWidth={1.5}
          strokeDasharray="4 3"
          connectNulls={false}
          dot={false}
          activeDot={{ r: 3 }}
          isAnimationActive={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

interface MiniPoint {
  label: string;
  value: number;
}

function MiniTooltip({
  active,
  payload,
  label,
  unit,
}: {
  active?: boolean;
  label?: string;
  unit?: string;
  payload?: Array<{ value?: number }>;
}) {
  if (!active || !payload?.length || payload[0].value == null) return null;
  return (
    <div className="rounded-md border border-border bg-surface px-2.5 py-1.5 shadow-sm">
      <div className="text-[10px] text-subtle-foreground">{label}</div>
      <div className="text-[12px] font-semibold tabular-nums text-foreground">
        {fmtUSD(payload[0].value)}
        {unit ? <span className="font-normal text-subtle-foreground">{unit}</span> : null}
      </div>
    </div>
  );
}

/**
 * Compact trend sparkline (bars or area) with a fixed height — used for burn and
 * pipeline-ARR panels. Axes are intentionally minimal to keep the footprint small.
 */
export function MiniTrendChart({
  data,
  kind = "bar",
  tone = "neutral",
  unit,
  height = 92,
}: {
  data: MiniPoint[];
  kind?: "bar" | "area";
  tone?: Tone;
  unit?: string;
  height?: number;
}) {
  const stroke = TONE_STROKE[tone];
  const gradientId = `mini-${tone}-${kind}`;

  if (kind === "area") {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 4, right: 2, bottom: 0, left: 2 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.18} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis dataKey="label" hide />
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Tooltip content={<MiniTooltip unit={unit} />} cursor={{ stroke: COLOR.zero, strokeWidth: 1 }} />
          <Area
            type="monotone"
            dataKey="value"
            stroke={stroke}
            strokeWidth={1.75}
            fill={`url(#${gradientId})`}
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 4, right: 2, bottom: 0, left: 2 }}>
        <XAxis dataKey="label" hide />
        <YAxis hide domain={[0, "dataMax"]} />
        <Tooltip
          content={<MiniTooltip unit={unit} />}
          cursor={{ fill: COLOR.grid }}
        />
        <Bar dataKey="value" fill={stroke} radius={[2, 2, 0, 0]} maxBarSize={18} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
