"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Database,
  FileWarning,
  Loader2,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import { api } from "@/lib/api";
import { broadcastDemoReset } from "@/lib/demo-reset";
import type { ConnectorInventory, ConnectorStatus, DemoResetResponse, ReconciliationReport } from "@/lib/types";
import { fmtInt } from "@/lib/format";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { CollapseIn } from "@/components/motion/presence";
import { Stagger, StaggerItem } from "@/components/motion/stagger";
import {
  EASE_OUT_EXPO,
  hoverLift,
  hoverLiftStrong,
  motionDuration,
  pressTap,
  springBar,
  springSnappy,
  staggerDelay,
  transitionFadeFast,
} from "@/components/motion/variants";
import { AtlasIcon, type AtlasIconName } from "@/components/atlas-icon";
import { cx, StatusDot } from "@/components/ui";
import { TonePill, type Tone } from "@/components/dashboard";

const FILE_LABELS: Record<string, string> = {
  ledger: "Ledger",
  invoices: "Invoices",
  vendor_export: "Vendors",
  crm_opportunities: "Sales pipeline",
  headcount_plan: "Hiring plan",
  security_evidence: "Security notes",
  board_policy: "Board rules",
};

const FILE_ICONS: Record<string, AtlasIconName> = {
  ledger: "runway",
  invoices: "reconcile",
  vendor_export: "evidence",
  crm_opportunities: "scenario",
  headcount_plan: "council",
  security_evidence: "risk",
  board_policy: "memo",
};

function loaded(status?: string): boolean {
  return status === "imported" || status === "partial" || status === "skipped_unchanged";
}

type FileCardState = "loaded" | "partial" | "review" | "error" | "stale" | "missing";

const STATE_LABELS: Record<FileCardState, string> = {
  loaded: "Loaded",
  partial: "Partial",
  review: "Needs review",
  error: "Error",
  stale: "Stale",
  missing: "Missing",
};

const STATE_TONES: Record<FileCardState, Tone> = {
  loaded: "positive",
  partial: "warning",
  review: "warning",
  error: "risk",
  stale: "warning",
  missing: "neutral",
};

const STATE_BACKGROUND: Record<FileCardState, string> = {
  loaded: "bg-positive-bg/35",
  partial: "bg-warning-bg/38",
  review: "bg-warning-bg/32",
  error: "bg-risk-bg/40",
  stale: "bg-warning-bg/28",
  missing: "bg-surface",
};

const FRESHNESS_STALE_DAYS = 21;

function freshness(connector: ConnectorStatus): { label: string; detail: string; days: number | null; stale: boolean } {
  const raw = connector.source_timestamp ?? connector.imported_at ?? null;
  if (!raw) {
    return loaded(connector.status)
      ? { label: "Freshness unknown", detail: "No timestamp supplied", days: null, stale: false }
      : { label: "No source yet", detail: "Waiting for upload", days: null, stale: false };
  }
  const timestamp = new Date(raw);
  if (Number.isNaN(timestamp.getTime())) {
    return { label: "Freshness unknown", detail: "Timestamp could not be parsed", days: null, stale: false };
  }
  const days = Math.max(0, Math.floor((Date.now() - timestamp.getTime()) / 86_400_000));
  const label = days === 0 ? "Updated today" : days === 1 ? "Updated 1 day ago" : `Updated ${days} days ago`;
  return {
    label,
    detail: timestamp.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }),
    days,
    stale: days > FRESHNESS_STALE_DAYS,
  };
}

