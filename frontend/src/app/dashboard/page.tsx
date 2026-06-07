"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Play,
  RefreshCw,
  Upload,
} from "lucide-react";
import { CollapseIn, PopIn } from "@/components/motion/presence";
import { MotionLink } from "@/components/motion/motion-link";
import { Stagger, StaggerItem } from "@/components/motion/stagger";
import { springBar, springSnappy } from "@/components/motion/variants";
import { api } from "@/lib/api";
import type {
  ConnectorInventory,
  ConnectorStatus,
  Discrepancy,
  ReconciliationReport,
} from "@/lib/types";
import { fmtInt } from "@/lib/format";
import { cx, StatusDot } from "@/components/ui";
import { TonePill, type Tone } from "@/components/dashboard";

type UploadState = {
  status: "uploading" | "done" | "error";
  detail?: string;
};

type BatchState = {
  total: number;
  completed: number;
  failed: number;
  current?: string;
  done?: boolean;
};

const FILES: Record<string, { label: string; expected: string; aliases: string[] }> = {
  ledger: {
    label: "Ledger",
    expected: "CSV",
    aliases: ["ledger", "general-ledger", "gl", "cash"],
  },
  invoices: {
    label: "Invoices",
    expected: "CSV",
    aliases: ["invoice", "invoices", "ap"],
  },
  vendor_export: {
    label: "Vendors",
    expected: "JSON",
    aliases: ["vendor", "procurement", "contract"],
  },
  crm_opportunities: {
    label: "Sales pipeline",
    expected: "CSV",
    aliases: ["crm", "opportunity", "opportunities", "pipeline"],
  },
  headcount_plan: {
    label: "Hiring plan",
    expected: "CSV",
    aliases: ["headcount", "workforce", "hris"],
  },
  security_evidence: {
    label: "Security notes",
    expected: "JSON",
    aliases: ["security", "soc", "soc2", "control", "evidence"],
  },
  board_policy: {
    label: "Board rules",
    expected: "JSON",
    aliases: ["board-policy", "board_policy", "policy", "policies"],
  },
};

const ORDER = Object.keys(FILES);
const BATCH_UPLOAD_INPUT_ID = "atlas-batch-upload";
const FILE_ACCEPT = ".csv,.json,text/csv,application/json";

function inferConnectorId(fileName: string): string | null {
  const normalized = fileName.toLowerCase().replace(/[_\s]+/g, "-");
  if (normalized.includes("board-policy") || (normalized.includes("board") && normalized.includes("policy"))) {
    return "board_policy";
  }
  const match = ORDER.find((id) => FILES[id].aliases.some((alias) => normalized.includes(alias)));
  return match ?? null;
}

function loadedStatus(status?: string): boolean {
  return status === "imported" || status === "partial" || status === "skipped_unchanged";
}

function rowTone(status?: string, state?: UploadState): Tone {
  if (state?.status === "uploading") return "info";
  if (state?.status === "error" || status === "error" || status === "missing_file") return "risk";
  if (status === "partial") return "warning";
  if (loadedStatus(status)) return "positive";
  return "neutral";
}

function rowLabel(status?: string, state?: UploadState): string {
  if (state?.status === "uploading") return "Uploading";
  if (state?.status === "error") return "Failed";
  if (state?.status === "done") return "Loaded";
  if (status === "partial") return "Loaded with notes";
  if (loadedStatus(status)) return "Loaded";
  if (status === "empty") return "Empty";
  if (status === "error") return "Failed";
  return "Need file";
}

function percent(batch: BatchState | null): number {
  if (!batch?.total) return 0;
  return Math.round((batch.completed / batch.total) * 100);
}

function serviceMessage(error: string): string {
  if (/demo service is offline/i.test(error)) return "The demo service is offline. Start the demo server, then try again.";
  return error;
}

