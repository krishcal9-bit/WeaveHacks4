"use client";

import { useCallback, useEffect, useMemo, useState, type DragEvent } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  AlertTriangle,
  CheckCircle2,
  FileCheck2,
  FileQuestion,
  Loader2,
  Play,
  RefreshCw,
  RotateCcw,
  Upload,
} from "lucide-react";
import { CollapseIn, PopIn } from "@/components/motion/presence";
import { MotionLink } from "@/components/motion/motion-link";
import { Stagger, StaggerItem } from "@/components/motion/stagger";
import {
  EASE_OUT_EXPO,
  hoverLift,
  hoverNudge,
  motionDuration,
  pressTap,
  springBar,
  springSnappy,
  staggerDelay,
  transitionFade,
  transitionFadeFast,
  transitionReveal,
} from "@/components/motion/variants";
import { onDemoReset } from "@/lib/demo-reset";
import { api } from "@/lib/api";
import { formatExecutiveError } from "@/lib/errors";
import { AtlasIcon, type AtlasIconName } from "@/components/atlas-icon";
import type {
  ConnectorInventory,
  ConnectorStatus,
  Discrepancy,
  ImportConfidence,
  ReconciliationReport,
} from "@/lib/types";
import { fmtInt } from "@/lib/format";
import { cx, StatusDot } from "@/components/ui";
import { TonePill, type Tone } from "@/components/dashboard";

type UploadState = {
  status: "matched" | "parsing" | "loaded" | "review" | "error";
  detail?: string;
  fileName?: string;
  file?: File;
};

type BatchState = {
  total: number;
  matched: number;
  completed: number;
  failed: number;
  review: number;
  phase: "matching" | "parsing" | "reconciling" | "done";
  current?: string;
  done?: boolean;
};

type MatchedUpload = {
  connectorId: string;
  file: File;
};

type UploadOutcome = {
  connectorId: string;
  fileName: string;
  status: "loaded" | "review" | "error";
  detail?: string;
};

const FILES: Record<string, { label: string; expected: string; icon: AtlasIconName; aliases: string[] }> = {
  ledger: {
    label: "Ledger",
    expected: "CSV / Excel",
    icon: "runway",
    aliases: ["ledger", "general-ledger", "cloudledger", "gl-detail", "gl", "cash", "close-detail"],
  },
  invoices: {
    label: "Invoices",
    expected: "CSV / Excel",
    icon: "reconcile",
    aliases: ["invoice", "invoices", "payablesdesk", "ap-aging", "ap", "bills", "payables"],
  },
  vendor_export: {
    label: "Vendors",
    expected: "JSON / Excel",
    icon: "evidence",
    aliases: ["vendor", "procurement", "contractvault", "contract", "supplier", "vendor-register"],
  },
  crm_opportunities: {
    label: "Sales pipeline",
    expected: "CSV / Excel",
    icon: "scenario",
    aliases: ["crm", "opportunity", "opportunities", "pipelinehub", "pipeline", "forecast", "sales"],
  },
  headcount_plan: {
    label: "Hiring plan",
    expected: "CSV / Excel",
    icon: "council",
    aliases: ["headcount", "peopleroster", "workforce", "hris", "people", "hiring"],
  },
  security_evidence: {
    label: "Security notes",
    expected: "JSON / Excel",
    icon: "risk",
    aliases: ["security", "trustvault", "grc", "soc", "soc2", "control", "evidence", "risk"],
  },
  board_policy: {
    label: "Board rules",
    expected: "JSON / Excel",
    icon: "memo",
    aliases: ["board-policy", "board_policy", "boardportal", "policy", "policies", "rules", "governance"],
  },
};

const ORDER = Object.keys(FILES);
const BATCH_UPLOAD_INPUT_ID = "atlas-batch-upload";
const FILE_ACCEPT =
  ".csv,.json,.jsonl,.xlsx,.xls,text/csv,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel";

function isWorkbookFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return (
    name.endsWith(".xlsx") ||
    name.endsWith(".xls") ||
    file.type === "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" ||
    file.type === "application/vnd.ms-excel"
  );
}

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
  if (state?.status === "matched" || state?.status === "parsing") return "info";
  if (state?.status === "error" || status === "error" || status === "missing_file") return "risk";
  if (state?.status === "review" || status === "partial") return "warning";
  if (state?.status === "loaded" || loadedStatus(status)) return "positive";
  return "neutral";
}

function rowLabel(status?: string, state?: UploadState): string {
  if (state?.status === "matched") return "Matched";
  if (state?.status === "parsing") return "Parsing";
  if (state?.status === "error") return "Failed";
  if (state?.status === "review") return "Needs review";
  if (state?.status === "loaded") return "Loaded";
  if (status === "partial") return "Loaded with notes";
  if (loadedStatus(status)) return "Loaded";
  if (status === "empty") return "Empty";
  if (status === "error") return "Failed";
  return "Need file";
}

