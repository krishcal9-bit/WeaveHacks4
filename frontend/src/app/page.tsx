"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Activity,
  Building2,
  ChartPie,
  CreditCard,
  Filter,
  Flame,
  History,
  Hourglass,
  LineChart,
  Percent,
  PiggyBank,
  Plus,
  Repeat2,
  Scale,
  ShieldAlert,
  Target,
  TrendingUp,
  TriangleAlert,
  UserPlus,
  Users,
  Wallet,
} from "lucide-react";
import { api } from "@/lib/api";
import type { CompanyFinancials, DecisionEvent, ReliabilityScore, SponsorHealth, Vendor } from "@/lib/types";
import {
  fmtDate,
  fmtInt,
  fmtMonthLabel,
  fmtMonths,
  fmtPct,
  fmtRelMonths,
  fmtUSD,
  monthsBetween,
  titleCase,
} from "@/lib/format";
import { cx, StatusDot } from "@/components/ui";
import {
  Bar,
  ConstraintBadge,
  DecisionRow,
  Delta,
  HealthChip,
  MetaItem,
  MetricTile,
  NotAvailable,
  Panel,
  RiskRow,
  ScrollX,
  SeverityPill,
  severityTone,
  type Tone,
  TonePill,
} from "@/components/dashboard";
import { CashForecastChart, MiniTrendChart } from "@/components/charts";

// Policy-derived thresholds (seeded finance policies + board constraints; never hallucinated).
const RUNWAY_FLOOR_MONTHS = 9; // pol-runway / board constraint
const MIN_CASH_BUFFER = 1_500_000; // pol-cash
const BOARD_NOTIFY_VENDOR = 150_000; // pol-spend / board constraint (annualized)
const GROSS_MARGIN_TARGET = 0.7;
const POLL_MS = 30_000;

const HEAT: Record<Tone, string> = {
  risk: "bg-risk-bg text-risk",
  warning: "bg-warning-bg text-warning",
  info: "bg-info-bg text-info",
  positive: "bg-positive-bg text-positive",
  neutral: "bg-surface-muted text-muted-foreground",
};

// Literal tone → text-color classes (no dynamic interpolation, so Tailwind always emits them).
const TONE_TEXT_CLASS: Record<Tone, string> = {
  neutral: "text-foreground",
  positive: "text-positive",
  warning: "text-warning",
  risk: "text-risk",
  info: "text-info",
};

function avg(nums: number[]): number {
  const xs = nums.filter((n) => !Number.isNaN(n));
  return xs.length ? xs.reduce((s, n) => s + n, 0) / xs.length : NaN;
}

function runwayTone(months: number): Tone {
  if (Number.isNaN(months)) return "neutral";
  return months >= 12 ? "positive" : months >= RUNWAY_FLOOR_MONTHS ? "warning" : "risk";
}

function renewalTone(months: number): Tone {
  if (Number.isNaN(months)) return "neutral";
  if (months <= 2) return "risk";
  if (months <= 4) return "warning";
  if (months <= 8) return "info";
  return "neutral";
}

function churnTone(churn?: number): Tone {
  if (churn == null) return "neutral";
  return churn > 0.04 ? "risk" : churn > 0.02 ? "warning" : "positive";
}

function ndrTone(ndr?: number): Tone {
  if (ndr == null) return "neutral";
  return ndr < 0.95 ? "risk" : ndr < 1 ? "warning" : "positive";
}

function calTone(score: number): Tone {
  return score >= 85 ? "positive" : score >= 70 ? "warning" : "risk";
}

function statusTone(status?: string): Tone {
  const s = (status ?? "").toLowerCase();
  if (s.includes("renewal")) return "warning";
  if (s.includes("churn") || s.includes("cancel") || s.includes("risk")) return "risk";
  if (s === "active") return "positive";
  return "neutral";
}

function avgReliability(scores?: ReliabilityScore[]): number | null {
  if (!scores?.length) return null;
  const a = avg(scores.map((s) => s.reliability));
  return Number.isNaN(a) ? null : a;
}

const CONSTRAINT_LABEL: Record<Tone, string> = {
  positive: "On track",
  warning: "Watch",
  risk: "Breach",
  info: "Active",
  neutral: "Monitored",
};

interface ConstraintEval {
  text: string;
  tone: Tone;
  note: string;
}

