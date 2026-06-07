import type { Tone } from "@/lib/council";

export type ExecutiveStateCode =
  | "service_offline"
  | "redis_unavailable"
  | "parse_failed"
  | "source_stale"
  | "insufficient_evidence"
  | "model_refused"
  | "reconciliation_blocked"
  | "unsupported_file"
  | "missing_fact";

export interface ExecutiveErrorPayload {
  code: ExecutiveStateCode;
  title: string;
  message: string;
  action: string;
  context?: string;
  detail_redacted?: string;
}

const CATALOG: Record<
  ExecutiveStateCode,
  { title: string; message: string; action: string; tone: Tone }
> = {
  service_offline: {
    title: "Service offline",
    message: "The Atlas agent service is not reachable.",
    action: "Start the demo server, then refresh.",
    tone: "risk",
  },
  redis_unavailable: {
    title: "Redis unavailable",
    message: "The live system of record is not reachable.",
    action: "Start Redis Stack, then refresh.",
    tone: "risk",
  },
  parse_failed: {
    title: "Parse failed",
    message: "The uploaded file could not be parsed into searchable evidence.",
    action: "Check the file format and try again.",
    tone: "risk",
  },
  source_stale: {
    title: "Source stale",
    message: "One or more imported sources are older than the freshness window.",
    action: "Re-import the source or note staleness in the brief.",
    tone: "warning",
  },
  insufficient_evidence: {
    title: "Insufficient evidence",
    message: "Not enough grounded evidence was retrieved for this step.",
    action: "Upload supporting documents or load connector sources.",
    tone: "warning",
  },
  model_refused: {
    title: "Model refused",
    message: "The model declined to complete this council step.",
    action: "Rephrase the decision and retry.",
    tone: "warning",
  },
  reconciliation_blocked: {
    title: "Reconciliation blocked",
    message: "Material discrepancies block a clean read of the data.",
    action: "Review open discrepancies before accepting the case.",
    tone: "warning",
  },
  unsupported_file: {
    title: "Unsupported file",
    message: "This file type is not accepted for import.",
    action: "Export CSV, JSON, Excel, PDF, DOCX, or plain text.",
    tone: "risk",
  },
  missing_fact: {
    title: "Missing fact",
    message: "A required fact is not present in live data.",
    action: "Load the missing connector source or upload supporting evidence.",
    tone: "warning",
  },
};

function isExecutiveCode(value: string): value is ExecutiveStateCode {
  return value in CATALOG;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : null;
}

export function parseExecutiveError(raw: unknown): ExecutiveErrorPayload | null {
  const record = asRecord(raw);
  if (!record) return null;
  const code = String(record.code ?? "");
  if (!isExecutiveCode(code)) return null;
  const meta = CATALOG[code];
  return {
    code,
    title: String(record.title ?? meta.title),
    message: String(record.message ?? meta.message),
    action: String(record.action ?? meta.action),
    context: typeof record.context === "string" ? record.context : undefined,
    detail_redacted: typeof record.detail_redacted === "string" ? record.detail_redacted : undefined,
  };
}

export function executiveErrorFromMessage(message: string): ExecutiveErrorPayload {
  const lower = message.toLowerCase();
  let code: ExecutiveStateCode = "service_offline";
  if (/redis/.test(lower)) code = "redis_unavailable";
  else if (/unsupported|not accepted/.test(lower)) code = "unsupported_file";
  else if (/parse|extract|corrupt|empty file/.test(lower)) code = "parse_failed";
  else if (/reconcil|discrepanc/.test(lower)) code = "reconciliation_blocked";
  else if (/refus/.test(lower)) code = "model_refused";
  else if (/stale|freshness/.test(lower)) code = "source_stale";
  else if (/missing fact|required fact/.test(lower)) code = "missing_fact";
  else if (/insufficient|no evidence/.test(lower)) code = "insufficient_evidence";
  const meta = CATALOG[code];
  return { code, title: meta.title, message: meta.message, action: meta.action };
}

export function formatExecutiveError(raw: unknown, fallbackMessage?: string): string {
  const parsed = parseExecutiveError(raw) ?? (fallbackMessage ? executiveErrorFromMessage(fallbackMessage) : null);
  if (!parsed) return fallbackMessage ?? "Something went wrong. Try again.";
  return `${parsed.title} — ${parsed.message} ${parsed.action}`;
}

export function executiveTone(code: ExecutiveStateCode): Tone {
  return CATALOG[code]?.tone ?? "neutral";
}