function connectorQualitySummary(connector?: ConnectorStatus): string | null {
  if (!connector) return null;
  const rejected = connector.rejected_count ?? 0;
  const duplicates = connector.duplicate_count ?? 0;
  const notes = [
    rejected ? `${fmtInt(rejected)} rejected` : null,
    duplicates ? `${fmtInt(duplicates)} duplicate${duplicates === 1 ? "" : "s"}` : null,
  ].filter(Boolean);
  return notes.length ? notes.join(" · ") : null;
}

function percent(batch: BatchState | null): number {
  if (!batch?.total) return 0;
  if (batch.phase === "matching") return Math.max(6, Math.round((batch.matched / batch.total) * 18));
  if (batch.phase === "reconciling") return 96;
  return Math.round((batch.completed / batch.total) * 100);
}

function serviceMessage(error: unknown): string {
  if (error && typeof error === "object" && "executive" in error) {
    const executive = (error as { executive?: unknown }).executive;
    if (executive) return formatExecutiveError(executive);
  }
  const message = error instanceof Error ? error.message : String(error);
  return formatExecutiveError(message, message);
}

function connectorNeedsReview(connector?: ConnectorStatus): boolean {
  return Boolean(
    connector &&
      (connector.status === "partial" ||
        (connector.rejected_count ?? 0) > 0 ||
        (connector.duplicate_count ?? 0) > 0 ||
        (connector.blockers?.length ?? 0) > 0 ||
        connector.reconciliation_status === "needs_review"),
  );
}

function uploadReviewDetail(connector?: ConnectorStatus): string {
  const quality = connectorQualitySummary(connector);
  if (quality) return quality;
  if (connector?.blockers?.[0]) return connector.blockers[0];
  if (connector?.status === "partial") return "Loaded with validation notes";
  if (connector?.reconciliation_status === "needs_review") return "Loaded; reconciliation needs review";
  return "Loaded; review recommended";
}

function confidenceTone(score?: number | null): Tone {
  if (score == null) return "neutral";
  if (score >= 85) return "positive";
  if (score >= 65) return "warning";
  return "risk";
}

function confidenceSummary(connector?: ConnectorStatus): string | null {
  if (!connector || connector.confidence_score == null) return null;
  const reasons = connector.confidence_reasons ?? [];
  return reasons[0] ? `${fmtInt(connector.confidence_score)}% confidence · ${reasons[0]}` : `${fmtInt(connector.confidence_score)}% confidence`;
}

function sourceDetail(connector?: ConnectorStatus): string {
  if (!connector) return "";
  if (connector.workbook_name && connector.workbook_sheet) {
    return `${connector.workbook_sheet} sheet · ${connector.workbook_name}`;
  }
  return connector.source_name ?? "";
}

function isFileDrag(event: DragEvent<HTMLElement>): boolean {
  return Array.from(event.dataTransfer.types).includes("Files");
}

function isLeavingElement(event: DragEvent<HTMLElement>): boolean {
  const nextTarget = event.relatedTarget;
  return nextTarget instanceof Node && event.currentTarget.contains(nextTarget);
}

