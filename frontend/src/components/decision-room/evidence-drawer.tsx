"use client";

import { useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Banknote,
  Boxes,
  ChevronDown,
  Database,
  ExternalLink,
  FileJson2,
  FileSearch,
  FolderOpen,
  GitCompareArrows,
  Link2,
  Network,
  Radio,
  Search,
  type LucideProps,
} from "lucide-react";
import { cx } from "@/components/ui";
import { EASE_OUT_EXPO, motionDuration, springSnappy } from "@/components/motion/variants";
import { fmtInt, fmtMonths, fmtPct, fmtUSD, truncate } from "@/lib/format";
import type { CompanyFinancials, CouncilContext, PinnedEvidence, PolicyHit, Vendor } from "@/lib/types";
import { EmptyState, MetricTile, Panel, SectionLabel } from "./primitives";
import { classifyRedisSignal, redisSignalMeta, type RedisSignalKind } from "./redis-event-style";

interface OperationsSnapshot {
  sources?: Array<{
    source_type?: string;
    origin?: string;
    records?: number;
    status?: string;
    source_timestamp?: string;
    freshness_days?: number | null;
    confidence_score?: number;
    confidence_reasons?: string[];
    required_facts_missing?: string[];
  }>;
  confidence?: {
    score?: number;
    detail?: string;
    sources_imported?: number;
    sources_total?: number;
    validation_failure_count?: number;
    duplicate_count?: number;
    stale_source_count?: number;
    reconciliation_discrepancy_count?: number;
    required_missing_count?: number;
    confidence_reasons?: string[];
  };
  reconciliation?: {
    status?: string;
    generated_at?: string;
    counts_by_severity?: Record<string, number>;
    open_discrepancies?: number;
    top_discrepancies?: Array<{
      id?: string;
      kind?: string;
      severity?: string;
      title?: string;
      recommended_action?: string;
    }>;
    blockers?: string[];
  };
}

