"use client";

import { Banknote, Boxes, Database, FileSearch, FolderOpen } from "lucide-react";
import { fmtMonths, fmtPct, fmtUSD, truncate } from "@/lib/format";
import type { CompanyFinancials, CouncilContext, PolicyHit, Vendor } from "@/lib/types";
import { EmptyState, MetricTile, Panel, SectionLabel } from "./primitives";

function GroundedBadge({ source }: { source: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded border border-info/20 bg-info-bg px-1.5 py-0.5 text-[10px] font-semibold text-info">
      <Database className="h-3 w-3" strokeWidth={2.25} />
      {source}
    </span>
  );
}

export function EvidenceDrawer({ context, started }: { context?: CouncilContext; started: boolean }) {
  const financials = context?.financials as CompanyFinancials | undefined;
  const vendors = (context?.vendors as Vendor[] | undefined) ?? [];
  const policies = (Array.isArray(context?.policies) ? (context?.policies as PolicyHit[]) : []) ?? [];
  const hasEvidence = Boolean(financials) || vendors.length > 0 || policies.length > 0;

  const topVendors = [...vendors]
    .sort((a, b) => (b.monthly_cost ?? 0) - (a.monthly_cost ?? 0))
    .slice(0, 4);

  return (
    <Panel
      icon={FolderOpen}
      eyebrow="Redis-grounded"
      title="Evidence drawer"
      collapsible
      defaultOpen={false}
    >
      {!hasEvidence ? (
        <EmptyState icon={FileSearch}>
          {started
            ? "Loading the company financial system of record, vendor contracts, and policy hits…"
            : "Intake loads financials, vendor contracts, and policy precedents that ground every position."}
        </EmptyState>
      ) : (
        <div className="grid gap-3">
          {financials && (
            <section>
              <div className="flex items-center justify-between gap-2">
                <SectionLabel>
                  <span className="inline-flex items-center gap-1.5">
                    <Banknote className="h-3.5 w-3.5" strokeWidth={2} />
                    {financials.name} financials
                  </span>
                </SectionLabel>
                <GroundedBadge source="RedisJSON" />
              </div>
              <div className="mt-2 grid gap-1.5 [grid-template-columns:repeat(2,minmax(0,1fr))] sm:[grid-template-columns:repeat(3,minmax(0,1fr))]">
                <MetricTile label="Cash on hand" value={fmtUSD(financials.cash_on_hand, { compact: true })} />
                <MetricTile label="Runway" value={fmtMonths(financials.runway_months)} />
                <MetricTile label="Net burn / mo" value={fmtUSD(financials.monthly_net_burn, { compact: true })} />
                <MetricTile label="MRR" value={fmtUSD(financials.mrr, { compact: true })} />
                <MetricTile label="Growth MoM" value={fmtPct(financials.mrr_growth_mom, 1)} />
                <MetricTile label="Gross margin" value={fmtPct(financials.gross_margin)} />
              </div>
            </section>
          )}

          {topVendors.length > 0 && (
            <section>
              <div className="flex items-center justify-between gap-2">
                <SectionLabel>
                  <span className="inline-flex items-center gap-1.5">
                    <Boxes className="h-3.5 w-3.5" strokeWidth={2} />
                    Vendor contracts ({vendors.length})
                  </span>
                </SectionLabel>
                <GroundedBadge source="RediSearch" />
              </div>
              <ul className="mt-2 divide-y divide-border rounded-md border border-border bg-background">
                {topVendors.map((vendor) => (
                  <li key={vendor.id ?? vendor.name} className="flex items-center justify-between gap-2 px-2.5 py-1.5">
                    <div className="min-w-0">
                      <div className="truncate text-[12px] font-semibold">{vendor.name}</div>
                      <div className="truncate text-[10px] text-subtle-foreground">{vendor.category}</div>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className="text-[12px] font-semibold tabular-nums">{fmtUSD(vendor.monthly_cost, { compact: true })}/mo</div>
                      <div className="text-[10px] text-subtle-foreground">{vendor.status}</div>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {policies.length > 0 && (
            <section>
              <div className="flex items-center justify-between gap-2">
                <SectionLabel>
                  <span className="inline-flex items-center gap-1.5">
                    <FileSearch className="h-3.5 w-3.5" strokeWidth={2} />
                    Policy &amp; precedent hits ({policies.length})
                  </span>
                </SectionLabel>
                <GroundedBadge source="Vector RAG" />
              </div>
              <ul className="mt-2 grid gap-1.5">
                {policies.slice(0, 4).map((hit, index) => {
                  const title = hit.title ?? hit.name ?? hit.source ?? `Policy ${index + 1}`;
                  const body = hit.summary ?? hit.text ?? hit.content;
                  const score = typeof hit.score === "number" ? hit.score : undefined;
                  return (
                    <li key={hit.id ?? `${title}-${index}`} className="rounded-md border border-border bg-background px-2.5 py-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <div className="truncate text-[12px] font-semibold">{title}</div>
                        {score != null && (
                          <span className="shrink-0 text-[10px] font-semibold tabular-nums text-subtle-foreground">{score.toFixed(2)}</span>
                        )}
                      </div>
                      {body && <p className="mt-0.5 break-words text-[11px] leading-relaxed text-muted-foreground">{truncate(String(body), 160)}</p>}
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
        </div>
      )}
    </Panel>
  );
}
