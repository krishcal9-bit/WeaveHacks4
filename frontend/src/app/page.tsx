"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Play,
  RefreshCw,
  Upload,
} from "lucide-react";
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
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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
      <section className="grid gap-4 border-b border-border pb-5 lg:grid-cols-[minmax(0,1fr)_360px] lg:items-end">
        <div className="min-w-0">
          <h1 className="text-[26px] font-semibold tracking-tight">Add the demo files</h1>
          <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-muted-foreground">
            Choose the seven Northwind files. Atlas loads them, checks them, and then you can run the council.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 lg:justify-end">
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-accent px-4 text-[13px] font-semibold text-accent-foreground transition-opacity hover:opacity-90"
          >
            <Upload className="h-4 w-4" strokeWidth={2} />
            Choose files
          </button>
          <Link
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
          </Link>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-border bg-surface px-3 text-[13px] font-semibold text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground"
          >
            {refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Refresh
          </button>
          <input
            ref={fileInputRef}
            className="hidden"
            type="file"
            accept=".csv,.json"
            multiple
            onChange={(event) => {
              if (event.target.files) void uploadFiles(event.target.files);
              event.currentTarget.value = "";
            }}
          />
        </div>
      </section>

      {error && (
        <div className="rounded-lg border border-risk/20 bg-risk-bg px-3 py-2 text-[13px] font-medium text-risk">
          {error}
        </div>
      )}

      <UploadProgress batch={batch} loaded={loaded} total={total} refreshing={refreshing} />

      <section className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="command-surface overflow-hidden">
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
            {rows.map((row) => (
              <FileRow
                key={row.id}
                id={row.id}
                label={row.label}
                expected={row.expected}
                connector={row.connector}
                state={uploadState[row.id]}
                onFile={(file) => void uploadFileFor(row.id, file)}
              />
            ))}
          </div>
        </div>

        <ResultPanel
          report={reconciliation}
          loaded={loaded}
          total={total}
          serviceError={error}
        />
      </section>
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

  return (
    <section className="command-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[13px] font-semibold">{label}</div>
          <div className="mt-0.5 truncate text-[12px] text-muted-foreground">{detail}</div>
        </div>
        <div className="text-[18px] font-semibold tabular-nums">{value}%</div>
      </div>
      <div className="mt-3 h-3 overflow-hidden rounded-full bg-surface-muted">
        <div className={cx("h-full rounded-full transition-all duration-300", barClass)} style={{ width: `${value}%` }} />
      </div>
    </section>
  );
}

function FileRow({
  id,
  label,
  expected,
  connector,
  state,
  onFile,
}: {
  id: string;
  label: string;
  expected: string;
  connector?: ConnectorStatus;
  state?: UploadState;
  onFile: (file: File) => void;
}) {
  const inputId = `upload-${id}`;
  const status = connector?.status;
  const uploading = state?.status === "uploading";
  const tone = rowTone(status, state);
  const detail = state?.detail ?? connector?.source_name ?? "";

  return (
    <div className="grid gap-3 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_150px_122px] sm:items-center">
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <StatusDot tone={tone} />
          <div className="text-[14px] font-semibold text-foreground">{label}</div>
          <TonePill tone={tone}>{rowLabel(status, state)}</TonePill>
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
      <label
        htmlFor={inputId}
        className={cx(
          "inline-flex h-9 cursor-pointer items-center justify-center gap-2 rounded-lg border border-border bg-surface px-3 text-[12px] font-semibold transition-colors hover:bg-surface-muted",
          uploading && "pointer-events-none opacity-60",
        )}
      >
        {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
        Choose
        <input
          id={inputId}
          className="hidden"
          type="file"
          accept=".csv,.json"
          onChange={(event) => {
            const [file] = Array.from(event.target.files ?? []);
            if (file) onFile(file);
            event.currentTarget.value = "";
          }}
        />
      </label>
    </div>
  );
}

function ResultPanel({
  report,
  loaded,
  total,
  serviceError,
}: {
  report: ReconciliationReport | null;
  loaded: number;
  total: number;
  serviceError: string | null;
}) {
  const issues = (report?.discrepancies ?? []).filter((item) => item.severity !== "info");
  const ready = loaded === total && total > 0;

  return (
    <aside className="command-surface flex min-h-[360px] flex-col p-4">
      <h2 className="text-[15px] font-semibold">What happened</h2>
      <div className="mt-3 rounded-lg border border-border bg-background p-3">
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
      </div>

      <div className="mt-4 flex-1">
        {issues.length ? (
          <ul className="space-y-2">
            {issues.slice(0, 4).map((item) => (
              <IssueItem key={item.id} item={item} />
            ))}
          </ul>
        ) : (
          <div className="flex min-h-[140px] items-center justify-center rounded-lg border border-dashed border-border bg-surface-quiet p-4 text-center text-[12px] text-muted-foreground">
            Notes from the file check will appear here.
          </div>
        )}
      </div>

      <div className="mt-4 border-t border-border pt-3">
        <div className="flex items-center justify-between gap-2 text-[12px] text-muted-foreground">
          <span>File check</span>
          <span>{serviceError ? "Offline" : ready ? (issues.length ? "Review notes" : "Ready") : loaded ? "Waiting" : "Not started"}</span>
        </div>
        <Link
          href="/decisions"
          className={cx(
            "mt-3 inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg text-[13px] font-semibold transition-opacity",
            ready ? "bg-accent text-accent-foreground hover:opacity-90" : "bg-surface-muted text-muted-foreground",
          )}
        >
          <Play className="h-4 w-4" />
          Run council
        </Link>
      </div>
    </aside>
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
    <li className="rounded-lg border border-border bg-background p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="line-clamp-1 text-[12px] font-semibold text-foreground">{item.title}</div>
          <div className="mt-1 line-clamp-2 text-[11.5px] leading-relaxed text-muted-foreground">{item.detail}</div>
        </div>
        <TonePill tone={tone}>{item.severity}</TonePill>
      </div>
    </li>
  );
}