function summaryNumber(summary: Record<string, unknown> | undefined, key: string): number {
  const value = summary?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function invoiceMessinessSummary(connector: ConnectorStatus): Record<string, unknown> | undefined {
  if (connector.connector_id !== "invoices") return undefined;
  return connector.messiness_summary;
}

function invoiceMessinessIssueCount(connector: ConnectorStatus): number {
  const summary = invoiceMessinessSummary(connector);
  return summaryNumber(summary, "issue_count");
}

function formatUsd(value: number): string {
  return new Intl.NumberFormat(undefined, { currency: "USD", maximumFractionDigits: 0, style: "currency" }).format(value);
}

function invoiceMessinessBadges(connector: ConnectorStatus): { label: string; tone: Tone }[] {
  const summary = invoiceMessinessSummary(connector);
  if (!summary) return [];
  const badges = [
    { label: `${fmtInt(summaryNumber(summary, "partial_payment_count"))} partial`, tone: "warning" as Tone },
    { label: `${fmtInt(summaryNumber(summary, "overdue_count"))} overdue`, tone: "risk" as Tone },
    { label: `${fmtInt(summaryNumber(summary, "disputed_count"))} disputed`, tone: "risk" as Tone },
    { label: `${fmtInt(summaryNumber(summary, "missing_due_date_count"))} missing due`, tone: "warning" as Tone },
    { label: `${fmtInt(summaryNumber(summary, "non_usd_count"))} FX`, tone: "neutral" as Tone },
    { label: `${fmtInt(summaryNumber(summary, "duplicate_vendor_name_count"))} name drift`, tone: "warning" as Tone },
  ];
  return badges.filter((badge) => !badge.label.startsWith("0 "));
}

function confidenceTone(score?: number | null): Tone {
  if (score == null) return "neutral";
  if (score >= 85) return "positive";
  if (score >= 65) return "warning";
  return "risk";
}

function fileState(connector: ConnectorStatus): FileCardState {
  if (connector.status === "error") return "error";
  if (connector.status === "partial") return "partial";
  if (
    loaded(connector.status) &&
    (connector.reconciliation_status === "needs_review" ||
      (connector.rejected_count ?? 0) > 0 ||
      (connector.duplicate_count ?? 0) > 0)
  ) {
    return "review";
  }
  if (loaded(connector.status) && (connector.confidence_score ?? 100) < 85) return "review";
  if (loaded(connector.status) && (connector.required_facts_missing?.length ?? 0) > 0) return "review";
  if (loaded(connector.status) && invoiceMessinessIssueCount(connector) > 0) return "review";
  if (loaded(connector.status) && freshness(connector).stale) return "stale";
  if (loaded(connector.status)) return "loaded";
  return "missing";
}

function qualitySummary(connector: ConnectorStatus): string | null {
  const rejected = connector.rejected_count ?? 0;
  const duplicates = connector.duplicate_count ?? 0;
  const invoiceBadges = invoiceMessinessBadges(connector);
  const notes = [
    rejected ? `${fmtInt(rejected)} rejected` : null,
    duplicates ? `${fmtInt(duplicates)} duplicate${duplicates === 1 ? "" : "s"}` : null,
    invoiceBadges.length ? invoiceBadges.map((badge) => badge.label).join(" · ") : null,
    connector.confidence_reasons?.[0] ?? null,
  ].filter(Boolean);
  return notes.length ? notes.join(" · ") : null;
}

function stateSummary(connector: ConnectorStatus, state: FileCardState): string {
  if (state === "error") return connector.blockers?.[0] ?? "Import failed";
  if (state === "partial") return qualitySummary(connector) ?? connector.blockers?.[0] ?? "Loaded with validation notes";
  if (state === "review") return qualitySummary(connector) ?? "Confidence or reconciliation found issues to review";
  if (state === "stale") return "Loaded source is outside the freshness window";
  if (state === "loaded") return `${fmtInt(connector.record_count)} rows ready`;
  return "No upload yet";
}

export default function SettingsPage() {
  const reduced = Boolean(useReducedMotion());
  const [inventory, setInventory] = useState<ConnectorInventory | null>(null);
  const [reconciliation, setReconciliation] = useState<ReconciliationReport | null>(null);
  const [resetting, setResetting] = useState(false);
  const [result, setResult] = useState<DemoResetResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [connectors, report] = await Promise.all([api.connectors(), api.reconciliation()]);
      setInventory(connectors);
      setReconciliation(report);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  async function resetDemo() {
    setResetting(true);
    try {
      const payload = await api.resetDemo();
      setResult(payload);
      setInventory({ mode: "strict-live", connectors: payload.connectors, confidence: payload.confidence });
      setReconciliation({ status: "not_run", detail: "No file check has been run yet.", discrepancies: [] });
      setExpanded(null);
      setError(null);
      broadcastDemoReset(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setResetting(false);
    }
  }

  const connectors = useMemo(() => inventory?.connectors ?? [], [inventory?.connectors]);
  const loadedCount = connectors.filter((connector) => loaded(connector.status)).length;
  const total = inventory?.confidence.sources_total ?? (connectors.length || 7);
  const fileStates = useMemo(() => connectors.map((connector) => fileState(connector)), [connectors]);
  const partialCount = fileStates.filter((state) => state === "partial").length;
  const reviewCount = fileStates.filter((state) => state === "review").length;
  const errorCount = fileStates.filter((state) => state === "error").length;
  const staleCount = fileStates.filter((state) => state === "stale").length;
  const missingCount = fileStates.filter((state) => state === "missing").length;
  const confidence = inventory?.confidence ?? reconciliation?.confidence ?? null;
  const lowConfidenceCount = connectors.filter((connector) => loaded(connector.status) && (connector.confidence_score ?? 100) < 85).length;
  const missingFactCount =
    confidence?.required_missing_count ??
    connectors.reduce((sum, connector) => sum + (connector.required_facts_missing?.length ?? 0), 0);
  const issues = (reconciliation?.discrepancies ?? []).filter((item) => item.severity !== "info").length;
  const deleted = result ? Object.values(result.deleted).reduce((sum, count) => sum + count, 0) : 0;

  return (
    <main className="mx-auto flex min-h-full w-full max-w-[980px] flex-col gap-4 px-4 py-5 sm:px-6">
      <Stagger className="flex flex-col gap-4">
      <StaggerItem className="border-b border-border pb-4">
        <div className="flex items-start gap-3">
          <AtlasIcon name="memory" size="lg" className="mt-1 hidden sm:inline-grid" />
          <div className="min-w-0">
            <h1 className="font-display text-[28px] font-medium tracking-tight">Demo reset</h1>
          </div>
        </div>
      </StaggerItem>

      <CollapseIn show={Boolean(error)}>
        <div className="rounded-lg border border-risk/20 bg-risk-bg px-3 py-2 text-[13px] font-medium text-risk">
          {error}
        </div>
      </CollapseIn>

      <StaggerItem className="grid gap-3 sm:grid-cols-3">
        <SettingStat label="Files loaded" value={`${loadedCount}/${total}`} numericValue={loadedCount} total={total} icon="upload" />
        <SettingStat
          label="Data confidence"
          value={confidence ? `${fmtInt(confidence.score)}%` : "0%"}
          numericValue={confidence?.score ?? 0}
          total={100}
          icon={confidenceTone(confidence?.score) === "risk" ? "risk" : "health"}
        />
        <SettingStat label="Review items" value={fmtInt(issues)} numericValue={issues} icon={issues ? "risk" : "reconcile"} />
      </StaggerItem>

      <StaggerItem>
      <section className="command-surface overflow-hidden" data-settings-loaded-files="true">
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold">Loaded files</h2>
          </div>
          <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
            <MiniCounter label="low confidence" value={lowConfidenceCount} tone={lowConfidenceCount ? "warning" : "positive"} />
            <MiniCounter label="partial" value={partialCount} tone="warning" />
            <MiniCounter label="review" value={reviewCount} tone="warning" />
            <MiniCounter label="stale" value={staleCount} tone="warning" />
            <MiniCounter label="errors" value={errorCount} tone="risk" />
            <MiniCounter label="missing facts" value={missingFactCount} tone={missingFactCount ? "risk" : "positive"} />
            <MiniCounter label="missing" value={missingCount} tone="neutral" />
          </div>
        </div>
        <div className="grid gap-2 p-4 sm:grid-cols-2">
          {connectors.map((connector, index) => (
            <LoadedFileCard
              key={connector.connector_id}
              connector={connector}
              index={index}
              expanded={expanded === connector.connector_id}
              onToggle={() => setExpanded((current) => (current === connector.connector_id ? null : connector.connector_id))}
            />
          ))}
        </div>
      </section>
      </StaggerItem>

      <div className="flex-1" />

      <StaggerItem>
      <motion.section
        className={cx(
          "rounded-lg border bg-surface p-4 shadow-sm",
          result ? "border-positive/25" : "border-risk/20",
        )}
        data-reset-status={result?.status ?? (resetting ? "resetting" : "idle")}
        layout
        transition={springSnappy}
        animate={
          resetting
            ? {
                boxShadow: [
                  "var(--shadow-soft)",
                  "0 0 0 1px color-mix(in srgb, var(--risk) 22%, transparent), 0 14px 38px color-mix(in srgb, var(--risk) 10%, transparent)",
                ],
              }
            : { boxShadow: "var(--shadow-soft)" }
        }
      >
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <AtlasIcon name={result ? "reconcile" : "risk"} size="sm" className="atlas-icon-badge--quiet" />
              {result ? (
                <CheckCircle2 className="h-4 w-4 text-positive" strokeWidth={2} />
              ) : (
                <AlertTriangle className="h-4 w-4 text-risk" strokeWidth={2} />
              )}
              <h2 className="text-[15px] font-semibold">Full demo reset</h2>
            </div>
            <AnimatePresence mode="wait">
              {resetting ? (
                <motion.div
                  key="resetting"
                  className="mt-3 max-w-sm"
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  transition={transitionFadeFast}
                >
                  <div className="flex items-center justify-between text-[11px] font-medium text-risk">
                    <span>Resetting live demo state</span>
                    <span>Working</span>
                  </div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-risk-bg">
                    <motion.div
                      className="h-full rounded-full bg-risk"
                      animate={{ x: ["-70%", "110%"] }}
                      transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut" }}
                      style={{ width: "58%" }}
                    />
                  </div>
                </motion.div>
              ) : result ? (
                <motion.div
                  key="result"
                  className="mt-3 inline-flex items-center gap-2 rounded-lg border border-positive/20 bg-positive-bg px-3 py-2 text-[12px] font-medium text-positive"
                  initial={{ opacity: 0, y: 8, scale: 0.98 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: -4 }}
                  transition={springSnappy}
                >
                  <ShieldCheck className="h-4 w-4 shrink-0" strokeWidth={2} />
                  Full reset complete — {fmtInt(deleted)} Redis key{deleted === 1 ? "" : "s"} cleared and demo reseeded.
                </motion.div>
              ) : null}
            </AnimatePresence>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Link
              href="/dashboard"
              className="inline-flex h-9 items-center justify-center rounded-lg border border-border bg-surface px-3 text-[12px] font-semibold text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground"
            >
              Upload files
            </Link>
            <motion.button
              type="button"
              onClick={() => void resetDemo()}
              disabled={resetting}
              className={cx(
                "inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-risk px-3 text-[12px] font-semibold text-white transition-opacity hover:opacity-90",
                resetting && "opacity-60",
              )}
              whileHover={resetting || reduced ? undefined : hoverLift}
              whileTap={resetting || reduced ? undefined : pressTap}
              transition={springSnappy}
            >
              {resetting ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
              {resetting ? "Resetting" : "Reset"}
            </motion.button>
          </div>
        </div>
      </motion.section>
      </StaggerItem>
      </Stagger>
    </main>
  );
}

function SettingStat({
  label,
  value,
  numericValue,
  total,
  icon,
}: {
  label: string;
  value: string;
  numericValue: number;
  total?: number;
  icon: AtlasIconName;
}) {
  const reduced = Boolean(useReducedMotion());
  const pct = total ? Math.round((numericValue / Math.max(total, 1)) * 100) : Math.min(100, numericValue * 12);
  return (
    <motion.div
      className="command-surface flex min-h-[82px] items-center gap-3 overflow-hidden p-3.5"
      whileHover={reduced ? undefined : hoverLiftStrong}
      transition={springSnappy}
    >
      <AtlasIcon name={icon} size="sm" className="atlas-icon-badge--quiet" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2 text-[11px] font-medium text-muted-foreground">
          <span>{label}</span>
          <motion.span key={numericValue} className="tabular-nums" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            {total ? `${pct}%` : fmtInt(numericValue)}
          </motion.span>
        </div>
        <motion.div
          key={value}
          className="mt-2 text-[24px] font-semibold leading-none tabular-nums text-foreground"
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={reduced ? { duration: motionDuration.instant } : springSnappy}
        >
          {value}
        </motion.div>
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-muted">
          <motion.div
            className="h-full rounded-full bg-accent"
            initial={false}
            animate={{ width: `${pct}%` }}
            transition={reduced ? { duration: motionDuration.fast } : springBar}
          />
        </div>
      </div>
    </motion.div>
  );
}

function MiniCounter({ label, value, tone }: { label: string; value: number; tone: Tone }) {
  return (
    <motion.div
      className="inline-flex h-7 items-center gap-1.5 rounded-full border border-border bg-surface px-2 text-[11px] font-medium text-muted-foreground"
      layout
    >
      <motion.span
        key={value}
        className="font-semibold tabular-nums text-foreground"
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={springSnappy}
      >
        {fmtInt(value)}
      </motion.span>
      <span>{label}</span>
      <StatusDot tone={tone} />
    </motion.div>
  );
}

function LoadedFileCard({
  connector,
  index,
  expanded,
  onToggle,
}: {
  connector: ConnectorStatus;
  index: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const reduced = useReducedMotion();
  const state = fileState(connector);
  const tone = STATE_TONES[state];
  const sourceFreshness = freshness(connector);
  const label = FILE_LABELS[connector.connector_id] ?? connector.source_type.replace(/_/g, " ");
  const summary = stateSummary(connector, state);
  const isMissing = state === "missing";
  const freshnessTone: Tone = sourceFreshness.stale ? "warning" : loaded(connector.status) ? "positive" : "neutral";
  const sourceConfidence = connector.confidence_score;
  const sourceConfidenceTone = confidenceTone(sourceConfidence);
  const StateIcon = state === "error" ? FileWarning : state === "missing" ? Database : Clock3;
  const invoiceSummary = invoiceMessinessSummary(connector);
  const invoiceBadges = invoiceMessinessBadges(connector);
  const openBalance = summaryNumber(invoiceSummary, "open_balance_total");

  return (
    <motion.article
      className={cx("rounded-lg border border-border bg-background p-3", STATE_BACKGROUND[state])}
      data-settings-file-card={connector.connector_id}
      data-file-state={state}
      layout
      initial={reduced ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...springSnappy, delay: staggerDelay(index, 0.04, 0.18) }}
      whileHover={reduced ? undefined : { y: -2, boxShadow: "var(--shadow-soft)" }}
    >
      <button
        type="button"
        className="flex w-full min-w-0 items-start justify-between gap-3 text-left"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="flex min-w-0 items-start gap-2">
          <span className="relative mt-0.5 shrink-0">
            <AtlasIcon
              name={FILE_ICONS[connector.connector_id] ?? "memory"}
              size="xs"
              className="atlas-icon-badge--quiet"
            />
            <StatusDot tone={tone} className="absolute -bottom-0.5 -right-0.5 ring-2 ring-background" />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-[13px] font-semibold text-foreground">{label}</span>
            <span className={cx("mt-1 block line-clamp-2 text-[12px]", state === "error" ? "text-risk" : state === "partial" || state === "review" || state === "stale" ? "text-warning" : "text-muted-foreground")}>
              {summary}
            </span>
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <TonePill tone={tone}>{STATE_LABELS[state]}</TonePill>
          <motion.span animate={{ rotate: expanded ? 180 : 0 }} transition={springSnappy}>
            <ChevronDown className="h-4 w-4 text-muted-foreground" strokeWidth={2} />
          </motion.span>
        </span>
      </button>

      <div className="mt-3 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
            <StateIcon className={cx("h-3.5 w-3.5", tone === "risk" ? "text-risk" : tone === "warning" ? "text-warning" : "text-muted-foreground")} strokeWidth={2} />
            <span className="truncate">{sourceFreshness.label}</span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-muted">
            <motion.div
              className={cx(
                "h-full rounded-full",
                freshnessTone === "positive" ? "bg-positive" : freshnessTone === "warning" ? "bg-warning" : "bg-border-strong",
              )}
              initial={false}
              animate={{ width: `${freshnessWidth(sourceFreshness.days, isMissing)}%` }}
              transition={reduced ? { duration: motionDuration.fast } : springBar}
            />
          </div>
        </div>
        <motion.div
          key={connector.record_count}
          className="text-right text-[12px] font-semibold tabular-nums text-foreground"
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={springSnappy}
        >
          {loaded(connector.status) ? fmtInt(connector.record_count) : "0"}
          <span className="ml-1 font-normal text-muted-foreground">rows</span>
        </motion.div>
      </div>
      <div className="mt-3 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
            <ShieldCheck
              className={cx(
                "h-3.5 w-3.5",
                sourceConfidenceTone === "positive" ? "text-positive" : sourceConfidenceTone === "warning" ? "text-warning" : sourceConfidenceTone === "risk" ? "text-risk" : "text-muted-foreground",
              )}
              strokeWidth={2}
            />
            <span className="truncate">Source confidence</span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-muted">
            <motion.div
              className={cx(
                "h-full rounded-full",
                sourceConfidenceTone === "positive" ? "bg-positive" : sourceConfidenceTone === "warning" ? "bg-warning" : sourceConfidenceTone === "risk" ? "bg-risk" : "bg-border-strong",
              )}
              initial={false}
              animate={{ width: `${Math.max(8, Math.min(100, sourceConfidence ?? 0))}%` }}
              transition={reduced ? { duration: motionDuration.fast } : springBar}
            />
          </div>
        </div>
        <motion.div
          key={sourceConfidence ?? "unknown"}
          className="text-right text-[12px] font-semibold tabular-nums text-foreground"
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={springSnappy}
        >
          {sourceConfidence == null ? "—" : `${fmtInt(sourceConfidence)}%`}
        </motion.div>
      </div>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            className="mt-3 border-t border-border pt-3"
            initial={reduced ? { opacity: 0 } : { opacity: 0, height: 0 }}
            animate={reduced ? { opacity: 1 } : { opacity: 1, height: "auto" }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, height: 0 }}
            transition={{ duration: motionDuration.normal, ease: EASE_OUT_EXPO }}
          >
            <dl className="grid gap-2 text-[11.5px] sm:grid-cols-2">
              <Detail label="Source" value={connector.source_name ?? connector.configured_path ?? "Not uploaded"} />
              {connector.workbook_name && (
                <Detail
                  label="Workbook sheet"
                  value={`${connector.workbook_sheet ?? "Unmatched sheet"} · ${connector.workbook_name}`}
                />
              )}
              <Detail label="Freshness" value={sourceFreshness.detail} tone={freshnessTone} />
              <Detail label="Rows" value={`${fmtInt(connector.record_count)} accepted`} tone={tone} />
              <Detail label="Confidence" value={sourceConfidence == null ? "Not scored" : `${fmtInt(sourceConfidence)}%`} tone={sourceConfidenceTone} />
              <Detail label="Quality" value={qualitySummary(connector) ?? "No row issues"} tone={tone} />
              <Detail label="Reconciliation" value={connector.reconciliation_status.replace(/_/g, " ")} tone={tone} />
              <Detail label="Transport" value={connector.transport || "file"} />
              {connector.source_format && <Detail label="Format" value={connector.source_format.toUpperCase()} />}
              {connector.header_row_number != null && <Detail label="Header row" value={`Row ${connector.header_row_number}`} />}
              {connector.hidden_column_count ? <Detail label="Hidden columns" value={fmtInt(connector.hidden_column_count)} /> : null}
              {connector.extra_column_count ? <Detail label="Extra columns" value={`${fmtInt(connector.extra_column_count)} ignored`} /> : null}
            </dl>
            {(connector.confidence_reasons?.length ?? 0) > 0 && (
              <div className="mt-3 rounded-md border border-warning/30 bg-warning-bg/25 p-2.5">
                <div className="text-[10px] font-semibold uppercase text-warning">Confidence reasons</div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {(connector.confidence_reasons ?? []).slice(0, 4).map((reason) => (
                    <TonePill key={reason} tone={sourceConfidenceTone === "risk" ? "risk" : "warning"}>
                      {reason}
                    </TonePill>
                  ))}
                </div>
              </div>
            )}
            {(connector.required_facts_missing?.length ?? 0) > 0 && (
              <div className="mt-3 rounded-md border border-risk/25 bg-risk-bg/25 p-2.5">
                <div className="text-[10px] font-semibold uppercase text-risk">Required facts missing</div>
                <ul className="mt-2 grid gap-1">
                  {(connector.required_facts_missing ?? []).slice(0, 4).map((fact) => (
                    <li key={fact} className="text-[11.5px] leading-relaxed text-muted-foreground">
                      {fact}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {invoiceBadges.length > 0 && (
              <div className="mt-3 rounded-md border border-warning/35 bg-warning-bg/35 p-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-[10px] font-semibold uppercase text-warning">Invoice messiness</div>
                  {openBalance > 0 && (
                    <div className="text-[11px] font-semibold tabular-nums text-foreground">{formatUsd(openBalance)} open balance</div>
                  )}
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {invoiceBadges.map((badge) => (
                    <TonePill key={badge.label} tone={badge.tone}>
                      {badge.label}
                    </TonePill>
                  ))}
                </div>
              </div>
            )}
            {connector.blockers.length > 0 && (
              <ul className="mt-3 space-y-1.5">
                {connector.blockers.slice(0, 3).map((blocker) => (
                  <li key={blocker} className="flex gap-1.5 text-[11.5px] leading-relaxed text-warning">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={2} />
                    <span>{blocker}</span>
                  </li>
                ))}
              </ul>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.article>
  );
}

function Detail({ label, value, tone = "neutral" }: { label: string; value: string; tone?: Tone }) {
  return (
    <div className="min-w-0 rounded-md border border-border/70 bg-surface/65 px-2 py-1.5">
      <dt className="text-[10px] font-medium uppercase text-subtle-foreground">{label}</dt>
      <dd
        className={cx(
          "mt-0.5 truncate text-[11.5px] font-semibold",
          tone === "positive" ? "text-positive" : tone === "warning" ? "text-warning" : tone === "risk" ? "text-risk" : "text-foreground",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

function freshnessWidth(days: number | null, missing: boolean): number {
  if (missing || days === null) return 18;
  return Math.max(18, Math.min(100, 100 - Math.round((days / 45) * 82)));
}