export default function DataRoomPage() {
  const [inventory, setInventory] = useState<ConnectorInventory | null>(null);
  const [reconciliation, setReconciliation] = useState<ReconciliationReport | null>(null);
  const [uploadState, setUploadState] = useState<Record<string, UploadState>>({});
  const [batch, setBatch] = useState<BatchState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(true);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [connectors, report] = await Promise.all([api.connectors(), api.reconciliation()]);
      setInventory(connectors);
      setReconciliation(report);
      setError(null);
    } catch (err) {
      setError(serviceMessage(err instanceof Error ? err.message : String(err)));
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  useEffect(() => {
    if (!error || !/demo service is offline/i.test(error)) return;
    const id = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(id);
  }, [error, load]);

  const connectorsById = useMemo(() => {
    return new Map((inventory?.connectors ?? []).map((connector) => [connector.connector_id, connector]));
  }, [inventory?.connectors]);

  const rows = useMemo(() => {
    return ORDER.map((id) => ({
      id,
      ...FILES[id],
      connector: connectorsById.get(id),
    }));
  }, [connectorsById]);

  const loaded = rows.filter((row) => loadedStatus(row.connector?.status)).length;
  const total = rows.length;
  const readyToRun = loaded === total && total > 0;

  async function uploadFileFor(connectorId: string, file: File): Promise<boolean> {
    setUploadState((prev) => ({
      ...prev,
      [connectorId]: { status: "uploading", detail: file.name },
    }));
    try {
      const result = await api.uploadConnectorFile(connectorId, file);
      setInventory({ mode: "strict-live", connectors: result.connectors, confidence: result.confidence });
      setReconciliation(result.reconciliation);
      setUploadState((prev) => ({
        ...prev,
        [connectorId]: { status: "done", detail: file.name },
      }));
      setError(null);
      return true;
    } catch (err) {
      const message = serviceMessage(err instanceof Error ? err.message : String(err));
      setUploadState((prev) => ({
        ...prev,
        [connectorId]: { status: "error", detail: message },
      }));
      setError(message);
      return false;
    }
  }

  async function uploadFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;

    setError(null);
    setBatch({ total: list.length, completed: 0, failed: 0, current: "Starting upload" });

    let failed = 0;
    let completed = 0;
    for (const file of list) {
      const connectorId = inferConnectorId(file.name);
      if (!connectorId) {
        failed += 1;
        completed += 1;
        setBatch({ total: list.length, completed, failed, current: file.name });
        setError(`This file name was not recognized: ${file.name}`);
        continue;
      }

      setBatch({ total: list.length, completed, failed, current: file.name });
      const ok = await uploadFileFor(connectorId, file);
      if (!ok) failed += 1;
      completed += 1;
      setBatch({ total: list.length, completed, failed, current: file.name });
    }

    setBatch({
      total: list.length,
      completed,
      failed,
      current: failed ? "Open the failed rows and try again" : "Ready to run the council",
      done: true,
    });
  }

  return (
    <main className="mx-auto flex w-full max-w-[1180px] flex-col gap-4 px-4 py-5 sm:px-6">
      <Stagger className="flex flex-col gap-4">
      <StaggerItem className="grid gap-4 border-b border-border pb-5 lg:grid-cols-[minmax(0,1fr)_360px] lg:items-end">
        <div className="min-w-0">
          <h1 className="font-display text-[28px] font-medium tracking-tight">Add the demo files</h1>
          <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-muted-foreground">
            Choose the seven files from any demo folder. Atlas loads them, checks them, and then you can run the council.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 lg:justify-end">
          <motion.label
            htmlFor={BATCH_UPLOAD_INPUT_ID}
            className="inline-flex h-10 cursor-pointer items-center justify-center gap-2 rounded-lg bg-accent px-4 text-[13px] font-semibold text-accent-foreground"
            whileHover={{ scale: 1.03, y: -1 }}
            whileTap={{ scale: 0.97 }}
            transition={springSnappy}
          >
            <Upload className="h-4 w-4" strokeWidth={2} />
            Choose files
          </motion.label>
          <MotionLink
            href="/decisions"
            className={cx(
              "inline-flex h-10 items-center justify-center gap-2 rounded-lg border px-4 text-[13px] font-semibold transition-colors",
              readyToRun
                ? "border-border bg-surface text-foreground hover:bg-surface-muted"
                : "border-border bg-surface text-muted-foreground",
            )}
          >
            <Play className="h-4 w-4" strokeWidth={2} />
            Run council
          </MotionLink>
          <motion.button
            type="button"
            onClick={() => void load()}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-border bg-surface px-3 text-[13px] font-semibold text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground"
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
            transition={springSnappy}
          >
            {refreshing ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <motion.span animate={{ rotate: 0 }} whileHover={{ rotate: 180 }} transition={{ duration: 0.35 }}>
                <RefreshCw className="h-4 w-4" />
              </motion.span>
            )}
            Refresh
          </motion.button>
          <input
            id={BATCH_UPLOAD_INPUT_ID}
            className="file-input-offscreen"
            type="file"
            accept={FILE_ACCEPT}
            multiple
            onChange={(event) => {
              if (event.target.files?.length) void uploadFiles(event.target.files);
              event.currentTarget.value = "";
            }}
          />
        </div>
      </StaggerItem>

      <CollapseIn show={Boolean(error)}>
        <div className="rounded-lg border border-risk/20 bg-risk-bg px-3 py-2 text-[13px] font-medium text-risk">
          {error}
        </div>
      </CollapseIn>

      <StaggerItem>
        <UploadProgress batch={batch} loaded={loaded} total={total} refreshing={refreshing} />
      </StaggerItem>

      <StaggerItem className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_340px]">
        <motion.div
          className="command-surface overflow-hidden"
          layout
          transition={springSnappy}
        >
          <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
            <div className="min-w-0">
              <h2 className="text-[15px] font-semibold">Files to load</h2>
              <p className="mt-0.5 text-[12px] text-muted-foreground">The file names are matched automatically.</p>
            </div>
            <TonePill tone={readyToRun ? "positive" : loaded ? "warning" : "neutral"}>
              {loaded}/{total} loaded
            </TonePill>
          </div>
          <div className="divide-y divide-border">
            {rows.map((row, index) => (
              <FileRow
                key={row.id}
                index={index}
                id={row.id}
                label={row.label}
                expected={row.expected}
                connector={row.connector}
                state={uploadState[row.id]}
                onFile={(file) => void uploadFileFor(row.id, file)}
              />
            ))}
          </div>
        </motion.div>

        <ResultPanel
          report={reconciliation}
          loaded={loaded}
          total={total}
          serviceError={error}
          readyToRun={readyToRun}
        />
      </StaggerItem>
      </Stagger>
    </main>
  );
}