interface SourceDescriptor {
  label: string;
  value: string;
  href?: string;
  meta?: string[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asOperations(value: unknown): OperationsSnapshot | undefined {
  return isRecord(value) ? (value as OperationsSnapshot) : undefined;
}

function KindIcon({ kind, ...props }: { kind: RedisSignalKind } & LucideProps) {
  switch (kind) {
    case "redisjson":
      return <FileJson2 {...props} />;
    case "redisearch":
      return <Search {...props} />;
    case "vector":
      return <Network {...props} />;
    case "stream":
      return <Radio {...props} />;
    case "reconciliation":
      return <GitCompareArrows {...props} />;
    default:
      return <Database {...props} />;
  }
}

function sourceFromPin(pin: PinnedEvidence): SourceDescriptor {
  const kind = classifyRedisSignal(pin.kind, pin.source, pin.title, pin.detail);
  const excerpt = typeof pin.detail === "string" ? pin.detail.slice(0, 240) : undefined;
  return {
    label: redisSignalMeta(kind).label,
    value: pin.source ?? pin.title ?? pin.kind ?? "operator-pinned evidence",
    meta: [pin.at, excerpt, pin.kind].filter(Boolean) as string[],
  };
}

function SignalBadge({ kind, active }: { kind: RedisSignalKind; active: boolean }) {
  const meta = redisSignalMeta(kind);
  return (
    <span
      data-evidence-badge={kind}
      data-activity-pulse={active ? "active" : "idle"}
      className={cx(
        "redis-signal-glyph inline-flex h-6 shrink-0 items-center gap-1.5 rounded-md border px-1.5 text-[10px] font-semibold",
        meta.softClass,
        active && "redis-signal-glyph--active",
        active && `redis-signal-glyph--active-${kind}`,
      )}
    >
      <KindIcon kind={kind} className="h-3 w-3" strokeWidth={2.25} />
      {meta.label}
    </span>
  );
}

export function EvidenceDrawer({
  context,
  started,
  active = false,
  pinnedEvidence = [],
}: {
  context?: CouncilContext;
  started: boolean;
  active?: boolean;
  pinnedEvidence?: PinnedEvidence[];
}) {
  const prefersReducedMotion = useReducedMotion();
  const reduced = Boolean(prefersReducedMotion);
  const financials = context?.financials as CompanyFinancials | undefined;
  const vendors = (context?.vendors as Vendor[] | undefined) ?? [];
  const policies = (Array.isArray(context?.policies) ? (context?.policies as PolicyHit[]) : []) ?? [];
  const operations = asOperations(context?.operations);
  const reconciliation = operations?.reconciliation;
  const hasEvidence = Boolean(financials) || vendors.length > 0 || policies.length > 0 || Boolean(reconciliation) || pinnedEvidence.length > 0;
  const live = active && !reduced;

  const topVendors = [...vendors]
    .sort((a, b) => (b.monthly_cost ?? 0) - (a.monthly_cost ?? 0))
    .slice(0, 4);

  return (
    <Panel
      id="evidence-drawer"
      icon={FolderOpen}
      visualIcon="evidence"
      eyebrow="Redis-grounded"
      title="Evidence drawer"
      collapsible
      defaultOpen
    >
      {!hasEvidence ? (
        <EmptyState icon={FileSearch} visualIcon="evidence">
          {started
            ? "Loading the company financial system of record, vendor contracts, policy hits, and reconciliation state..."
            : "Intake loads financials, vendor contracts, policy precedents, and reconciliation facts that ground every position."}
        </EmptyState>
      ) : (
        <motion.div layout className="grid gap-3" data-evidence-groups>
          {financials && (
            <EvidenceGroup
              kind="redisjson"
              title={`${financials.name} financials`}
              icon={<Banknote className="h-3.5 w-3.5" strokeWidth={2} />}
              active={live}
              reduced={reduced}
              source={{
                label: "RedisJSON key",
                value: `atlas:company:${financials.id ?? financials.name}`,
                meta: [
                  `${financials.cash_history?.length ?? 0} cash history points`,
                  `${financials.cash_forecast?.length ?? 0} forecast rows`,
                  financials.updated ? `updated ${financials.updated}` : undefined,
                ].filter(Boolean) as string[],
              }}
            >
              <div className="grid gap-1.5 [grid-template-columns:repeat(2,minmax(0,1fr))]">
                <MetricTile label="Cash on hand" value={fmtUSD(financials.cash_on_hand, { compact: true })} />
                <MetricTile label="Runway" value={fmtMonths(financials.runway_months)} />
                <MetricTile label="Net burn / mo" value={fmtUSD(financials.monthly_net_burn, { compact: true })} />
                <MetricTile label="MRR" value={fmtUSD(financials.mrr, { compact: true })} />
                <MetricTile label="Growth MoM" value={fmtPct(financials.mrr_growth_mom, 1)} />
                <MetricTile label="Gross margin" value={fmtPct(financials.gross_margin)} />
              </div>
            </EvidenceGroup>
          )}

          {topVendors.length > 0 && (
            <EvidenceGroup
              kind="redisearch"
              title={`Vendor contracts (${vendors.length})`}
              icon={<Boxes className="h-3.5 w-3.5" strokeWidth={2} />}
              active={live}
              reduced={reduced}
              source={{
                label: "RediSearch query",
                value: "FT.SEARCH atlas:idx:vendors @status:* SORTBY monthly_cost DESC",
                meta: [`${vendors.length} vendor documents`, `${topVendors.length} displayed`],
              }}
            >
              <ul className="divide-y divide-border rounded-md border border-border bg-background">
                {topVendors.map((vendor) => (
                  <li key={vendor.id ?? vendor.name} className="flex min-h-[50px] items-center justify-between gap-2 px-2.5 py-1.5">
                    <div className="min-w-0">
                      <div className="truncate text-[12px] font-semibold">{vendor.name}</div>
                      <div className="truncate text-[10px] text-subtle-foreground">
                        {vendor.category}
                        {vendor.renewal_date ? ` · renews ${vendor.renewal_date}` : ""}
                      </div>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className="text-[12px] font-semibold tabular-nums">{fmtUSD(vendor.monthly_cost, { compact: true })}/mo</div>
                      <div className="text-[10px] text-subtle-foreground">{vendor.status}</div>
                    </div>
                  </li>
                ))}
              </ul>
            </EvidenceGroup>
          )}

          {policies.length > 0 && (
            <EvidenceGroup
              kind="vector"
              title={`Policy & precedent hits (${policies.length})`}
              icon={<FileSearch className="h-3.5 w-3.5" strokeWidth={2} />}
              active={live}
              reduced={reduced}
              source={{
                label: "Vector RAG query",
                value: "FT.SEARCH atlas:idx:policies KNN policy/precedent embeddings",
                meta: [`${policies.length} retrieved hits`, "score shown when supplied"],
              }}
            >
              <ul className="grid gap-1.5">
                {policies.slice(0, 4).map((hit, index) => {
                  const title = hit.title ?? hit.name ?? hit.source ?? `Policy ${index + 1}`;
                  const body = hit.summary ?? hit.text ?? hit.content;
                  const score = typeof hit.score === "number" ? hit.score : typeof hit.distance === "number" ? hit.distance : undefined;
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
            </EvidenceGroup>
          )}

          {reconciliation && (
            <EvidenceGroup
              kind="reconciliation"
              title="Reconciliation"
              icon={<GitCompareArrows className="h-3.5 w-3.5" strokeWidth={2} />}
              active={live}
              reduced={reduced}
              source={{
                label: "Reconciliation record",
                value: "atlas:reconciliation:latest + atlas:stream:reconciliation",
                meta: [
                  reconciliation.generated_at ? `generated ${reconciliation.generated_at}` : undefined,
                  `${operations?.sources?.length ?? 0} imported sources`,
                ].filter(Boolean) as string[],
              }}
            >
              <div className="grid gap-1.5 [grid-template-columns:repeat(2,minmax(0,1fr))]">
                <MetricTile label="Status" value={reconciliation.status ?? "unknown"} />
                <MetricTile label="Open issues" value={String(reconciliation.open_discrepancies ?? 0)} />
                <MetricTile label="Data confidence" value={operations?.confidence?.score == null ? "—" : `${fmtInt(operations.confidence.score)}%`} />
                <MetricTile label="Sources" value={`${operations?.confidence?.sources_imported ?? operations?.sources?.length ?? 0}/${operations?.confidence?.sources_total ?? operations?.sources?.length ?? 0}`} />
                <MetricTile label="Duplicates" value={fmtInt(operations?.confidence?.duplicate_count ?? 0)} />
                <MetricTile label="Missing facts" value={fmtInt(operations?.confidence?.required_missing_count ?? 0)} />
              </div>
              {operations?.confidence?.detail && (
                <p className="mt-2 break-words text-[11px] leading-relaxed text-muted-foreground">
                  {operations.confidence.detail}
                </p>
              )}
              {(operations?.confidence?.confidence_reasons ?? []).length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {(operations?.confidence?.confidence_reasons ?? []).slice(0, 4).map((reason) => (
                    <span key={reason} className="rounded border border-warning/25 bg-warning-bg/25 px-1.5 py-0.5 text-[10px] font-semibold text-warning">
                      {reason}
                    </span>
                  ))}
                </div>
              )}
              {(operations?.sources ?? []).length > 0 && (
                <ul className="mt-2 grid gap-1.5">
                  {(operations?.sources ?? []).slice(0, 4).map((source) => (
                    <li key={`${source.source_type}-${source.source_timestamp ?? source.records}`} className="rounded border border-border bg-background px-2 py-1.5">
                      <div className="flex min-w-0 items-center justify-between gap-2">
                        <span className="truncate text-[11px] font-semibold">{source.source_type?.replace(/_/g, " ") ?? "source"}</span>
                        <span className="shrink-0 text-[10px] font-semibold tabular-nums text-subtle-foreground">
                          {source.confidence_score == null ? "unscored" : `${fmtInt(source.confidence_score)}%`}
                        </span>
                      </div>
                      <div className="mt-0.5 line-clamp-1 text-[10px] text-muted-foreground">
                        {source.confidence_reasons?.[0] ??
                          (source.freshness_days == null ? "Freshness unknown" : `${source.freshness_days}d old`)}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              {(reconciliation.top_discrepancies ?? []).length > 0 && (
                <ul className="mt-2 grid gap-1.5">
                  {(reconciliation.top_discrepancies ?? []).slice(0, 3).map((item) => (
                    <li key={item.id ?? item.title} className="rounded border border-warning/20 bg-warning-bg/20 px-2 py-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-[11px] font-semibold">{item.title ?? item.kind ?? "Reconciliation issue"}</span>
                        {item.severity && <span className="shrink-0 text-[10px] font-semibold uppercase text-warning">{item.severity}</span>}
                      </div>
                      {item.recommended_action && (
                        <p className="mt-0.5 break-words text-[10px] leading-relaxed text-muted-foreground">
                          {truncate(item.recommended_action, 120)}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </EvidenceGroup>
          )}

          {pinnedEvidence.length > 0 && (
            <EvidenceGroup
              kind="stream"
              title={`Pinned evidence (${pinnedEvidence.length})`}
              icon={<Link2 className="h-3.5 w-3.5" strokeWidth={2} />}
              active={live}
              reduced={reduced}
              source={{
                label: "Operator evidence stream",
                value: "atlas:commands:pinned_evidence",
                meta: [`${pinnedEvidence.length} board-record pins`],
              }}
            >
              <ul className="grid gap-1.5">
                {pinnedEvidence.slice(0, 4).map((pin) => {
                  const kind = classifyRedisSignal(pin.kind, pin.source, pin.title, pin.detail);
                  const meta = redisSignalMeta(kind);
                  return (
                    <li key={pin.id} className="rounded-md border border-border bg-background px-2.5 py-1.5">
                      <div className="flex items-center gap-2">
                        <span className={cx("h-1.5 w-1.5 shrink-0 rounded-full", meta.accentClass)} />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[12px] font-semibold">{pin.title ?? pin.kind}</div>
                          {pin.detail && <div className="truncate text-[10px] text-subtle-foreground">{pin.detail}</div>}
                        </div>
                      </div>
                      <SourceReveal source={sourceFromPin(pin)} reduced={reduced} />
                    </li>
                  );
                })}
              </ul>
            </EvidenceGroup>
          )}
        </motion.div>
      )}
    </Panel>
  );
}

function EvidenceGroup({
  kind,
  title,
  icon,
  active,
  reduced,
  source,
  children,
}: {
  kind: RedisSignalKind;
  title: string;
  icon: ReactNode;
  active: boolean;
  reduced: boolean;
  source: SourceDescriptor;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(true);
  const meta = redisSignalMeta(kind);

  return (
    <motion.section
      layout="position"
      data-evidence-kind={kind}
      data-activity-pulse={active ? "active" : "idle"}
      className={cx("redis-evidence-group rounded-md border bg-surface/80", meta.borderClass)}
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduced ? { duration: motionDuration.instant } : springSnappy}
    >
      <div className="flex min-w-0 items-center gap-2 px-2.5 py-2">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          aria-expanded={open}
        >
          <span className="inline-flex min-w-0 items-center gap-1.5">
            {icon}
            <SectionLabel className="truncate">{title}</SectionLabel>
          </span>
          <ChevronDown
            className={cx("ml-auto h-4 w-4 shrink-0 text-subtle-foreground transition-transform", !open && "-rotate-90")}
            strokeWidth={2}
          />
        </button>
        <SignalBadge kind={kind} active={active} />
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            layout="position"
            initial={reduced ? { opacity: 0 } : { opacity: 0, height: 0 }}
            animate={reduced ? { opacity: 1 } : { opacity: 1, height: "auto" }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, height: 0 }}
            transition={reduced ? { duration: motionDuration.instant } : { duration: motionDuration.quick, ease: EASE_OUT_EXPO }}
            className="overflow-hidden border-t border-border/70"
          >
            <div className="grid gap-2 p-2.5">
              <SourceReveal source={source} reduced={reduced} />
              {children}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.section>
  );
}

function SourceReveal({ source, reduced }: { source: SourceDescriptor; reduced: boolean }) {
  const [open, setOpen] = useState(false);

  return (
    <div data-source-reveal={open ? "open" : "closed"}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="inline-flex max-w-full items-center gap-1 rounded border border-border bg-background px-2 py-0.5 text-[10px] font-semibold text-subtle-foreground transition-colors hover:bg-surface-muted hover:text-foreground"
      >
        <ExternalLink className="h-3 w-3 shrink-0" strokeWidth={2.2} />
        <span className="truncate">{open ? "Hide source link" : "Reveal source link"}</span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: -2 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: -2 }}
            transition={reduced ? { duration: motionDuration.instant } : { duration: motionDuration.fast, ease: EASE_OUT_EXPO }}
            className="mt-1 rounded border border-border bg-background px-2 py-1.5"
          >
            <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-subtle-foreground">{source.label}</div>
            {source.href ? (
              <a href={source.href} target="_blank" rel="noreferrer" className="mt-0.5 block break-all text-[11px] text-info hover:underline">
                {source.value}
              </a>
            ) : (
              <code className="mt-0.5 block break-all rounded bg-surface-muted px-1.5 py-1 text-[10px] text-muted-foreground">
                {source.value}
              </code>
            )}
            {(source.meta ?? []).length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {(source.meta ?? []).map((item) => (
                  <span key={item} className="rounded border border-border bg-surface px-1.5 py-0.5 text-[10px] text-subtle-foreground">
                    {item}
                  </span>
                ))}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