export default function DataRoomPage() {
  const reduced = Boolean(useReducedMotion());
  const [inventory, setInventory] = useState<ConnectorInventory | null>(null);
  const [reconciliation, setReconciliation] = useState<ReconciliationReport | null>(null);
  const [uploadState, setUploadState] = useState<Record<string, UploadState>>({});
  const [batch, setBatch] = useState<BatchState | null>(null);
  const [dropTarget, setDropTarget] = useState<"batch" | string | null>(null);
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

  useEffect(() => onDemoReset(() => {
    setUploadState({});
    setBatch(null);
    setDropTarget(null);
    void load();
  }), [load]);

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

  function handleBatchDragEnter(event: DragEvent<HTMLElement>) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    setDropTarget("batch");
  }

  function handleBatchDragOver(event: DragEvent<HTMLElement>) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setDropTarget("batch");
  }

  function handleBatchDragLeave(event: DragEvent<HTMLElement>) {
    if (isLeavingElement(event)) return;
    setDropTarget(null);
  }

  function handleBatchDrop(event: DragEvent<HTMLElement>) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    const files = Array.from(event.dataTransfer.files);
    if (files.length) void uploadFiles(files);
    setDropTarget(null);
  }

  async function uploadFileFor(connectorId: string, file: File): Promise<UploadOutcome> {
    const previousStatus = uploadState[connectorId]?.status;
    const fileName = file.name;
    setUploadState((prev) => ({
      ...prev,
      [connectorId]: { status: "matched", detail: `Matched ${fileName}`, fileName, file },
    }));
    await new Promise((resolve) => window.setTimeout(resolve, 120));
    setUploadState((prev) => ({
      ...prev,
      [connectorId]: { status: "parsing", detail: fileName, fileName, file },
    }));
    try {
      const result = await api.uploadConnectorFile(connectorId, file);
      const connector = result.connectors.find((item) => item.connector_id === connectorId);
      const needsReview = connectorNeedsReview(connector);
      setInventory({ mode: "strict-live", connectors: result.connectors, confidence: result.confidence });
      setReconciliation(result.reconciliation);
      const status = needsReview ? "review" : "loaded";
      const detail = needsReview ? uploadReviewDetail(connector) : fileName;
      setUploadState((prev) => ({
        ...prev,
        [connectorId]: {
          status,
          detail,
          fileName,
          file,
        },
      }));
      setBatch((prev) => {
        if (!prev?.done || previousStatus === status) return prev;
        const failed = previousStatus === "error" ? Math.max(0, prev.failed - 1) : prev.failed;
        const reviewBase = previousStatus === "review" ? Math.max(0, prev.review - 1) : prev.review;
        const review = status === "review" ? reviewBase + 1 : reviewBase;
        return {
          ...prev,
          failed,
          review,
          current: failed
            ? "Open failed rows and try again"
            : review
              ? "Recovered with review notes"
              : "Recovered and reconciled",
        };
      });
      setError(null);
      return { connectorId, fileName, status, detail };
    } catch (err) {
      const message = serviceMessage(err instanceof Error ? err.message : String(err));
      setUploadState((prev) => ({
        ...prev,
        [connectorId]: { status: "error", detail: message, fileName, file },
      }));
      setBatch((prev) => {
        if (!prev?.done || previousStatus === "error") return prev;
        const review = previousStatus === "review" ? Math.max(0, prev.review - 1) : prev.review;
        return {
          ...prev,
          failed: prev.failed + 1,
          review,
          current: "Retry failed; open the row and try again",
        };
      });
      setError(message);
      return { connectorId, fileName, status: "error", detail: message };
    }
  }

  async function uploadWorkbook(file: File): Promise<{ failed: number; review: number; completed: number }> {
    const fileName = file.name;
    setUploadState((prev) => {
      const next = { ...prev };
      for (const connectorId of ORDER) {
        next[connectorId] = { status: "matched", detail: `Workbook ${fileName}`, fileName, file };
      }
      return next;
    });
    await new Promise((resolve) => window.setTimeout(resolve, 140));
    setUploadState((prev) => {
      const next = { ...prev };
      for (const connectorId of ORDER) {
        next[connectorId] = { status: "parsing", detail: "Detecting workbook sheets", fileName, file };
      }
      return next;
    });
    try {
      const result = await api.uploadWorkbookFile(file);
      setInventory({ mode: "strict-live", connectors: result.connectors, confidence: result.confidence });
      setReconciliation(result.reconciliation);

      const rowUpdates = result.connectors.map((connector) => {
        const needsReview = connectorNeedsReview(connector);
        const hasData = loadedStatus(connector.status);
        const status: UploadState["status"] = hasData ? (needsReview ? "review" : "loaded") : "error";
        return {
          connector,
          status,
          detail: hasData
            ? needsReview
              ? uploadReviewDetail(connector)
              : sourceDetail(connector)
            : connector.blockers?.[0] ?? "No matching worksheet detected",
        };
      });
      const failed = rowUpdates.filter((row) => row.status === "error").length;
      const review = rowUpdates.filter((row) => row.status === "review").length;
      setUploadState((prev) => {
        const next = { ...prev };
        for (const row of rowUpdates) {
          next[row.connector.connector_id] = {
            status: row.status,
            detail: row.detail,
            fileName,
            file,
          };
        }
        return next;
      });
      setError(failed ? `${failed} workbook sheet${failed === 1 ? "" : "s"} need review before import.` : null);
      return { failed, review, completed: ORDER.length };
    } catch (err) {
      const message = serviceMessage(err instanceof Error ? err.message : String(err));
      setUploadState((prev) => {
        const next = { ...prev };
        for (const connectorId of ORDER) {
          next[connectorId] = { status: "error", detail: message, fileName, file };
        }
        return next;
      });
      setError(message);
      return { failed: ORDER.length, review: 0, completed: ORDER.length };
    }
  }

  async function uploadFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;

    setError(null);
    setDropTarget(null);

    const workbookFiles = list.filter((file) => isWorkbookFile(file) && (list.length === 1 || file.name.toLowerCase().includes("workbook")));
    if (workbookFiles.length) {
      setBatch({
        total: ORDER.length * workbookFiles.length,
        matched: ORDER.length * workbookFiles.length,
        completed: 0,
        failed: 0,
        review: 0,
        phase: "matching",
        current: workbookFiles.length === 1 ? "Workbook matched" : `${workbookFiles.length} workbooks matched`,
      });
      let failed = 0;
      let review = 0;
      let completed = 0;
      for (const workbook of workbookFiles) {
        setBatch({
          total: ORDER.length * workbookFiles.length,
          matched: ORDER.length * workbookFiles.length,
          completed,
          failed,
          review,
          phase: "parsing",
          current: `Reading ${workbook.name}`,
        });
        const outcome = await uploadWorkbook(workbook);
        failed += outcome.failed;
        review += outcome.review;
        completed += outcome.completed;
      }
      setBatch({
        total: ORDER.length * workbookFiles.length,
        matched: ORDER.length * workbookFiles.length,
        completed,
        failed,
        review,
        phase: "done",
        current: failed
          ? "Some workbook sheets need attention"
          : review
            ? "Workbook loaded with review notes"
            : "Workbook reconciliation complete",
        done: true,
      });
      return;
    }

    const matched: MatchedUpload[] = [];
    const unmatched: File[] = [];
    for (const file of list) {
      const connectorId = inferConnectorId(file.name);
      if (connectorId) matched.push({ connectorId, file });
      else unmatched.push(file);
    }

    setBatch({
      total: list.length,
      matched: matched.length,
      completed: 0,
      failed: unmatched.length,
      review: 0,
      phase: "matching",
      current: unmatched.length ? `${matched.length}/${list.length} files matched` : `${matched.length} files matched`,
    });

    setUploadState((prev) => {
      const next = { ...prev };
      for (const { connectorId, file } of matched) {
        next[connectorId] = { status: "matched", detail: `Matched ${file.name}`, fileName: file.name, file };
      }
      return next;
    });

    if (unmatched.length) {
      setError(`Review file names: ${unmatched.map((file) => file.name).join(", ")}`);
    }

    await new Promise((resolve) => window.setTimeout(resolve, 160));

    let failed = unmatched.length;
    let review = 0;
    let completed = unmatched.length;
    for (const { connectorId, file } of matched) {
      setBatch({ total: list.length, matched: matched.length, completed, failed, review, phase: "parsing", current: file.name });
      const outcome = await uploadFileFor(connectorId, file);
      if (outcome.status === "error") failed += 1;
      if (outcome.status === "review") review += 1;
      completed += 1;
      setBatch({ total: list.length, matched: matched.length, completed, failed, review, phase: "parsing", current: file.name });
    }

    setBatch({ total: list.length, matched: matched.length, completed, failed, review, phase: "reconciling", current: "Reconciling loaded files" });
    await new Promise((resolve) => window.setTimeout(resolve, 180));
    setBatch({
      total: list.length,
      matched: matched.length,
      completed,
      failed,
      review,
      phase: "done",
      current: failed
        ? "Open failed rows and try again"
        : review
          ? "Loaded with review notes"
          : "Reconciliation complete",
      done: true,
    });
  }

  return (
    <main className="mx-auto flex w-full max-w-[1180px] flex-col gap-4 px-4 py-5 sm:px-6">
      <Stagger className="flex flex-col gap-4">
      <StaggerItem className="grid gap-4 border-b border-border pb-5 lg:grid-cols-[minmax(0,1fr)_360px] lg:items-end">
        <div className="flex min-w-0 items-start gap-3">
          <AtlasIcon name="upload" size="lg" className="mt-1 hidden sm:inline-grid" />
          <div className="min-w-0">
            <h1 className="font-display text-[28px] font-medium tracking-tight">Add the company files</h1>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 lg:justify-end">
          <motion.label
            htmlFor={BATCH_UPLOAD_INPUT_ID}
            className="inline-flex h-10 cursor-pointer items-center justify-center gap-2 rounded-lg bg-accent px-4 text-[13px] font-semibold text-accent-foreground"
            whileHover={reduced ? undefined : hoverLift}
            whileTap={reduced ? undefined : pressTap}
            transition={springSnappy}
          >
            <Upload className="h-4 w-4" strokeWidth={2} />
            Choose files
          </motion.label>
          <MotionLink
            href="/decisions"
            className={cx(
              "inline-flex h-11 shrink-0 items-center justify-center gap-2.5 rounded-lg border px-5 text-[13px] font-semibold transition-colors",
              readyToRun
                ? "border-border bg-surface text-foreground hover:bg-surface-muted"
                : "border-border bg-surface text-muted-foreground",
            )}
          >
            <Play className="h-4 w-4 shrink-0" strokeWidth={2} />
            Run council
          </MotionLink>
          <motion.button
            type="button"
            onClick={() => void load()}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-border bg-surface px-3 text-[13px] font-semibold text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground"
            whileHover={reduced ? undefined : hoverLift}
            whileTap={reduced ? undefined : pressTap}
            transition={springSnappy}
          >
            {refreshing ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <motion.span animate={{ rotate: 0 }} whileHover={reduced ? undefined : { rotate: 180 }} transition={transitionReveal}>
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
          className={cx(
            "command-surface relative overflow-hidden transition-colors",
            dropTarget === "batch" && "border-info bg-info-bg/35",
          )}
          data-dashboard-upload-dropzone="true"
          data-drop-active={dropTarget === "batch" ? "true" : "false"}
          onDragEnter={handleBatchDragEnter}
          onDragOver={handleBatchDragOver}
          onDragLeave={handleBatchDragLeave}
          onDrop={handleBatchDrop}
          layout
          transition={springSnappy}
        >
          <AnimatePresence>
            {dropTarget === "batch" && (
              <motion.div
                className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-info-bg/95 px-4 text-center"
                initial={{ opacity: 0.92 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={transitionFadeFast}
              >
                <motion.div
                  className="rounded-lg border border-info/40 bg-surface px-4 py-3 text-[13px] font-semibold text-info shadow-[var(--shadow-soft)]"
                  initial={{ y: 8, scale: 0.98 }}
                  animate={{ y: 0, scale: 1 }}
                  exit={{ y: -6, scale: 0.98 }}
                  transition={springSnappy}
                >
                  Drop files to match connectors
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>
          <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
            <div className="flex min-w-0 items-center gap-2">
              <AtlasIcon name="upload" size="sm" className="atlas-icon-badge--quiet" />
              <div className="min-w-0">
                <h2 className="text-[15px] font-semibold">Files to load</h2>
              </div>
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
                icon={row.icon}
                label={row.label}
                expected={row.expected}
                connector={row.connector}
                state={uploadState[row.id]}
                onFile={(file) => void uploadFileFor(row.id, file)}
                onFiles={(files) => void uploadFiles(files)}
                dropActive={dropTarget === row.id}
                onDropActiveChange={(active) => setDropTarget(active ? row.id : null)}
              />
            ))}
          </div>
        </motion.div>

        <div className="flex min-w-0 flex-col gap-3">
          <DocumentEvidenceUpload />
          <ResultPanel
            report={reconciliation}
            confidence={inventory?.confidence ?? reconciliation?.confidence ?? null}
            loaded={loaded}
            total={total}
            serviceError={error}
            readyToRun={readyToRun}
          />
        </div>
      </StaggerItem>
      </Stagger>
    </main>
  );
}

function DocumentEvidenceUpload() {
  const reduced = Boolean(useReducedMotion());
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [detail, setDetail] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const job = await api.parseJob(jobId);
        if (cancelled) return;
        setStatus(job.status);
        if (job.error) setDetail(formatExecutiveError({ code: job.error_code ?? "parse_failed", message: job.error }));
        else if (job.doc_id) setDetail(`Indexed as ${job.doc_id}`);
        if (!["ready", "needs_review", "failed"].includes(job.status)) return;
      } catch (err) {
        if (!cancelled) setDetail(serviceMessage(err));
      }
    };
    void poll();
    const id = window.setInterval(() => void poll(), 1200);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [jobId]);

  async function onUpload(file: File) {
    setBusy(true);
    setDetail(null);
    try {
      const result = await api.uploadDocument(file);
      setJobId(result.parse_job.job_id);
      setStatus(result.parse_job.status);
      setDetail(`Parsing ${file.name}`);
    } catch (err) {
      setDetail(serviceMessage(err));
      setStatus("failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="command-surface p-4">
      <div className="flex items-center gap-2">
        <AtlasIcon name="evidence" size="sm" className="atlas-icon-badge--quiet" />
        <div>
          <h2 className="text-[15px] font-semibold">Council documents</h2>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <motion.label
          htmlFor="atlas-document-upload"
          className="inline-flex h-9 cursor-pointer items-center justify-center gap-2 rounded-lg border border-border bg-surface px-3 text-[12px] font-semibold"
          whileHover={reduced ? undefined : hoverLift}
          whileTap={reduced ? undefined : pressTap}
          transition={springSnappy}
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
          Upload document
        </motion.label>
        {status && <TonePill tone={status === "failed" ? "risk" : status === "needs_review" ? "warning" : "info"}>{status}</TonePill>}
      </div>
      {detail && <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">{detail}</p>}
      <input
        id="atlas-document-upload"
        className="file-input-offscreen"
        type="file"
        accept=".pdf,.docx,.csv,.json,.jsonl,.txt,.md"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void onUpload(file);
          event.currentTarget.value = "";
        }}
      />
    </section>
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
    ? batch.phase === "matching"
      ? `Matched ${batch.matched}/${batch.total}`
      : batch.phase === "reconciling"
        ? "Reconciling uploads"
        : batch.done
          ? batch.failed
            ? `${batch.failed} file${batch.failed === 1 ? " needs" : "s need"} recovery`
            : batch.review
              ? `${batch.review} file${batch.review === 1 ? " needs" : "s need"} review`
              : "Reconciliation complete"
          : `Parsing ${batch.completed}/${batch.total}`
    : refreshing
      ? "Checking current files"
      : `${loaded}/${total} files loaded`;
  const detail = batch?.current ?? (loaded === total && total > 0 ? "Ready to run the council" : "Upload the full folder for the cleanest analysis.");
  const barClass = batch?.failed
    ? "bg-risk"
    : batch?.review
      ? "bg-warning"
      : batch?.done
        ? "bg-positive"
        : batch?.phase === "matching"
          ? "bg-info"
          : "bg-accent";
  const active = Boolean(batch && !batch.done);
  const iconName: AtlasIconName = batch?.failed
    ? "risk"
    : batch?.review
      ? "evidence"
      : active
      ? "upload"
      : value === 100
        ? "reconcile"
        : "memory";

  return (
    <motion.section
      className="command-surface relative overflow-hidden p-4"
      data-batch-phase={batch?.phase ?? "idle"}
      data-batch-progress={value}
      layout
      animate={
        active && !reduced
          ? { boxShadow: ["var(--shadow-soft)", "0 0 0 1px color-mix(in srgb, var(--accent) 25%, transparent), 0 12px 40px color-mix(in srgb, var(--accent) 12%, transparent)"] }
          : { boxShadow: "var(--shadow-soft)" }
      }
      transition={active ? { duration: 1.8, repeat: Infinity, repeatType: "reverse", ease: "easeInOut" } : springSnappy}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-3">
          <AtlasIcon name={iconName} size="sm" className="atlas-icon-badge--quiet" />
          <div className="min-w-0">
          <AnimatePresence mode="wait">
            <motion.div
              key={label}
              className="text-[13px] font-semibold"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={transitionFade}
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
              transition={transitionFade}
            >
              {detail}
            </motion.div>
          </AnimatePresence>
          </div>
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
          transition={reduced ? { duration: motionDuration.quick } : springBar}
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
  icon,
  index,
  label,
  expected,
  connector,
  state,
  onFile,
  onFiles,
  dropActive,
  onDropActiveChange,
}: {
  id: string;
  icon: AtlasIconName;
  index: number;
  label: string;
  expected: string;
  connector?: ConnectorStatus;
  state?: UploadState;
  onFile: (file: File) => void;
  onFiles: (files: File[]) => void;
  dropActive: boolean;
  onDropActiveChange: (active: boolean) => void;
}) {
  const reduced = useReducedMotion();
  const inputId = `upload-${id}`;
  const status = connector?.status;
  const phase = state?.status;
  const active = phase === "matched" || phase === "parsing";
  const justDone = phase === "loaded";
  const needsReview = phase === "review" || connectorNeedsReview(connector);
  const failed = phase === "error" || status === "error";
  const tone = rowTone(status, state);
  const detail = dropActive ? `Drop ${expected} file here` : state?.detail ?? sourceDetail(connector);
  const statusKey = needsReview && !failed && !active ? "Needs review" : rowLabel(status, state);
  const isLoaded = loadedStatus(status);
  const showChoose = !isLoaded || failed || needsReview;
  const retryFile = (failed || phase === "review") && state?.file ? state.file : null;
  const StatusIcon = failed
    ? AlertTriangle
    : needsReview
      ? FileQuestion
      : active
        ? Loader2
        : isLoaded || phase === "loaded"
          ? FileCheck2
          : Upload;

  function handleRowDragEnter(event: DragEvent<HTMLElement>) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    onDropActiveChange(true);
  }

  function handleRowDragOver(event: DragEvent<HTMLElement>) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
    onDropActiveChange(true);
  }

  function handleRowDragLeave(event: DragEvent<HTMLElement>) {
    event.stopPropagation();
    if (isLeavingElement(event)) return;
    onDropActiveChange(false);
  }

  function handleRowDrop(event: DragEvent<HTMLElement>) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    const files = Array.from(event.dataTransfer.files);
    onDropActiveChange(false);
    if (files.length > 1) onFiles(files);
    else if (files[0]) onFile(files[0]);
  }

  return (
    <motion.div
      className={cx(
        "relative grid min-h-[78px] gap-3 px-4 py-3 transition-colors sm:grid-cols-[minmax(0,1fr)_150px_170px] sm:items-center",
        dropActive && "bg-info-bg/45",
      )}
      data-upload-row={id}
      data-upload-status={phase ?? (isLoaded ? "loaded" : status ?? "missing_file")}
      data-drop-active={dropActive ? "true" : "false"}
      onDragEnter={handleRowDragEnter}
      onDragOver={handleRowDragOver}
      onDragLeave={handleRowDragLeave}
      onDrop={handleRowDrop}
      layout
      initial={reduced ? false : { opacity: 0, x: -16 }}
      animate={{
        opacity: 1,
        x: 0,
        backgroundColor: dropActive
          ? "color-mix(in srgb, var(--info-bg) 68%, transparent)"
          : active
          ? "color-mix(in srgb, var(--info-bg) 55%, transparent)"
          : justDone
            ? "color-mix(in srgb, var(--positive-bg) 45%, transparent)"
            : needsReview
              ? "color-mix(in srgb, var(--warning-bg) 34%, transparent)"
            : "transparent",
      }}
      transition={{ ...springSnappy, delay: staggerDelay(index, 0.04, 0.18) }}
    >
      <AnimatePresence>
        {(justDone || needsReview || dropActive) && !reduced && (
          <motion.span
            className={cx(
              "pointer-events-none absolute inset-0",
              justDone ? "bg-positive/10" : dropActive ? "bg-info/10" : "bg-warning/10",
            )}
            initial={{ opacity: 0.8, scaleX: 0, originX: 0 }}
            animate={{ opacity: 0, scaleX: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: motionDuration.emphasis, ease: EASE_OUT_EXPO }}
          />
        )}
      </AnimatePresence>
      <div className="relative min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <motion.div
            className="relative shrink-0"
            animate={active && !reduced ? { scale: [1, 1.08, 1] } : { scale: 1 }}
            transition={{ duration: 1.2, repeat: active && !reduced ? Infinity : 0, ease: "easeInOut" }}
          >
            <AtlasIcon name={icon} size="xs" className="atlas-icon-badge--quiet" />
            <StatusDot tone={tone} className="absolute -bottom-0.5 -right-0.5 ring-2 ring-surface" />
          </motion.div>
          <div className="text-[14px] font-semibold text-foreground">{label}</div>
          <AnimatePresence mode="wait">
            <motion.span key={statusKey} initial={{ opacity: 0, y: 2 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -2 }} transition={transitionFadeFast}>
              <TonePill tone={tone}>{statusKey}</TonePill>
            </motion.span>
          </AnimatePresence>
        </div>
        <div
          className={cx(
            "mt-1 line-clamp-1 text-[12px]",
            failed ? "text-risk" : needsReview ? "text-warning" : "text-muted-foreground",
          )}
        >
          {detail || `${expected} file`}
        </div>
        {(connectorQualitySummary(connector) || connector?.blockers?.[0]) && (
          <div className="mt-1 line-clamp-1 text-[12px] text-warning">
            {connectorQualitySummary(connector) ?? connector?.blockers?.[0]}
          </div>
        )}
        {confidenceSummary(connector) && (
          <div className="mt-1 flex min-w-0 items-center gap-1.5 text-[11.5px] text-subtle-foreground">
            <StatusDot tone={confidenceTone(connector?.confidence_score)} />
            <span className="line-clamp-1">{confidenceSummary(connector)}</span>
          </div>
        )}
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
      {showChoose && (
        <div className="flex flex-wrap items-center gap-2 sm:justify-end">
          <input
            id={inputId}
            className="file-input-offscreen"
            type="file"
            accept={FILE_ACCEPT}
            disabled={active}
            onChange={(event) => {
              const [file] = Array.from(event.target.files ?? []);
              if (file) onFile(file);
              event.currentTarget.value = "";
            }}
          />
          {retryFile && (
            <motion.button
              type="button"
              className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-border bg-surface px-3 text-[12px] font-semibold text-foreground transition-colors hover:bg-surface-muted"
              onClick={() => onFile(retryFile)}
              whileHover={active || reduced ? undefined : hoverLift}
              whileTap={active || reduced ? undefined : pressTap}
              transition={springSnappy}
              disabled={active}
            >
              <RotateCcw className="h-4 w-4" strokeWidth={2} />
              Retry
            </motion.button>
          )}
          <motion.label
            htmlFor={inputId}
            className={cx(
              "inline-flex h-9 cursor-pointer items-center justify-center gap-2 rounded-lg border border-border bg-surface px-3 text-[12px] font-semibold transition-colors hover:bg-surface-muted",
              active && "pointer-events-none opacity-60",
            )}
            whileHover={active || reduced ? undefined : hoverLift}
            whileTap={active || reduced ? undefined : pressTap}
            transition={springSnappy}
          >
            <AnimatePresence mode="wait">
              <motion.span
                key={phase ?? "idle"}
                initial={{ opacity: 0, rotate: -90 }}
                animate={{ opacity: 1, rotate: 0 }}
                exit={{ opacity: 0, rotate: 90 }}
                transition={transitionFade}
                className="inline-flex"
              >
                <StatusIcon className={cx("h-4 w-4", active && !reduced && "animate-spin")} strokeWidth={2} />
              </motion.span>
            </AnimatePresence>
            {isLoaded || needsReview ? "Replace" : "Choose"}
          </motion.label>
        </div>
      )}
    </motion.div>
  );
}