function UploadProgress({
  batch,
  loaded,
  total,
  refreshing,
}: {
  batch: BatchState | null;
  loaded: number;
  total: number;
  refreshing: boolean;
}) {
  const reduced = useReducedMotion();
  const value = batch ? percent(batch) : total ? Math.round((loaded / total) * 100) : 0;
  const label = batch
    ? batch.done
      ? batch.failed
        ? `${batch.failed} file${batch.failed === 1 ? "" : "s"} failed`
        : "All files loaded"
      : `Loading ${batch.completed}/${batch.total}`
    : refreshing
      ? "Checking current files"
      : `${loaded}/${total} files loaded`;
  const detail = batch?.current ?? (loaded === total && total > 0 ? "Ready to run the council" : "Upload the full folder for the cleanest demo.");
  const barClass = batch?.done && !batch.failed ? "bg-positive" : batch?.failed ? "bg-risk" : "bg-accent";
  const active = Boolean(batch && !batch.done);

  return (
    <motion.section
      className="command-surface relative overflow-hidden p-4"
      layout
      animate={
        active && !reduced
          ? { boxShadow: ["var(--shadow-soft)", "0 0 0 1px color-mix(in srgb, var(--accent) 25%, transparent), 0 12px 40px color-mix(in srgb, var(--accent) 12%, transparent)"] }
          : { boxShadow: "var(--shadow-soft)" }
      }
      transition={active ? { duration: 1.6, repeat: Infinity, repeatType: "reverse" } : springSnappy}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <AnimatePresence mode="wait">
            <motion.div
              key={label}
              className="text-[13px] font-semibold"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.22 }}
            >
              {label}
            </motion.div>
          </AnimatePresence>
          <AnimatePresence mode="wait">
            <motion.div
              key={detail}
              className="mt-0.5 truncate text-[12px] text-muted-foreground"
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 8 }}
              transition={{ duration: 0.2 }}
            >
              {detail}
            </motion.div>
          </AnimatePresence>
        </div>
        <motion.div
          className="text-[18px] font-semibold tabular-nums"
          key={value}
          initial={{ scale: 0.85, opacity: 0.6 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={springSnappy}
        >
          {value}%
        </motion.div>
      </div>
      <div className="relative mt-3 h-3 overflow-hidden rounded-full bg-surface-muted">
        <motion.div
          className={cx("absolute inset-y-0 left-0 rounded-full", barClass)}
          initial={false}
          animate={{ width: `${value}%` }}
          transition={reduced ? { duration: 0.2 } : springBar}
        />
        {active && !reduced && (
          <motion.div
            className="absolute inset-y-0 w-16 rounded-full bg-white/25"
            animate={{ x: ["-20%", "420%"] }}
            transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut" }}
            style={{ left: 0 }}
          />
        )}
      </div>
    </motion.section>
  );
}