// Evaluate each board constraint against live metrics where derivable; unknown constraints
// render neutrally rather than guessing.
function evaluateConstraints(co: CompanyFinancials, vendors: Vendor[]): ConstraintEval[] {
  const list = co.board_constraints ?? [];
  return list.map((text) => {
    const t = text.toLowerCase();
    if (t.includes("runway")) {
      const ok = co.runway_months >= RUNWAY_FLOOR_MONTHS;
      return { text, tone: ok ? "positive" : "risk", note: `${fmtMonths(co.runway_months)} vs ${RUNWAY_FLOOR_MONTHS}-mo floor` };
    }
    if (t.includes("vendor") || t.includes("150")) {
      const over = vendors.filter((v) => v.annual_cost > BOARD_NOTIFY_VENDOR);
      return {
        text,
        tone: over.length ? "warning" : "positive",
        note: over.length
          ? `${over.length} contract${over.length > 1 ? "s" : ""} over ${fmtUSD(BOARD_NOTIFY_VENDOR, { compact: true })}/yr`
          : "All within limit",
      };
    }
    if (t.includes("headcount") || t.includes("hire")) {
      const plans = co.hiring_plan ?? [];
      const roles = plans.reduce((s, h) => s + h.roles, 0);
      const added = plans.reduce((s, h) => s + h.monthly_cost, 0);
      return {
        text,
        tone: plans.length ? "info" : "neutral",
        note: plans.length ? `${roles} roles · +${fmtUSD(added, { compact: true })}/mo planned` : "No planned hires",
      };
    }
    if (t.includes("soc 2") || t.includes("soc2") || t.includes("security") || t.includes("evidence")) {
      const open = (co.security_incidents ?? []).filter((s) => /open|gap/i.test(s.status ?? ""));
      return {
        text,
        tone: open.length ? "warning" : "positive",
        note: open.length ? `${open.length} open control gap${open.length > 1 ? "s" : ""}` : "No open gaps",
      };
    }
    return { text, tone: "neutral", note: "Monitored" };
  });
}

interface SavingsItem {
  name: string;
  detail: string;
  est: number;
}

function computeSavings(vendors: Vendor[]): SavingsItem[] {
  return vendors
    .filter((v) => v.status === "up_for_renewal" || /over|underused|trending/i.test(v.notes ?? ""))
    .map((v) => ({
      name: v.name,
      detail: v.status === "up_for_renewal" ? `Renewal ${fmtDate(v.renewal_date)} · renegotiate terms` : v.notes ?? "",
      est: Math.round(v.annual_cost * 0.2),
    }))
    .sort((a, b) => b.est - a.est);
}

interface HiringRow {
  team: string;
  roles: number;
  monthly_cost: number;
  start_month: string;
  dependency?: string;
  cumulativeAdded: number;
  runwayAfter: number;
}

function computeHiringImpact(co: CompanyFinancials): HiringRow[] {
  const plans = [...(co.hiring_plan ?? [])].sort((a, b) => a.start_month.localeCompare(b.start_month));
  let cum = 0;
  return plans.map((h) => {
    cum += h.monthly_cost;
    const newBurn = co.monthly_net_burn + cum;
    const runwayAfter = newBurn > 0 ? co.cash_on_hand / newBurn : Infinity;
    return { ...h, cumulativeAdded: cum, runwayAfter };
  });
}

export default function DashboardPage() {
  const [co, setCo] = useState<CompanyFinancials | null>(null);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [decisions, setDecisions] = useState<DecisionEvent[]>([]);
  const [health, setHealth] = useState<SponsorHealth | null>(null);
  const [healthErr, setHealthErr] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const gotCompany = useRef(false);

  useEffect(() => {
    let active = true;
    const load = async () => {
      const [c, v, d, h] = await Promise.allSettled([
        api.company(),
        api.vendors(),
        api.decisions(),
        api.healthSnapshot(),
      ]);
      if (!active) return;
      if (c.status === "fulfilled") {
        gotCompany.current = true;
        setCo(c.value);
        setErr(null);
      } else if (!gotCompany.current) {
        setErr(String(c.reason));
      }
      if (v.status === "fulfilled") setVendors(v.value);
      if (d.status === "fulfilled") setDecisions(d.value);
      if (h.status === "fulfilled") {
        setHealth(h.value);
        setHealthErr(false);
      } else {
        setHealthErr(true);
      }
      setUpdatedAt(
        new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
      );
    };
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  if (err && !co) return <ErrorState err={err} />;
  if (!co) return <DashboardSkeleton />;

  const ready = health?.ready === true;

  return (
    <div className="mx-auto w-full max-w-[1560px] px-4 py-5 sm:px-6">
      <Header co={co} updatedAt={updatedAt} ready={ready} />

      <div className="mt-4">
        <ReadinessStrip health={health} healthErr={healthErr} />
      </div>

      <div className="mt-3">
        <KpiStrip co={co} />
      </div>

      <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-12">
        <ForecastPanel co={co} className="lg:col-span-8" />
        <ConstraintsPanel co={co} vendors={vendors} className="lg:col-span-4" />

        <BurnPanel co={co} className="lg:col-span-4" />
        <ArrPanel co={co} className="lg:col-span-4" />
        <CohortPanel co={co} className="lg:col-span-4" />

        <PipelinePanel co={co} className="lg:col-span-5" />
        <VendorPanel co={co} vendors={vendors} className="lg:col-span-7" />

        <HiringPanel co={co} className="lg:col-span-7" />
        <BlockersPanel co={co} className="lg:col-span-5" />

        <DecisionsPanel decisions={decisions} className="lg:col-span-7" />
        <CalibrationPanel co={co} className="lg:col-span-5" />

        <SavingsPanel vendors={vendors} className="lg:col-span-7" />
        <OpexPanel co={co} className="lg:col-span-5" />
      </div>
    </div>
  );
}

function Header({ co, updatedAt, ready }: { co: CompanyFinancials; updatedAt: string | null; ready: boolean }) {
  const meta = [co.stage, co.sector, `${fmtInt(co.headcount)} people`, co.hq].filter(Boolean).join(" · ");
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.08em] text-subtle-foreground">
          <Building2 className="h-3.5 w-3.5" strokeWidth={1.85} />
          Executive command center
        </div>
        <h1 className="mt-1 truncate text-[20px] font-semibold tracking-tight text-foreground">{co.name}</h1>
        <p className="mt-0.5 text-[12px] text-muted-foreground">{meta}</p>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 py-1 text-[11px] text-muted-foreground">
          <StatusDot tone={ready ? "positive" : "warning"} pulse />
          {updatedAt ? `Updated ${updatedAt}` : "Connecting…"}
        </span>
        <Link
          href="/decisions"
          className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-[12px] font-medium text-accent-foreground transition-opacity hover:opacity-90"
        >
          <Plus className="h-4 w-4" strokeWidth={2} />
          New decision
        </Link>
      </div>
    </div>
  );
}

