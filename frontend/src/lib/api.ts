import type {
  CommandResult,
  CommandState,
  CompanyFinancials,
  ConnectorImportResponse,
  ConnectorInventory,
  DemoResetResponse,
  DecisionEvent,
  Discrepancy,
  ObservabilitySnapshot,
  OperatorCommand,
  RealtimeSession,
  ReconciliationReport,
  RosterMember,
  SourceDetail,
  SourceProvenance,
  SponsorHealth,
  Vendor,
  WorkbookImportResponse,
  BoardNarrative,
  DecisionPortfolio,
  PlanCard,
  PlaybookCatalogEntry,
  SensitivityResult,
  SensitivitySuite,
  StrategicPlan,
  StressTest,
  ApprovalMatrix,
  AuditEvent,
  GovernanceState,
  Obligation,
  PolicyRule,
} from "./types";

import { agentBase } from "./agent-base";
import { formatExecutiveError, parseExecutiveError, type ExecutiveErrorPayload } from "./errors";

const BASE = agentBase();

export class ApiRequestError extends Error {
  status: number;
  path: string;
  executive?: ExecutiveErrorPayload;

  constructor(path: string, status: number, message: string, executive?: ExecutiveErrorPayload) {
    super(message);
    this.name = "ApiRequestError";
    this.path = path;
    this.status = status;
    this.executive = executive;
  }
}

async function responseError(path: string, res: Response): Promise<ApiRequestError> {
  let detail: unknown = "";
  try {
    const body = (await res.json()) as { detail?: unknown; message?: unknown };
    detail = body.detail ?? body.message ?? "";
  } catch {
    try {
      detail = await res.text();
    } catch {
      detail = "";
    }
  }
  const executive = parseExecutiveError(detail);
  const message = executive
    ? formatExecutiveError(executive)
    : formatExecutiveError(detail, `${path} failed (${res.status})`);
  return new ApiRequestError(path, res.status, message, executive ?? undefined);
}

function networkError(path: string, err: unknown): Error {
  const message = err instanceof Error ? err.message : String(err);
  if (/failed to fetch|load failed|networkerror|network error/i.test(message)) {
    return new Error(`The demo service is offline. Start the demo server, then try again. (${BASE}${path})`);
  }
  return err instanceof Error ? err : new Error(message);
}

async function getJSON<T>(path: string): Promise<T> {
  try {
    const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
    if (!res.ok) throw await responseError(path, res);
    return res.json() as Promise<T>;
  } catch (err) {
    throw networkError(path, err);
  }
}

async function postJSON<T>(path: string): Promise<T> {
  try {
    const res = await fetch(`${BASE}${path}`, { method: "POST", cache: "no-store" });
    if (!res.ok) throw await responseError(path, res);
    return res.json() as Promise<T>;
  } catch (err) {
    throw networkError(path, err);
  }
}

async function postForm<T>(path: string, formData: FormData): Promise<T> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      cache: "no-store",
      body: formData,
    });
    if (!res.ok) throw await responseError(path, res);
    return res.json() as Promise<T>;
  } catch (err) {
    throw networkError(path, err);
  }
}

// Readiness payloads come back with the full body even on 503 (not-ready), so the
// dashboard can surface the live health gate instead of failing. Network errors still throw.
async function getJSONAllowNotReady<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  return res.json() as Promise<T>;
}