function FileRow({
  id,
  index,
  label,
  expected,
  connector,
  state,
  onFile,
}: {
  id: string;
  index: number;
  label: string;
  expected: string;
  connector?: ConnectorStatus;
  state?: UploadState;
  onFile: (file: File) => void;
}) {
  const reduced = useReducedMotion();
  const inputId = `upload-${id}`;
  const status = connector?.status;
  const uploading = state?.status === "uploading";
  const justDone = state?.status === "done";
  const tone = rowTone(status, state);
  const detail = state?.detail ?? connector?.source_name ?? "";
  const statusKey = rowLabel(status, state);

  return (
    <motion.div
      className="relative grid gap-3 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_150px_122px] sm:items-center"
      layout
      initial={reduced ? false : { opacity: 0, x: -16 }}
      animate={{
        opacity: 1,
        x: 0,
        backgroundColor: uploading
          ? "color-mix(in srgb, var(--info-bg) 55%, transparent)"
          : justDone
            ? "color-mix(in srgb, var(--positive-bg) 45%, transparent)"
            : "transparent",
      }}
      transition={{ ...springSnappy, delay: index * 0.04 }}
    >
      <AnimatePresence>
        {justDone && !reduced && (
          <motion.span
            className="pointer-events-none absolute inset-0 bg-positive/10"
            initial={{ opacity: 0.8, scaleX: 0, originX: 0 }}
            animate={{ opacity: 0, scaleX: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.65, ease: [0.22, 1, 0.36, 1] }}
          />
        )}
      </AnimatePresence>
      <div className="relative min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <motion.div animate={uploading ? { scale: [1, 1.25, 1] } : { scale: 1 }} transition={{ duration: 0.9, repeat: uploading ? Infinity : 0 }}>
            <StatusDot tone={tone} />
          </motion.div>
          <div className="text-[14px] font-semibold text-foreground">{label}</div>
          <AnimatePresence mode="wait">
            <motion.span key={statusKey} initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.9 }} transition={{ duration: 0.18 }}>
              <TonePill tone={tone}>{statusKey}</TonePill>
            </motion.span>
          </AnimatePresence>
        </div>
        <div
          className={cx(
            "mt-1 line-clamp-1 text-[12px]",
            state?.status === "error" ? "text-risk" : "text-muted-foreground",
          )}
        >
          {detail || `${expected} file`}
        </div>
        {connector?.blockers?.[0] && <div className="mt-1 line-clamp-1 text-[12px] text-warning">{connector.blockers[0]}</div>}
      </div>
      <div className="text-[12px] text-muted-foreground sm:text-right">
        {loadedStatus(status) ? (
          <>
            <span className="font-semibold tabular-nums text-foreground">{fmtInt(connector?.record_count ?? 0)}</span> rows
          </>
        ) : (
          expected
        )}
      </div>
      <input
        id={inputId}
        className="file-input-offscreen"
        type="file"
        accept={FILE_ACCEPT}
        disabled={uploading}
        onChange={(event) => {
          const [file] = Array.from(event.target.files ?? []);
          if (file) onFile(file);
          event.currentTarget.value = "";
        }}
      />
      <motion.label
        htmlFor={inputId}
        className={cx(
          "inline-flex h-9 cursor-pointer items-center justify-center gap-2 rounded-lg border border-border bg-surface px-3 text-[12px] font-semibold transition-colors hover:bg-surface-muted",
          uploading && "pointer-events-none opacity-60",
        )}
        whileHover={uploading ? undefined : { scale: 1.04 }}
        whileTap={uploading ? undefined : { scale: 0.96 }}
        transition={springSnappy}
      >
        <AnimatePresence mode="wait">
          <motion.span
            key={uploading ? "loading" : "idle"}
            initial={{ opacity: 0, rotate: -90 }}
            animate={{ opacity: 1, rotate: 0 }}
            exit={{ opacity: 0, rotate: 90 }}
            transition={{ duration: 0.2 }}
            className="inline-flex"
          >
            {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
          </motion.span>
        </AnimatePresence>
        Choose
      </motion.label>
    </motion.div>
  );
}