function ReadinessStrip({ health, healthErr }: { health: SponsorHealth | null; healthErr: boolean }) {
  if (healthErr && !health) {
    return (
      <Panel title="Live system readiness" eyebrow="Sponsors" icon={Activity}>
        <div className="flex items-center gap-2 text-[12px] text-subtle-foreground">
          <StatusDot tone="neutral" />
          Readiness checks unavailable — the agent health endpoint did not respond.
        </div>
      </Panel>
    );
  }
  const sponsors = health?.sponsors ?? [];
  const ready = health?.ready === true;
  const live = sponsors.filter((s) => s.ready).length;
  const blockers = health?.blockers ?? [];
  return (
    <Panel
      title="Live system readiness"
      eyebrow="Sponsors"
      icon={Activity}
      action={<TonePill tone={ready ? "positive" : "warning"}>{ready ? "All systems live" : "Preflight blocked"}</TonePill>}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="text-[11px] font-medium tabular-nums text-muted-foreground">
          {sponsors.length ? `${live}/${sponsors.length} sponsors live` : "No checks"} · {health?.mode ?? "strict-live"}
        </span>
        <div className="flex flex-wrap gap-1.5">
          {sponsors.length === 0 ? (
            <span className="text-[11px] text-subtle-foreground">No sponsor checks reported</span>
          ) : (
            sponsors.map((s) => (
              <HealthChip key={s.label} label={s.label} ready={s.ready} detail={s.detail ?? s.error} />
            ))
          )}
        </div>
      </div>
      {blockers.length > 0 && (
        <ul className="mt-2.5 space-y-1 border-t border-border pt-2.5">
          {blockers.map((b, i) => (
            <li key={i} className="flex items-start gap-1.5 text-[11.5px] leading-relaxed text-risk">
              <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={2} />
              <span className="min-w-0 break-words">{b}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function KpiStrip({ co }: { co: CompanyFinancials }) {
  const slack = co.runway_months - RUNWAY_FLOOR_MONTHS;
  const gmTone: Tone = co.gross_margin >= GROSS_MARGIN_TARGET ? "positive" : "warning";
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      <MetricTile
        icon={Wallet}
        label="Cash on hand"
        value={fmtUSD(co.cash_on_hand, { compact: true })}
        sub={co.last_raise ? `${fmtUSD(co.last_raise.amount, { compact: true })} ${co.last_raise.round}` : `as of ${fmtDate(co.updated)}`}
      />
      <MetricTile
        icon={Flame}
        label="Net burn / mo"
        value={fmtUSD(co.monthly_net_burn, { compact: true })}
        sub={`${fmtUSD(co.monthly_gross_burn, { compact: true })} gross`}
      />
      <MetricTile
        icon={Hourglass}
        label="Runway"
        value={fmtMonths(co.runway_months)}
        tone={runwayTone(co.runway_months)}
        sub={
          <Delta
            value={`${slack >= 0 ? "+" : ""}${slack.toFixed(1)} mo vs floor`}
            direction={slack >= 0 ? "up" : "down"}
            tone={slack >= 0 ? "positive" : "risk"}
          />
        }
      />
      <MetricTile
        icon={TrendingUp}
        label="ARR"
        value={fmtUSD(co.arr, { compact: true })}
        sub={<Delta value={`${fmtPct(co.mrr_growth_mom)} MoM`} direction="up" tone="positive" />}
      />
      <MetricTile
        icon={Percent}
        label="Gross margin"
        value={fmtPct(co.gross_margin)}
        tone={gmTone}
        sub={`target ${fmtPct(GROSS_MARGIN_TARGET)}`}
      />
      <MetricTile
        icon={Repeat2}
        label="Net dollar retention"
        value={fmtPct(co.ndr)}
        tone={ndrTone(co.ndr)}
        sub={`${fmtPct(co.logo_churn_mom, 1)} logo churn`}
      />
    </div>
  );
}

function LegendSwatch({ color, label, dashed = false }: { color: string; label: string; dashed?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="inline-block h-0.5 w-4 rounded"
        style={{
          background: dashed ? `repeating-linear-gradient(90deg, ${color} 0 4px, transparent 4px 7px)` : color,
        }}
      />
      <span>{label}</span>
    </span>
  );
}

function ForecastPanel({ co, className }: { co: CompanyFinancials; className?: string }) {
  const fc = co.cash_forecast ?? [];
  const downsideZero = fc.find((f) => f.downside_cash < 0);
  const baseTrough = fc.length ? fc.reduce((m, f) => (f.base_cash < m.base_cash ? f : m), fc[0]) : null;
  const action = downsideZero ? (
    <TonePill tone="risk">Downside below $0 · {fmtMonthLabel(downsideZero.month)}</TonePill>
  ) : baseTrough ? (
    <TonePill tone="neutral">Base trough {fmtUSD(baseTrough.base_cash, { compact: true })}</TonePill>
  ) : undefined;
  return (
    <Panel title="Cash & runway forecast" eyebrow="Liquidity" icon={LineChart} className={className} action={action}>
      {co.cash_history.length === 0 && fc.length === 0 ? (
        <NotAvailable label="No cash history or forecast available" />
      ) : (
        <>
          <CashForecastChart history={co.cash_history} forecast={fc} minCashBuffer={MIN_CASH_BUFFER} />
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border pt-2 text-[11px] text-muted-foreground">
            <LegendSwatch color="#1b2330" label="Actual cash" />
            <LegendSwatch color="#2f5bb7" label="Base case" />
            <LegendSwatch color="#b42318" label="Downside" dashed />
            <LegendSwatch color="#b54708" label={`Min cash ${fmtUSD(MIN_CASH_BUFFER, { compact: true })}`} dashed />
          </div>
        </>
      )}
    </Panel>
  );
}

function ConstraintsPanel({ co, vendors, className }: { co: CompanyFinancials; vendors: Vendor[]; className?: string }) {
  const evals = evaluateConstraints(co, vendors);
  return (
    <Panel title="Board constraint monitor" eyebrow="Governance" icon={Scale} className={className}>
      {evals.length === 0 ? (
        <NotAvailable label="No board constraints published" />
      ) : (
        <ul className="divide-y divide-border">
          {evals.map((c, i) => (
            <li key={i} className="flex items-start gap-2.5 py-2.5">
              <span className="w-[68px] shrink-0">
                <ConstraintBadge tone={c.tone}>{CONSTRAINT_LABEL[c.tone]}</ConstraintBadge>
              </span>
              <div className="min-w-0 flex-1">
                <div className="line-clamp-2 text-[12px] leading-relaxed text-foreground">{c.text}</div>
                <div className="mt-0.5 text-[11px] tabular-nums text-subtle-foreground">{c.note}</div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function BurnPanel({ co, className }: { co: CompanyFinancials; className?: string }) {
  const hist = co.cash_history ?? [];
  const data = hist.map((h) => ({ label: fmtMonthLabel(h.month), value: h.net_burn }));
  const current = hist.at(-1)?.net_burn;
  const avgBurn = avg(hist.map((h) => h.net_burn));
  return (
    <Panel title="Net burn — trailing 12 mo" eyebrow="Cash burn" icon={Flame} className={className}>
      {data.length === 0 ? (
        <NotAvailable label="No burn history available" />
      ) : (
        <>
          <div className="flex items-end justify-between gap-3">
            <MetaItem label="Current" value={`${fmtUSD(current, { compact: true })}/mo`} />
            <MetaItem label="12-mo avg" value={`${fmtUSD(avgBurn, { compact: true })}/mo`} />
          </div>
          <div className="mt-2">
            <MiniTrendChart data={data} kind="bar" tone="neutral" unit="/mo" />
          </div>
        </>
      )}
    </Panel>
  );
}

function ArrPanel({ co, className }: { co: CompanyFinancials; className?: string }) {
  const arrHist = co.arr_history ?? [];
  const fc = co.cash_forecast ?? [];
  const usingBooked = arrHist.length > 0;
  const data = usingBooked
    ? arrHist.map((a) => ({ label: fmtMonthLabel(a.month), value: a.arr }))
    : fc
        .filter((f) => f.weighted_pipeline_arr != null)
        .map((f) => ({ label: fmtMonthLabel(f.month), value: f.weighted_pipeline_arr as number }));
  const title = usingBooked ? "Booked ARR — trailing" : "Weighted pipeline ARR — forecast";
  const endpoint = data.at(-1)?.value;
  return (
    <Panel title={title} eyebrow="Revenue" icon={TrendingUp} className={className}>
      {data.length === 0 ? (
        <NotAvailable label="No ARR or pipeline series available" />
      ) : (
        <>
          <div className="flex items-end justify-between gap-2">
            <MetaItem label="Current ARR" value={fmtUSD(co.arr, { compact: true })} />
            <MetaItem label="MoM" value={fmtPct(co.mrr_growth_mom)} tone="positive" />
            <MetaItem label={usingBooked ? "Latest" : "Builds to"} value={fmtUSD(endpoint, { compact: true })} />
          </div>
          <div className="mt-2">
            <MiniTrendChart data={data} kind="area" tone="info" />
          </div>
        </>
      )}
    </Panel>
  );
}

function CohortPanel({ co, className }: { co: CompanyFinancials; className?: string }) {
  const cohorts = co.customer_cohorts ?? [];
  return (
    <Panel title="Cohort health" eyebrow="Customers" icon={Users} className={className}>
      {cohorts.length === 0 ? (
        <NotAvailable label="No cohort data available" />
      ) : (
        <ul className="divide-y divide-border">
          {cohorts.map((c, i) => (
            <li key={c.segment ?? i} className="py-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[12.5px] font-medium text-foreground">{c.segment}</span>
                <span className="shrink-0 text-[11px] tabular-nums text-subtle-foreground">
                  {fmtInt(c.customers)} cust · {fmtUSD(c.mrr, { compact: true })} MRR
                </span>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                <TonePill tone={churnTone(c.logo_churn_mom)}>{fmtPct(c.logo_churn_mom, 1)} churn</TonePill>
                <TonePill tone={ndrTone(c.ndr)}>{fmtPct(c.ndr)} NDR</TonePill>
                {c.risk && <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">{c.risk}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function PipelinePanel({ co, className }: { co: CompanyFinancials; className?: string }) {
  const stages = co.pipeline_by_stage ?? [];
  const totalWeighted = stages.reduce((s, st) => s + (st.weighted_arr || 0), 0);
  return (
    <Panel
      title="Pipeline risk board"
      eyebrow="Pipeline"
      icon={Filter}
      className={className}
      action={
        stages.length ? (
          <span className="text-[11px] tabular-nums text-subtle-foreground">
            {fmtUSD(totalWeighted, { compact: true })} weighted
          </span>
        ) : undefined
      }
    >
      {stages.length === 0 ? (
        <NotAvailable label="No pipeline data available" />
      ) : (
        <ul className="space-y-2.5">
          {stages.map((st, i) => {
            const conv = st.arr > 0 ? st.weighted_arr / st.arr : 0;
            const convTone: Tone = conv >= 0.7 ? "positive" : conv >= 0.4 ? "info" : "neutral";
            return (
              <li key={st.stage ?? i}>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[12.5px] font-medium text-foreground">{st.stage}</span>
                  <span className="shrink-0 text-[11px] tabular-nums text-subtle-foreground">
                    {fmtInt(st.opportunities)} opps · {fmtUSD(st.arr, { compact: true })}
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <Bar value={st.weighted_arr} max={st.arr || st.weighted_arr} tone={convTone} className="flex-1" />
                  <span className="w-12 shrink-0 text-right text-[11px] font-semibold tabular-nums text-foreground">
                    {fmtUSD(st.weighted_arr, { compact: true })}
                  </span>
                </div>
                {st.risk && <div className="mt-0.5 line-clamp-1 text-[11px] text-warning">{st.risk}</div>}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

function VendorPanel({ co, vendors, className }: { co: CompanyFinancials; vendors: Vendor[]; className?: string }) {
  const asOf = co.updated;
  const rows = vendors
    .map((v) => ({ v, months: monthsToRenewal(asOf, v.renewal_date) }))
    .sort((a, b) => (Number.isNaN(a.months) ? 999 : a.months) - (Number.isNaN(b.months) ? 999 : b.months));
  const maxCost = Math.max(1, ...vendors.map((v) => v.annual_cost || 0));
  return (
    <Panel
      title="Vendor renewal & spend"
      eyebrow="Procurement"
      icon={CreditCard}
      className={className}
      action={<span className="hidden text-[11px] text-subtle-foreground sm:inline">heat = renewal urgency</span>}
    >
      {rows.length === 0 ? (
        <NotAvailable label="No vendor contracts available" />
      ) : (
        <ScrollX minWidth={640}>
          <table className="w-full border-collapse text-[12px]">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-[0.08em] text-subtle-foreground">
                <th className="py-1.5 pr-2 font-medium">Vendor</th>
                <th className="px-2 py-1.5 font-medium">Annual</th>
                <th className="px-2 py-1.5 font-medium">Renewal</th>
                <th className="px-2 py-1.5 font-medium">Status</th>
                <th className="py-1.5 pl-2 text-right font-medium">Notes</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map(({ v, months }) => (
                <tr key={v.id ?? v.name} className="align-top">
                  <td className="py-2 pr-2">
                    <div className="font-medium text-foreground">{v.name}</div>
                    <div className="text-[10.5px] text-subtle-foreground">{titleCase(v.category || "")}</div>
                  </td>
                  <td className="px-2 py-2 tabular-nums">
                    <div className="font-semibold text-foreground">{fmtUSD(v.annual_cost, { compact: true })}</div>
                    <Bar
                      value={v.annual_cost}
                      max={maxCost}
                      tone={v.annual_cost > BOARD_NOTIFY_VENDOR ? "warning" : "neutral"}
                      className="mt-1 w-16"
                    />
                  </td>
                  <td className="px-2 py-2">
                    <span
                      className={cx(
                        "inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium tabular-nums",
                        HEAT[renewalTone(months)],
                      )}
                    >
                      {fmtRelMonths(months)}
                    </span>
                    <div className="mt-0.5 text-[10.5px] tabular-nums text-subtle-foreground">{fmtDate(v.renewal_date)}</div>
                  </td>
                  <td className="px-2 py-2">
                    <TonePill tone={statusTone(v.status)}>{titleCase(v.status || "—")}</TonePill>
                  </td>
                  <td className="py-2 pl-2 text-right text-[11px] text-muted-foreground">
                    <div className="ml-auto line-clamp-2 max-w-[210px]">{v.notes ?? "—"}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollX>
      )}
    </Panel>
  );
}

function HiringPanel({ co, className }: { co: CompanyFinancials; className?: string }) {
  const rows = computeHiringImpact(co);
  const totalMonthly = rows.length ? rows[rows.length - 1].cumulativeAdded : 0;
  const totalRoles = rows.reduce((s, r) => s + r.roles, 0);
  const finalRunway = rows.length ? rows[rows.length - 1].runwayAfter : co.runway_months;
  const fmtRun = (x: number) => (Number.isFinite(x) ? fmtMonths(x) : "∞");
  return (
    <Panel
      title="Hiring-plan impact"
      eyebrow="Headcount"
      icon={UserPlus}
      className={className}
      action={
        rows.length ? (
          <span className="text-[11px] tabular-nums text-subtle-foreground">+{fmtUSD(totalMonthly, { compact: true })}/mo</span>
        ) : undefined
      }
    >
      {rows.length === 0 ? (
        <NotAvailable label="No hiring plan published" />
      ) : (
        <ScrollX minWidth={560}>
          <table className="w-full border-collapse text-[12px]">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-[0.08em] text-subtle-foreground">
                <th className="py-1.5 pr-2 font-medium">Team</th>
                <th className="px-2 py-1.5 text-right font-medium">Roles</th>
                <th className="px-2 py-1.5 text-right font-medium">Monthly</th>
                <th className="px-2 py-1.5 font-medium">Start</th>
                <th className="py-1.5 pl-2 text-right font-medium">Runway after</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((r) => (
                <tr key={r.team} className="align-top">
                  <td className="py-2 pr-2">
                    <div className="font-medium text-foreground">{r.team}</div>
                    {r.dependency && (
                      <div className="line-clamp-1 max-w-[220px] text-[10.5px] text-subtle-foreground">{r.dependency}</div>
                    )}
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums text-foreground">{fmtInt(r.roles)}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-foreground">{fmtUSD(r.monthly_cost, { compact: true })}</td>
                  <td className="px-2 py-2 tabular-nums text-muted-foreground">{fmtDate(r.start_month)}</td>
                  <td className={cx("py-2 pl-2 text-right font-semibold tabular-nums", TONE_TEXT_CLASS[runwayTone(r.runwayAfter)])}>
                    {fmtRun(r.runwayAfter)}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-border-strong font-semibold">
                <td className="py-2 pr-2 text-foreground">All hires</td>
                <td className="px-2 py-2 text-right tabular-nums text-foreground">{fmtInt(totalRoles)}</td>
                <td className="px-2 py-2 text-right tabular-nums text-foreground">+{fmtUSD(totalMonthly, { compact: true })}</td>
                <td className="px-2 py-2 text-subtle-foreground">—</td>
                <td className={cx("py-2 pl-2 text-right tabular-nums", TONE_TEXT_CLASS[runwayTone(finalRunway)])}>
                  {fmtRun(finalRunway)}
                </td>
              </tr>
            </tfoot>
          </table>
        </ScrollX>
      )}
    </Panel>
  );
}

function BlockersPanel({ co, className }: { co: CompanyFinancials; className?: string }) {
  const audits = (co.audit_findings ?? []).map((a) => ({
    key: `audit-${a.id}`,
    severity: a.severity,
    title: `Audit · ${a.id}`,
    detail: `${a.area} — ${a.finding}`,
    meta: a.due ? `due ${fmtDate(a.due)}` : undefined,
  }));
  const sec = (co.security_incidents ?? []).map((s, i) => ({
    key: `sec-${s.date ?? i}`,
    severity: s.severity,
    title: `Security · ${fmtDate(s.date)}`,
    detail: `${s.status ? `${titleCase(s.status)} — ` : ""}${s.summary}`,
    meta: s.cash_risk != null ? `${fmtUSD(s.cash_risk, { compact: true })} at risk` : undefined,
  }));
  const sevRank: Record<Tone, number> = { risk: 0, warning: 1, info: 2, neutral: 3, positive: 4 };
  const items = [...audits, ...sec].sort((a, b) => sevRank[severityTone(a.severity)] - sevRank[severityTone(b.severity)]);
  return (
    <Panel title="Audit & security blockers" eyebrow="Risk & controls" icon={ShieldAlert} className={className}>
      {items.length === 0 ? (
        <NotAvailable label="No open audit or security items" />
      ) : (
        <ul className="divide-y divide-border">
          {items.map((it) => (
            <li key={it.key}>
              <RiskRow
                tone={severityTone(it.severity)}
                title={it.title}
                badge={<SeverityPill severity={it.severity} />}
                meta={it.meta}
                detail={it.detail}
              />
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function DecisionsPanel({ decisions, className }: { decisions: DecisionEvent[]; className?: string }) {
  const items = decisions.slice(0, 6);
  return (
    <Panel
      title="Recent decisions"
      eyebrow="Decision log"
      icon={History}
      className={className}
      action={
        <Link href="/activity" className="text-[11px] font-medium text-muted-foreground hover:text-foreground">
          View all
        </Link>
      }
    >
      {items.length === 0 ? (
        <p className="py-6 text-center text-[12px] text-subtle-foreground">No decisions recorded yet.</p>
      ) : (
        <ul className="divide-y divide-border">
          {items.map((d) => {
            const rel = avgReliability(d.reliability_scores);
            const source = d.source === "debate" ? "Committee decision" : d.source === "history" ? "Historical" : d.source;
            return (
              <li key={d._id}>
                <DecisionRow
                  title={d.title}
                  summary={d.summary}
                  decision={d.decision}
                  confidence={d.confidence}
                  source={source}
                  trailing={
                    rel != null ? (
                      <span className="text-[10.5px] tabular-nums text-subtle-foreground">{rel.toFixed(0)} reliability</span>
                    ) : undefined
                  }
                />
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

function CalibrationPanel({ co, className }: { co: CompanyFinancials; className?: string }) {
  const outcomes = co.decision_outcomes ?? [];
  const avgCal = avg(outcomes.map((o) => (o.calibration_score == null ? NaN : o.calibration_score)));
  return (
    <Panel
      title="Forecast calibration"
      eyebrow="Reliability"
      icon={Target}
      className={className}
      action={!Number.isNaN(avgCal) ? <TonePill tone={calTone(avgCal)}>{avgCal.toFixed(0)} avg</TonePill> : undefined}
    >
      {outcomes.length === 0 ? (
        <NotAvailable label="No decision outcomes recorded" />
      ) : (
        <ul className="divide-y divide-border">
          {outcomes.map((o, i) => (
            <li key={o.decision_id ?? i} className="flex items-start gap-2.5 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-[12px] font-medium text-foreground">{o.owner}</span>
                  <span className="shrink-0 text-[10.5px] text-subtle-foreground">{o.decision_id}</span>
                </div>
                <div className="mt-0.5 line-clamp-2 text-[11.5px] leading-relaxed text-muted-foreground">
                  <span className="text-subtle-foreground">pred:</span> {o.predicted}{" "}
                  <span className="text-subtle-foreground">→ actual:</span> {o.actual}
                </div>
                <div className="mt-0.5 text-[11px] text-foreground">{o.outcome}</div>
              </div>
              {o.calibration_score != null && (
                <span
                  className={cx(
                    "shrink-0 rounded px-1.5 py-0.5 text-[12px] font-semibold tabular-nums",
                    HEAT[calTone(o.calibration_score)],
                  )}
                >
                  {o.calibration_score}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function SavingsPanel({ vendors, className }: { vendors: Vendor[]; className?: string }) {
  const items = computeSavings(vendors);
  const total = items.reduce((s, i) => s + i.est, 0);
  return (
    <Panel
      title="Savings & opportunity queue"
      eyebrow="Efficiency"
      icon={PiggyBank}
      className={className}
      action={
        items.length ? (
          <span className="text-[11px] font-semibold tabular-nums text-positive">~{fmtUSD(total, { compact: true })}/yr</span>
        ) : undefined
      }
    >
      {items.length === 0 ? (
        <p className="py-6 text-center text-[12px] text-subtle-foreground">No savings opportunities flagged.</p>
      ) : (
        <ul className="divide-y divide-border">
          {items.map((s) => (
            <li key={s.name} className="flex items-start justify-between gap-3 py-2.5">
              <div className="min-w-0">
                <div className="truncate text-[12.5px] font-medium text-foreground">{s.name}</div>
                <div className="mt-0.5 line-clamp-2 text-[11.5px] text-muted-foreground">{s.detail}</div>
              </div>
              <div className="shrink-0 text-right">
                <div className="text-[13px] font-semibold tabular-nums text-positive">~{fmtUSD(s.est, { compact: true })}</div>
                <div className="text-[10px] text-subtle-foreground">est./yr</div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function OpexPanel({ co, className }: { co: CompanyFinancials; className?: string }) {
  const ox = co.opex_monthly;
  const entries: { k: string; v: number; tone: Tone }[] = ox
    ? [
        { k: "R&D", v: ox.rd, tone: "info" },
        { k: "Sales & marketing", v: ox.sm, tone: "warning" },
        { k: "G&A", v: ox.ga, tone: "neutral" },
      ]
    : [];
  const total = entries.reduce((s, e) => s + e.v, 0);
  return (
    <Panel
      title="Operating expense mix"
      eyebrow="Spend"
      icon={ChartPie}
      className={className}
      action={
        total ? (
          <span className="text-[11px] tabular-nums text-subtle-foreground">{fmtUSD(total, { compact: true })}/mo opex</span>
        ) : undefined
      }
    >
      {!ox ? (
        <NotAvailable label="No opex breakdown available" />
      ) : (
        <ul className="space-y-3">
          {entries.map((e) => (
            <li key={e.k}>
              <div className="flex items-center justify-between text-[12px]">
                <span className="font-medium text-foreground">{e.k}</span>
                <span className="tabular-nums text-muted-foreground">
                  {fmtUSD(e.v, { compact: true })} · {fmtPct(total ? e.v / total : 0)}
                </span>
              </div>
              <Bar value={e.v} max={total || 1} tone={e.tone} className="mt-1" />
            </li>
          ))}
          <li className="flex items-center justify-between border-t border-border pt-2 text-[11.5px]">
            <span className="text-subtle-foreground">Gross burn (incl. COGS)</span>
            <span className="font-semibold tabular-nums text-foreground">{fmtUSD(co.monthly_gross_burn, { compact: true })}/mo</span>
          </li>
        </ul>
      )}
    </Panel>
  );
}

// Months from the company's "as of" date to a renewal date (stable regardless of wall clock).
function monthsToRenewal(asOf: string | undefined, renewal: string): number {
  return monthsBetween(asOf ?? new Date().toISOString().slice(0, 10), renewal);
}

function DashboardSkeleton() {
  return (
    <div className="mx-auto w-full max-w-[1560px] px-4 py-5 sm:px-6">
      <div className="h-12 w-64 animate-pulse rounded bg-surface-muted" />
      <div className="mt-4 h-16 animate-pulse rounded-lg border border-border bg-surface" />
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-[94px] animate-pulse rounded-lg border border-border bg-surface" />
        ))}
      </div>
      <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-12">
        <div className="h-80 animate-pulse rounded-lg border border-border bg-surface lg:col-span-8" />
        <div className="h-80 animate-pulse rounded-lg border border-border bg-surface lg:col-span-4" />
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-44 animate-pulse rounded-lg border border-border bg-surface lg:col-span-4" />
        ))}
      </div>
    </div>
  );
}

function ErrorState({ err }: { err: string }) {
  return (
    <div className="mx-auto flex min-h-[60vh] w-full max-w-[640px] flex-col items-center justify-center px-6 text-center">
      <ShieldAlert className="h-8 w-8 text-risk" strokeWidth={1.5} />
      <h1 className="mt-3 text-[16px] font-semibold text-foreground">Finance service unreachable</h1>
      <p className="mt-1 text-[13px] leading-relaxed text-muted-foreground">
        The Atlas agent did not respond on the configured API. Confirm the service is running on port 8123 and Redis Stack
        is seeded.
      </p>
      <p className="mt-3 max-w-full truncate rounded bg-surface-muted px-2 py-1 text-[11px] text-subtle-foreground">{err}</p>
    </div>
  );
}