function ResultPanel({
  report,
  confidence,
  loaded,
  total,
  serviceError,
  readyToRun,
}: {
  report: ReconciliationReport | null;
  confidence: ImportConfidence | null;
  loaded: number;
  total: number;
  serviceError: string | null;
  readyToRun: boolean;
}) {
  const reduced = Boolean(useReducedMotion());
  const issues = (report?.discrepancies ?? []).filter((item) => item.severity !== "info");
  const ready = loaded === total && total > 0;
  const offline = Boolean(serviceError && /demo service is offline/i.test(serviceError));
  const stateKey = offline ? "offline" : ready ? (issues.length ? "review" : "ready") : loaded ? "partial" : "waiting";
  const stateIcon: AtlasIconName = offline
    ? "risk"
    : ready
      ? issues.length
        ? "evidence"
        : "reconcile"
      : loaded
        ? "upload"
        : "memory";

  return (
    <motion.aside
      className="command-surface flex min-h-[360px] flex-col p-4"
      layout
      initial={reduced ? false : { opacity: 0, x: 16 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ ...springSnappy, delay: 0.12 }}
    >
      <div className="flex items-center gap-2">
        <AtlasIcon name={stateIcon} size="sm" className="atlas-icon-badge--quiet" />
        <h2 className="text-[15px] font-semibold">File readiness</h2>
      </div>
      <div className="mt-3 rounded-lg border border-border bg-background p-3">
        <AnimatePresence mode="wait">
          <motion.div
            key={stateKey}
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: 8, filter: "blur(3px)" }}
            animate={reduced ? { opacity: 1 } : { opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: -6, filter: "blur(2px)" }}
            transition={transitionReveal}
          >
            {offline ? (
              <StateMessage
                tone="risk"
                title="The demo service is not connected"
                detail="Uploads need the agent server before files can load."
              />
            ) : ready ? (
              <StateMessage
                tone={issues.length ? "warning" : "positive"}
                title={issues.length ? "Reconciliation complete. Review the notes." : "Reconciliation complete."}
                detail={
                  issues.length
                    ? `Atlas Finance found ${fmtInt(issues.length)} item${issues.length === 1 ? "" : "s"} to review before the run.`
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
              <StateMessage tone="neutral" title="Waiting for files" detail="Choose the company files to begin." />
            )}
          </motion.div>
        </AnimatePresence>
      </div>

      {confidence && (
        <motion.div
          className="mt-3 rounded-lg border border-border bg-background p-3"
          layout
          initial={reduced ? false : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={springSnappy}
          data-dashboard-confidence={confidence.score}
        >
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="text-[12px] font-semibold text-foreground">Data confidence</div>
              <div className="mt-0.5 line-clamp-2 text-[11.5px] leading-relaxed text-muted-foreground">
                {confidence.detail}
              </div>
            </div>
            <TonePill tone={confidenceTone(confidence.score)}>{fmtInt(confidence.score)}%</TonePill>
          </div>
          <div className="mt-3 grid gap-1.5 [grid-template-columns:repeat(2,minmax(0,1fr))]">
            <ConfidenceMetric label="Valid rows" value={`${Math.round((confidence.validation_pass_rate ?? 0) * 100)}%`} />
            <ConfidenceMetric label="Coverage" value={`${confidence.sources_imported}/${confidence.sources_total}`} />
            <ConfidenceMetric label="Duplicates" value={fmtInt(confidence.duplicate_count ?? 0)} />
            <ConfidenceMetric label="Reconcile" value={fmtInt(confidence.reconciliation_discrepancy_count ?? issues.length)} />
          </div>
          {(confidence.confidence_reasons ?? []).length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {(confidence.confidence_reasons ?? []).slice(0, 3).map((reason) => (
                <span key={reason} className="rounded border border-warning/25 bg-warning-bg/25 px-1.5 py-0.5 text-[10.5px] font-medium text-warning">
                  {reason}
                </span>
              ))}
            </div>
          )}
        </motion.div>
      )}

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
              className="flex min-h-[140px] flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-surface-quiet p-4 text-center text-[12px] text-muted-foreground"
              initial={{ opacity: 0, scale: 0.98 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.98 }}
            >
              <AtlasIcon name="reconcile" size="md" className="atlas-icon-badge--quiet" />
              <span>Notes from the file check will appear here.</span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div className="mt-4 border-t border-border pt-4">
        <div className="flex items-center justify-between gap-2 text-[12px] text-muted-foreground">
          <span>File check</span>
          <span>{offline ? "Offline" : ready ? (issues.length ? "Review notes" : "Reconciled") : loaded ? "Waiting" : "Not started"}</span>
        </div>
        <PopIn show={readyToRun} className="mt-4 w-full">
          <MotionLink
            href="/decisions"
            className="inline-flex h-11 w-full items-center justify-center gap-2.5 rounded-lg bg-accent px-5 py-2.5 text-[13px] font-semibold text-accent-foreground"
          >
            <Play className="h-4 w-4 shrink-0" strokeWidth={2} />
            Run council
          </MotionLink>
        </PopIn>
        {!readyToRun && (
          <div className="mt-4 inline-flex h-11 w-full items-center justify-center gap-2.5 rounded-lg bg-surface-muted px-5 py-2.5 text-[13px] font-semibold text-muted-foreground">
            <Play className="h-4 w-4 shrink-0" strokeWidth={2} />
            Run council
          </div>
        )}
      </div>
    </motion.aside>
  );
}

function ConfidenceMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border/70 bg-surface/70 px-2 py-1.5">
      <div className="text-[10px] font-medium uppercase text-subtle-foreground">{label}</div>
      <div className="mt-0.5 truncate text-[12px] font-semibold tabular-nums text-foreground">{value}</div>
    </div>
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
  const reduced = Boolean(useReducedMotion());
  const tone: Tone = item.severity === "critical" || item.severity === "high" ? "risk" : item.severity === "medium" ? "warning" : "info";
  const iconName: AtlasIconName = tone === "risk" ? "risk" : tone === "warning" ? "evidence" : "health";
  return (
    <motion.li
      className="rounded-lg border border-border bg-background p-3"
      variants={{ hidden: { opacity: 0, x: 12 }, show: { opacity: 1, x: 0 } }}
      whileHover={reduced ? undefined : hoverNudge}
      transition={springSnappy}
    >
      <div className="flex items-start justify-between gap-2">
        <AtlasIcon name={iconName} size="xs" className="atlas-icon-badge--quiet" />
        <div className="min-w-0">
          <div className="line-clamp-1 text-[12px] font-semibold text-foreground">{item.title}</div>
          <div className="mt-1 line-clamp-2 text-[11.5px] leading-relaxed text-muted-foreground">{item.detail}</div>
        </div>
        <TonePill tone={tone}>{item.severity}</TonePill>
      </div>
    </motion.li>
  );
}