// POST a JSON body. The command engine returns a full structured envelope even
// when it refuses a command (HTTP 503 for not-live), so we keep the body on 503
// and only throw on transport / unexpected (5xx) failures.
async function postJSONBody<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok && res.status !== 503) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  company: () => getJSON<CompanyFinancials>("/api/company"),
  vendors: () => getJSON<Vendor[]>("/api/vendors"),
  decisions: () => getJSON<DecisionEvent[]>("/api/decisions"),
  roster: () => getJSON<RosterMember[]>("/api/roster"),
  health: () => getJSON<SponsorHealth>("/api/health"),
  // Tolerant variant for the dashboard readiness strip: keeps the payload on 503.
  healthSnapshot: () => getJSONAllowNotReady<SponsorHealth>("/api/health"),
  observability: () => getJSON<ObservabilitySnapshot>("/api/observability"),
  realtimeSession: () => postJSON<RealtimeSession>("/api/realtime/session"),
  // AG-UI command-and-control channel (server-authoritative dispatcher).
  command: (body: OperatorCommand) => postJSONBody<CommandResult>("/api/command", body),
  commandState: (room?: string) =>
    getJSON<{ room: string; state: CommandState }>(
      `/api/command/state${room ? `?room=${encodeURIComponent(room)}` : ""}`,
    ),
  // Finance-operations connectors: ingestion status, source inventory, reconciliation.
  connectors: () => getJSON<ConnectorInventory>("/api/connectors"),
  uploadConnectorFile: (id: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return postForm<ConnectorImportResponse>(`/api/connectors/import/${encodeURIComponent(id)}`, formData);
  },
  uploadWorkbookFile: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return postForm<WorkbookImportResponse>("/api/connectors/import-workbook", formData);
  },
  sources: () => getJSON<{ count: number; sources: SourceProvenance[] }>("/api/sources"),
  sourceDetail: (id: string, sample = 10) =>
    getJSON<SourceDetail>(`/api/sources/${encodeURIComponent(id)}?sample=${sample}`),
  reconciliation: () => getJSON<ReconciliationReport>("/api/reconciliation"),
  runReconciliation: () => postJSON<ReconciliationReport>("/api/reconciliation/run"),
  discrepancies: (severity?: string, kind?: string) => {
    const qs = new URLSearchParams();
    if (severity) qs.set("severity", severity);
    if (kind) qs.set("kind", kind);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return getJSON<{ count: number; discrepancies: Discrepancy[] }>(
      `/api/reconciliation/discrepancies${suffix}`,
    );
  },
  discrepancy: (id: string) =>
    getJSON<Discrepancy>(`/api/reconciliation/discrepancies/${encodeURIComponent(id)}`),
  resetDemo: () => postJSON<DemoResetResponse>("/api/demo/reset"),
  // Strategic planning digital twin: plans, playbooks, stress tests, sensitivity,
  // portfolios, and (model-generated) board narratives. The compute calls are
  // deterministic; planNarrative is the one OpenAI-backed call.
  playbooks: () => getJSON<PlaybookCatalogEntry[]>("/api/playbooks"),
  plans: (limit = 25) => getJSON<PlanCard[]>(`/api/plans?limit=${limit}`),
  plan: (id: string) => getJSON<StrategicPlan>(`/api/plans/${encodeURIComponent(id)}`),
  createPlan: (
    body: {
      horizon_months?: number;
      playbook?: string;
      decision?: string;
      title?: string;
      assumptions_overrides?: Record<string, number>;
    } = {},
  ) => postJSONBody<StrategicPlan>("/api/plans", body),
  stressPlan: (id: string, body: { trials?: number; seed?: number; horizon_months?: number } = {}) =>
    postJSONBody<StressTest>(`/api/plans/${encodeURIComponent(id)}/stress`, body),
  planNarrative: (id: string, refresh = false) =>
    getJSON<BoardNarrative>(
      `/api/plans/${encodeURIComponent(id)}/narrative${refresh ? "?refresh=true" : ""}`,
    ),
  sensitivity: (variable?: string, horizonMonths = 12, outputMetric = "min_cash") => {
    const qs = new URLSearchParams({
      horizon_months: String(horizonMonths),
      output_metric: outputMetric,
    });
    if (variable) qs.set("variable", variable);
    return getJSON<SensitivityResult | SensitivitySuite>(`/api/sensitivity?${qs.toString()}`);
  },
  comparePlaybooks: (body: { decision: string; playbooks?: string[]; horizon_months?: number }) =>
    postJSONBody<DecisionPortfolio>("/api/playbooks/compare", body),
  // Governance — board policies, approval requests, obligations, and the audit
  // log. Read-only here; approval decisions are recorded server-side with the
  // no-fake-human-approval guard.
  policies: () => getJSON<PolicyRule[]>("/api/policies"),
  policiesSearch: (q: string) =>
    getJSON<PolicyRule[]>(`/api/policies/search?q=${encodeURIComponent(q)}`),
  approvalMatrix: () => getJSON<ApprovalMatrix>("/api/approval-matrix"),
  approvals: (status?: string, limit = 50) => {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (status) qs.set("status", status);
    return getJSON<GovernanceState[]>(`/api/approvals?${qs.toString()}`);
  },
  approval: (id: string) => getJSON<GovernanceState>(`/api/approvals/${encodeURIComponent(id)}`),
  obligations: (status?: string, kind?: string) => {
    const qs = new URLSearchParams();
    if (status) qs.set("status", status);
    if (kind) qs.set("kind", kind);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return getJSON<Obligation[]>(`/api/obligations${suffix}`);
  },
  audit: (requestId?: string, limit = 50) => {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (requestId) qs.set("request_id", requestId);
    return getJSON<AuditEvent[]>(`/api/audit?${qs.toString()}`);
  },
  documents: (params?: { q?: string; offset?: number; limit?: number; source_category?: string }) => {
    const qs = new URLSearchParams();
    if (params?.q) qs.set("q", params.q);
    if (params?.offset != null) qs.set("offset", String(params.offset));
    if (params?.limit != null) qs.set("limit", String(params.limit));
    if (params?.source_category) qs.set("source_category", params.source_category);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return getJSON<{ count: number; total: number; offset: number; limit: number; documents: unknown[] }>(
      `/api/documents${suffix}`,
    );
  },
  uploadDocument: (file: File, connectorId?: string) => {
    const formData = new FormData();
    formData.append("file", file);
    const qs = connectorId ? `?connector_id=${encodeURIComponent(connectorId)}` : "";
    return postForm<{ parse_job: { job_id: string; status: string } }>(`/api/documents/upload${qs}`, formData);
  },
  parseJob: (jobId: string) =>
    getJSON<{ job_id: string; status: string; error?: string; error_code?: string; doc_id?: string }>(
      `/api/documents/parse-jobs/${encodeURIComponent(jobId)}`,
    ),
};
