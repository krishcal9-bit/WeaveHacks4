"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, Loader2, RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import type { ConnectorInventory, DemoResetResponse, ReconciliationReport } from "@/lib/types";
import { fmtInt } from "@/lib/format";
import { motion } from "motion/react";
import { CollapseIn } from "@/components/motion/presence";
import { Stagger, StaggerItem } from "@/components/motion/stagger";
import { springSnappy } from "@/components/motion/variants";
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

function resetTone(status?: string): Tone {
  return status === "reset" ? "positive" : "neutral";
}

function loaded(status?: string): boolean {
  return status === "imported" || status === "partial" || status === "skipped_unchanged";
}

export default function SettingsPage() {
  const [inventory, setInventory] = useState<ConnectorInventory | null>(null);
  const [reconciliation, setReconciliation] = useState<ReconciliationReport | null>(null);
  const [resetting, setResetting] = useState(false);
  const [result, setResult] = useState<DemoResetResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

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
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setResetting(false);
    }
  }

  const connectors = inventory?.connectors ?? [];
  const loadedCount = connectors.filter((connector) => loaded(connector.status)).length;
  const total = inventory?.confidence.sources_total ?? (connectors.length || 7);
  const issues = (reconciliation?.discrepancies ?? []).filter((item) => item.severity !== "info").length;
  const deleted = result ? Object.values(result.deleted).reduce((sum, count) => sum + count, 0) : 0;

  return (
    <main className="mx-auto flex min-h-full w-full max-w-[980px] flex-col gap-4 px-4 py-5 sm:px-6">
      <Stagger className="flex flex-col gap-4">
      <StaggerItem className="border-b border-border pb-4">
        <h1 className="font-display text-[28px] font-medium tracking-tight">Demo reset</h1>
        <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-muted-foreground">
          Clear the uploaded files and start the demo from a blank state.
        </p>
      </StaggerItem>

      <CollapseIn show={Boolean(error)}>
        <div className="rounded-lg border border-risk/20 bg-risk-bg px-3 py-2 text-[13px] font-medium text-risk">
          {error}
        </div>
      </CollapseIn>

      <StaggerItem className="grid gap-3 sm:grid-cols-2">
        <SettingStat label="Files loaded" value={`${loadedCount}/${total}`} />
        <SettingStat label="Review items" value={fmtInt(issues)} />
      </StaggerItem>

      <StaggerItem>
      <section className="command-surface overflow-hidden">
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div>
            <h2 className="text-[15px] font-semibold">Loaded files</h2>
            <p className="mt-0.5 text-[12px] text-muted-foreground">After reset, every file returns to “Need file.”</p>
          </div>
          {result && <TonePill tone={resetTone(result.status)}>{result.status}</TonePill>}
        </div>
        <div className="grid gap-2 p-4 sm:grid-cols-2">
          {connectors.map((connector, index) => {
            const isLoaded = loaded(connector.status);
            const tone: Tone = connector.status === "partial" ? "warning" : isLoaded ? "positive" : connector.status === "error" ? "risk" : "neutral";
            return (
              <motion.div
                key={connector.connector_id}
                className="rounded-lg border border-border bg-background p-3"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ ...springSnappy, delay: index * 0.05 }}
                whileHover={{ y: -2, boxShadow: "var(--shadow-soft)" }}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <StatusDot tone={tone} />
                    <div className="truncate text-[13px] font-semibold text-foreground">
                      {FILE_LABELS[connector.connector_id] ?? connector.source_type.replace(/_/g, " ")}
                    </div>
                  </div>
                  <TonePill tone={tone}>{isLoaded ? "Loaded" : "Need file"}</TonePill>
                </div>
                <div className="mt-1 text-[12px] text-muted-foreground">
                  {isLoaded ? `${fmtInt(connector.record_count)} rows` : "No upload yet"}
                </div>
              </motion.div>
            );
          })}
        </div>
      </section>
      </StaggerItem>

      <div className="flex-1" />

      <StaggerItem>
      <motion.section
        className="rounded-lg border border-risk/20 bg-surface p-4 shadow-sm"
        layout
        transition={springSnappy}
      >
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {result ? (
                <CheckCircle2 className="h-4 w-4 text-positive" strokeWidth={2} />
              ) : (
                <AlertTriangle className="h-4 w-4 text-risk" strokeWidth={2} />
              )}
              <h2 className="text-[15px] font-semibold">Reset uploaded files</h2>
            </div>
            <p className="mt-1 max-w-2xl text-[12px] leading-relaxed text-muted-foreground">
              Clears uploaded files, the latest file check, and the council draft state.
            </p>
            {result && (
              <div className="mt-2 text-[11px] text-subtle-foreground">
                {fmtInt(deleted)} item{deleted === 1 ? "" : "s"} cleared.
              </div>
            )}
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
              whileHover={resetting ? undefined : { scale: 1.04 }}
              whileTap={resetting ? undefined : { scale: 0.96 }}
              transition={springSnappy}
            >
              {resetting ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
              Reset
            </motion.button>
          </div>
        </div>
      </motion.section>
      </StaggerItem>
      </Stagger>
    </main>
  );
}

function SettingStat({ label, value }: { label: string; value: string }) {
  return (
    <motion.div
      className="command-surface min-h-[82px] p-3.5"
      whileHover={{ y: -3, scale: 1.01 }}
      transition={springSnappy}
    >
      <div className="text-[11px] font-medium text-muted-foreground">{label}</div>
      <motion.div
        key={value}
        className="mt-2 text-[24px] font-semibold leading-none tabular-nums text-foreground"
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={springSnappy}
      >
        {value}
      </motion.div>
    </motion.div>
  );
}
