"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowUpRight, Loader2, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { broadcastDemoReset, onDemoReset } from "@/lib/demo-reset";
import { useLiveFeed } from "@/lib/use-live-feed";
import { KpiHero } from "@/components/overview/kpi-hero";
import { CashChart } from "@/components/overview/cash-chart";
import { OpexBreakdown, PipelinePanel, RecentRulings, Section } from "@/components/overview/overview-panels";
import type { CompanyFinancials, DecisionEvent } from "@/lib/types";

/*
  Executive Overview — the front page of the ledger. Everything here is read
  straight from the live system of record (`/api/company`, `/api/decisions`)
  and refreshes the moment a council run concludes, via the SSE bridge over
  Redis pub/sub (`/api/live`), with a slow poll as a safety net.
*/

const POLL_MS = 60_000;

export default function OverviewPage() {
  const [company, setCompany] = useState<CompanyFinancials | null>(null);
  const [decisions, setDecisions] = useState<DecisionEvent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [demoLoading, setDemoLoading] = useState(false);

  const refresh = useCallback(async () => {
    const [companyResult, decisionsResult] = await Promise.allSettled([api.company(), api.decisions()]);
    if (companyResult.status === "fulfilled") {
      const record = companyResult.value;
      setCompany(record && record.name ? record : null);
    }
    if (decisionsResult.status === "fulfilled" && Array.isArray(decisionsResult.value)) {
      setDecisions(decisionsResult.value);
    }
    setLoaded(true);
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(() => void refresh(), 0);
    const interval = window.setInterval(() => void refresh(), POLL_MS);
    const offReset = onDemoReset(() => void refresh());
    return () => {
      window.clearTimeout(timeout);
      window.clearInterval(interval);
      offReset();
    };
  }, [refresh]);

  // The moment a council ruling persists, the graph publishes to
  // atlas:dashboard — refetch immediately instead of waiting for the poll.
  useLiveFeed(["decision", "reliability"], () => void refresh());

  const onLoadDemo = useCallback(async () => {
    if (demoLoading) return;
    setDemoLoading(true);
    try {
      const result = await api.resetDemo();
      broadcastDemoReset(result);
    } catch (err) {
      console.error("Live demo reseed failed", err);
    } finally {
      await refresh();
      setDemoLoading(false);
    }
  }, [demoLoading, refresh]);

  return (
    <div className="mx-auto w-full max-w-[1280px] px-4 py-5 md:px-8 md:py-7">
      {!loaded ? (
        <div className="grid gap-3">
          <div className="h-[230px] animate-pulse rounded-lg border border-border bg-surface" />
          <div className="grid gap-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <div className="h-[330px] animate-pulse rounded-lg border border-border bg-surface" />
            <div className="h-[330px] animate-pulse rounded-lg border border-border bg-surface" />
          </div>
        </div>
      ) : !company ? (
        <EmptyLedger demoLoading={demoLoading} onLoadDemo={onLoadDemo} />
      ) : (
        <div className="grid gap-3">
          <div className="reveal">
            <KpiHero company={company} />
          </div>

          <div className="grid gap-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <Section
              folio="01"
              title="Cash & runway"
              hint="History solid · forecast dashed · downside dotted"
              className="reveal reveal-d1"
            >
              <CashChart company={company} />
            </Section>
            <Section
              folio="02"
              title="Recent rulings"
              hint="Live from the decision stream"
              className="reveal reveal-d2"
              action={
                <Link
                  href="/decisions"
                  className="inline-flex items-center gap-1 text-[11.5px] font-semibold text-accent transition-opacity hover:opacity-80"
                >
                  Convene <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={2.25} />
                </Link>
              }
            >
              <RecentRulings decisions={decisions} />
            </Section>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Section folio="03" title="Operating spend" hint="Top categories, monthly" className="reveal reveal-d3">
              <OpexBreakdown company={company} />
            </Section>
            <Section folio="04" title="Pipeline by stage" hint="Weighted ARR" className="reveal reveal-d4">
              <PipelinePanel company={company} />
            </Section>
          </div>
        </div>
      )}
    </div>
  );
}

function EmptyLedger({ demoLoading, onLoadDemo }: { demoLoading: boolean; onLoadDemo: () => Promise<void> }) {
  return (
    <section className="command-surface command-surface--feature relative overflow-hidden px-6 py-14 md:px-12 md:py-20">
      <div
        aria-hidden
        className="pointer-events-none absolute -top-40 left-1/2 h-[420px] w-[720px] -translate-x-1/2"
        style={{
          background: "radial-gradient(closest-side, color-mix(in srgb, var(--gilt) 9%, transparent), transparent 72%)",
        }}
      />
      <div className="relative max-w-[640px]">
        <span className="kicker kicker--gilt">Executive Overview</span>
        <h1 className="headline mt-3 text-[36px] font-medium text-foreground md:text-[48px]">The ledger is empty.</h1>
        <p className="lede mt-3">
          Atlas reconstructs your company&apos;s financial position — cash, burn, runway, pipeline — from the
          books you upload. Load the demo company for a full live dataset, or bring your own.
        </p>
        <div className="mt-7 flex flex-wrap items-center gap-2.5">
          <button
            type="button"
            disabled={demoLoading}
            onClick={() => void onLoadDemo()}
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-accent bg-accent px-5 text-[13px] font-semibold text-accent-foreground transition-all hover:brightness-105 disabled:opacity-50"
          >
            {demoLoading ? <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.25} /> : <Sparkles className="h-4 w-4" strokeWidth={2.25} />}
            {demoLoading ? "Seeding live demo company…" : "Load demo company"}
          </button>
          <Link
            href="/dashboard"
            className="inline-flex h-10 items-center gap-1.5 rounded-lg border border-border bg-surface px-5 text-[13px] font-semibold text-foreground transition-colors hover:bg-surface-muted"
          >
            Upload my books <ArrowUpRight className="h-4 w-4" strokeWidth={2.25} />
          </Link>
        </div>
      </div>
    </section>
  );
}