function ResultPanel({
  report,
  loaded,
  total,
  serviceError,
  readyToRun,
}: {
  report: ReconciliationReport | null;
  loaded: number;
  total: number;
  serviceError: string | null;
  readyToRun: boolean;
}) {
  const issues = (report?.discrepancies ?? []).filter((item) => item.severity !== "info");
  const ready = loaded === total && total > 0;
  const stateKey = serviceError ? "offline" : ready ? (issues.length ? "review" : "ready") : loaded ? "partial" : "waiting";

  return (
    <motion.aside
      className="command-surface flex min-h-[360px] flex-col p-4"
      layout
      initial={{ opacity: 0, x: 16 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ ...springSnappy, delay: 0.12 }}
    >
      <h2 className="text-[15px] font-semibold">What happened</h2>
      <div className="mt-3 rounded-lg border border-border bg-background p-3">
        <AnimatePresence mode="wait">
          <motion.div
            key={stateKey}
            initial={{ opacity: 0, y: 8, filter: "blur(3px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -6, filter: "blur(2px)" }}
            transition={{ duration: 0.28 }}
          >
            {serviceError ? (
              <StateMessage
                tone="risk"
                title="The demo service is not connected"
                detail="Uploads need the agent server before files can load."
              />
            ) : ready ? (
              <StateMessage
                tone={issues.length ? "warning" : "positive"}
                title={issues.length ? "Files loaded. Review the notes." : "Files loaded."}
                detail={
                  issues.length
                    ? `Atlas found ${fmtInt(issues.length)} item${issues.length === 1 ? "" : "s"} to review before the run.`
                    : "No review items found."
                }
              />
            ) : loaded ? (
              <StateMessage
                tone="warning"
                title="Keep adding files"
                detail={`${fmtInt(total - loaded)} file${total - loaded === 1 ? "" : "s"} still missing.`}
              />
            ) : (
              <StateMessage tone="neutral" title="Waiting for files" detail="Choose the demo files to begin." />
            )}
          </motion.div>
        </AnimatePresence>
      </div>

      <div className="mt-4 flex-1">
        <AnimatePresence mode="wait">
          {issues.length ? (
            <motion.ul
              key="issues"
              className="space-y-2"
              initial="hidden"
              animate="show"
              exit={{ opacity: 0 }}
              variants={{ hidden: {}, show: { transition: { staggerChildren: 0.06 } } }}
            >
              {issues.slice(0, 4).map((item) => (
                <IssueItem key={item.id} item={item} />
              ))}
            </motion.ul>
          ) : (
            <motion.div
              key="empty"
              className="flex min-h-[140px] items-center justify-center rounded-lg border border-dashed border-border bg-surface-quiet p-4 text-center text-[12px] text-muted-foreground"
              initial={{ opacity: 0, scale: 0.98 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.98 }}
            >
              Notes from the file check will appear here.
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div className="mt-4 border-t border-border pt-3">
        <div className="flex items-center justify-between gap-2 text-[12px] text-muted-foreground">
          <span>File check</span>
          <span>{serviceError ? "Offline" : ready ? (issues.length ? "Review notes" : "Ready") : loaded ? "Waiting" : "Not started"}</span>
        </div>
        <PopIn show={readyToRun}>
          <MotionLink
            href="/decisions"
            className="mt-3 inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-accent text-[13px] font-semibold text-accent-foreground"
          >
            <Play className="h-4 w-4" />
            Run council
          </MotionLink>
        </PopIn>
        {!readyToRun && (
          <div className="mt-3 inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-surface-muted text-[13px] font-semibold text-muted-foreground">
            <Play className="h-4 w-4" />
            Run council
          </div>
        )}
      </div>
    </motion.aside>
  );
}

function StateMessage({ tone, title, detail }: { tone: Tone; title: string; detail: string }) {
  const Icon = tone === "positive" ? CheckCircle2 : AlertTriangle;
  return (
    <div className="flex items-start gap-2.5">
      <Icon
        className={cx(
          "mt-0.5 h-4 w-4 shrink-0",
          tone === "positive" ? "text-positive" : tone === "risk" ? "text-risk" : tone === "warning" ? "text-warning" : "text-muted-foreground",
        )}
        strokeWidth={2}
      />
      <div className="min-w-0">
        <div className="text-[13px] font-semibold text-foreground">{title}</div>
        <div className="mt-0.5 text-[12px] leading-relaxed text-muted-foreground">{detail}</div>
      </div>
    </div>
  );
}

function IssueItem({ item }: { item: Discrepancy }) {
  const tone: Tone = item.severity === "critical" || item.severity === "high" ? "risk" : item.severity === "medium" ? "warning" : "info";
  return (
    <motion.li
      className="rounded-lg border border-border bg-background p-3"
      variants={{ hidden: { opacity: 0, x: 12 }, show: { opacity: 1, x: 0 } }}
      whileHover={{ x: 2 }}
      transition={springSnappy}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="line-clamp-1 text-[12px] font-semibold text-foreground">{item.title}</div>
          <div className="mt-1 line-clamp-2 text-[11.5px] leading-relaxed text-muted-foreground">{item.detail}</div>
        </div>
        <TonePill tone={tone}>{item.severity}</TonePill>
      </div>
    </motion.li>
  );
}
