"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, Plus } from "lucide-react";
import { api } from "@/lib/api";
import type { CompanyFinancials, DecisionEvent, Vendor } from "@/lib/types";
import { fmtMonths, fmtPct, fmtUSD } from "@/lib/format";
import { Card, Pill, SectionTitle } from "@/components/ui";
import { RunwayChart } from "@/components/runway-chart";
import { decisionStyle } from "@/lib/agents";

const RUNWAY_GUARDRAIL = 9; // months — from the seeded finance policy

export default function DashboardPage() {
  const [co, setCo] = useState<CompanyFinancials | null>(null);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [decisions, setDecisions] = useState<DecisionEvent[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.company().then(setCo).catch((e) => setErr(String(e)));
    api.vendors().then(setVendors).catch(() => {});
    api.decisions().then(setDecisions).catch(() => {});
  }, []);

  if (err) {
    return (
      <div className="p-10">
        <p className="text-sm text-risk">
          Couldn’t reach the finance service. Make sure the agent is running on :8123.
        </p>
        <p className="mt-1 text-xs text-subtle-foreground">{err}</p>
      </div>
    );
  }
  if (!co) return <Skeleton />;

  const runwaySlack = co.runway_months - RUNWAY_GUARDRAIL;
  const savings = vendors
    .filter((v) => v.status === "up_for_renewal" || (v.notes ?? "").match(/over|underused/i))
    .map((v) => ({
      name: v.name,
      detail:
        v.status === "up_for_renewal"
          ? `Renewal ${v.renewal_date} · renegotiate`
          : v.notes ?? "",
      est: Math.round(v.annual_cost * 0.2),
    }));

  return (
    <div className="mx-auto max-w-[1180px] px-8 py-8">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <SectionTitle>Executive Dashboard</SectionTitle>
          <h1 className="mt-1.5 text-[22px] font-semibold tracking-tight">Company Health</h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            {co.sector} · {co.headcount} people · updated {co.cash_history.at(-1)?.month}
          </p>
        </div>
        <Link
          href="/decisions"
          className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3.5 py-2 text-[13px] font-medium text-accent-foreground transition-opacity hover:opacity-90"
        >
          <Plus className="h-4 w-4" strokeWidth={2} />
          New decision
        </Link>
      </div>

      {/* KPI row */}
      <div className="mt-6 grid grid-cols-4 gap-4">
        <Kpi label="Cash on hand" value={fmtUSD(co.cash_on_hand, { compact: true })} sub={`${fmtUSD(co.last_raise?.amount, { compact: true })} ${co.last_raise?.round}`} />
        <Kpi label="Net burn / mo" value={fmtUSD(co.monthly_net_burn, { compact: true })} sub={`${fmtUSD(co.monthly_gross_burn, { compact: true })} gross`} />
        <Kpi
          label="Runway"
          value={fmtMonths(co.runway_months)}
          sub={`${runwaySlack >= 0 ? "+" : ""}${runwaySlack.toFixed(1)} mo vs ${RUNWAY_GUARDRAIL}-mo floor`}
          tone={co.runway_months >= 12 ? "positive" : co.runway_months >= RUNWAY_GUARDRAIL ? "warning" : "risk"}
        />
        <Kpi
          label="ARR"
          value={fmtUSD(co.arr, { compact: true })}
          sub={`${fmtPct(co.mrr_growth_mom)} MoM`}
          subTone="positive"
          subIcon
        />
      </div>

      {/* Chart + guardrails */}
      <div className="mt-4 grid grid-cols-3 gap-4">
        <Card className="col-span-2 p-5">
          <div className="flex items-center justify-between">
            <SectionTitle>Cash & Runway · trailing 12 months</SectionTitle>
            <Pill className="border-border text-muted-foreground">
              {fmtUSD(co.cash_history[0]?.cash, { compact: true })} → {fmtUSD(co.cash_on_hand, { compact: true })}
            </Pill>
          </div>
          <div className="mt-4">
            <RunwayChart data={co.cash_history} />
          </div>
        </Card>

        <Card className="p-5">
          <SectionTitle>Guardrails & Risk</SectionTitle>
          <div className="mt-4 space-y-4">
            <Guardrail
              label="Runway floor"
              ok={co.runway_months >= RUNWAY_GUARDRAIL}
              value={`${fmtMonths(co.runway_months)} / ${RUNWAY_GUARDRAIL} mo min`}
            />
            <Guardrail label="Gross margin" ok={co.gross_margin >= 0.7} value={fmtPct(co.gross_margin)} />
            <Guardrail label="Net revenue retention" ok={co.ndr >= 1.0} value={fmtPct(co.ndr)} />
            <Guardrail
              label="Burn multiple"
              ok={co.monthly_net_burn / (co.mrr * co.mrr_growth_mom) < 2}
              value={(co.monthly_net_burn / (co.mrr * co.mrr_growth_mom)).toFixed(1) + "×"}
            />
          </div>
        </Card>
      </div>

      {/* Recent decisions + savings */}
      <div className="mt-4 grid grid-cols-3 gap-4">
        <Card className="col-span-2 p-5">
          <div className="flex items-center justify-between">
            <SectionTitle>Recent Decisions</SectionTitle>
            <Link href="/activity" className="text-[12px] font-medium text-muted-foreground hover:text-foreground">
              View all
            </Link>
          </div>
          <div className="mt-3 divide-y divide-border">
            {decisions.length === 0 && (
              <p className="py-6 text-[13px] text-subtle-foreground">No decisions yet.</p>
            )}
            {decisions.slice(0, 5).map((d) => (
              <div key={d._id} className="flex items-start justify-between gap-4 py-3">
                <div className="min-w-0">
                  <div className="truncate text-[13px] font-medium">{d.title}</div>
                  {d.summary && (
                    <div className="mt-0.5 line-clamp-1 text-[12px] text-muted-foreground">{d.summary}</div>
                  )}
                </div>
                {d.decision && (
                  <Pill className={decisionStyle(d.decision)}>
                    {d.decision}
                    {typeof d.confidence === "number" ? ` · ${d.confidence}%` : ""}
                  </Pill>
                )}
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-5">
          <SectionTitle>Savings Opportunities</SectionTitle>
          <div className="mt-3 space-y-3">
            {savings.length === 0 && (
              <p className="py-2 text-[13px] text-subtle-foreground">No flags.</p>
            )}
            {savings.map((s) => (
              <div key={s.name} className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-[13px] font-medium">{s.name}</div>
                  <div className="mt-0.5 line-clamp-1 text-[12px] text-muted-foreground">{s.detail}</div>
                </div>
                <div className="shrink-0 text-[13px] font-semibold tabular-nums text-positive">
                  ~{fmtUSD(s.est, { compact: true })}/yr
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

function Kpi({
  label,
  value,
  sub,
  tone,
  subTone,
  subIcon,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "positive" | "warning" | "risk";
  subTone?: "positive";
  subIcon?: boolean;
}) {
  const toneCls =
    tone === "positive" ? "text-positive" : tone === "warning" ? "text-warning" : tone === "risk" ? "text-risk" : "text-foreground";
  return (
    <Card className="p-4">
      <div className="text-[12px] font-medium text-muted-foreground">{label}</div>
      <div className={`mt-2 text-[26px] font-semibold leading-none tracking-tight tabular-nums ${toneCls}`}>
        {value}
      </div>
      {sub && (
        <div
          className={`mt-2 inline-flex items-center gap-1 text-[12px] tabular-nums ${
            subTone === "positive" ? "text-positive" : "text-subtle-foreground"
          }`}
        >
          {subIcon && <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={2} />}
          {sub}
        </div>
      )}
    </Card>
  );
}

function Guardrail({ label, ok, value }: { label: string; ok: boolean; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-positive" : "bg-warning"}`} />
        <span className="text-[13px] text-muted-foreground">{label}</span>
      </div>
      <span className="text-[13px] font-medium tabular-nums">{value}</span>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="mx-auto max-w-[1180px] px-8 py-8">
      <div className="h-7 w-48 animate-pulse rounded bg-surface-muted" />
      <div className="mt-6 grid grid-cols-4 gap-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-24 animate-pulse rounded-xl border border-border bg-surface" />
        ))}
      </div>
      <div className="mt-4 h-72 animate-pulse rounded-xl border border-border bg-surface" />
    </div>
  );
}
