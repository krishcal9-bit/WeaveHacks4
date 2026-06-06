"use client";

import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtMonthLabel, fmtUSD } from "@/lib/format";

interface Point {
  month: string;
  cash: number;
  net_burn: number;
}

function ChartTooltip(props: {
  active?: boolean;
  label?: string;
  payload?: Array<{ payload: Point }>;
}) {
  if (!props.active || !props.payload?.length) return null;
  const d = props.payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 shadow-sm">
      <div className="text-[11px] font-medium text-muted-foreground">{props.label}</div>
      <div className="mt-0.5 text-[13px] font-semibold tabular-nums">{fmtUSD(d.cash)}</div>
      <div className="text-[11px] text-subtle-foreground tabular-nums">
        net burn {fmtUSD(d.net_burn)}/mo
      </div>
    </div>
  );
}

export function RunwayChart({ data }: { data: Point[] }) {
  const chartData = data.map((d) => ({ ...d, label: fmtMonthLabel(d.month) }));
  return (
    <ResponsiveContainer width="100%" height={248}>
      <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="cashFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#1b2330" stopOpacity={0.1} />
            <stop offset="100%" stopColor="#1b2330" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="label"
          tickLine={false}
          axisLine={false}
          tick={{ fontSize: 11, fill: "#8a93a1" }}
          dy={8}
          interval="preserveStartEnd"
        />
        <YAxis
          tickFormatter={(v: number) => fmtUSD(v, { compact: true })}
          tickLine={false}
          axisLine={false}
          width={52}
          tick={{ fontSize: 11, fill: "#8a93a1" }}
        />
        <Tooltip content={<ChartTooltip />} cursor={{ stroke: "#d3d8df", strokeWidth: 1 }} />
        <Area
          type="monotone"
          dataKey="cash"
          stroke="#1b2330"
          strokeWidth={2}
          fill="url(#cashFill)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
