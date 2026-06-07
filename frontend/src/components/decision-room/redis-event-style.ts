import type { Tone } from "@/lib/council";

export type RedisSignalKind =
  | "redisjson"
  | "redisearch"
  | "vector"
  | "stream"
  | "reconciliation"
  | "document"
  | "tool"
  | "warning"
  | "generic";

export interface RedisSignalMeta {
  label: string;
  shortLabel: string;
  tone: Tone;
  accentClass: string;
  softClass: string;
  borderClass: string;
  textClass: string;
}

export const REDIS_SIGNAL_ORDER: RedisSignalKind[] = [
  "redisjson",
  "redisearch",
  "vector",
  "document",
  "stream",
  "reconciliation",
  "tool",
  "warning",
  "generic",
];

export const REDIS_SIGNAL_META: Record<RedisSignalKind, RedisSignalMeta> = {
  redisjson: {
    label: "RedisJSON",
    shortLabel: "JSON",
    tone: "info",
    accentClass: "bg-info",
    softClass: "border-info/20 bg-info-bg text-info",
    borderClass: "border-info/25",
    textClass: "text-info",
  },
  redisearch: {
    label: "RediSearch",
    shortLabel: "Search",
    tone: "accent",
    accentClass: "bg-accent",
    softClass: "border-accent/20 bg-surface-muted text-foreground",
    borderClass: "border-accent/25",
    textClass: "text-foreground",
  },
  vector: {
    label: "Vector RAG",
    shortLabel: "RAG",
    tone: "positive",
    accentClass: "bg-positive",
    softClass: "border-positive/20 bg-positive-bg text-positive",
    borderClass: "border-positive/25",
    textClass: "text-positive",
  },
  stream: {
    label: "Redis Stream",
    shortLabel: "Stream",
    tone: "positive",
    accentClass: "bg-positive",
    softClass: "border-positive/20 bg-positive-bg text-positive",
    borderClass: "border-positive/25",
    textClass: "text-positive",
  },
  reconciliation: {
    label: "Reconciliation",
    shortLabel: "Recon",
    tone: "warning",
    accentClass: "bg-warning",
    softClass: "border-warning/20 bg-warning-bg text-warning",
    borderClass: "border-warning/25",
    textClass: "text-warning",
  },
  document: {
    label: "Document evidence",
    shortLabel: "Docs",
    tone: "info",
    accentClass: "bg-info",
    softClass: "border-info/20 bg-info-bg text-info",
    borderClass: "border-info/25",
    textClass: "text-info",
  },
  tool: {
    label: "Redis Tool",
    shortLabel: "Tool",
    tone: "neutral",
    accentClass: "bg-subtle-foreground",
    softClass: "border-border bg-surface-muted text-muted-foreground",
    borderClass: "border-border",
    textClass: "text-muted-foreground",
  },
  warning: {
    label: "Redis Warning",
    shortLabel: "Warn",
    tone: "risk",
    accentClass: "bg-risk",
    softClass: "border-risk/20 bg-risk-bg text-risk",
    borderClass: "border-risk/25",
    textClass: "text-risk",
  },
  generic: {
    label: "Redis",
    shortLabel: "Redis",
    tone: "neutral",
    accentClass: "bg-subtle-foreground",
    softClass: "border-border bg-surface-muted text-muted-foreground",
    borderClass: "border-border",
    textClass: "text-muted-foreground",
  },
};

function joinSignalText(values: unknown[]): string {
  return values
    .flatMap((value) => {
      if (value == null) return [];
      if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return [String(value)];
      if (Array.isArray(value)) return value.map((entry) => String(entry));
      if (typeof value === "object") return Object.values(value as Record<string, unknown>).map((entry) => String(entry));
      return [String(value)];
    })
    .join(" ")
    .toLowerCase();
}

const DOCUMENT_ACTIVITY_KINDS = new Set([
  "document_indexed",
  "document_vector_query",
  "document_chunks_retrieved",
  "document_source_used",
  "document_fact_promoted",
  "document_discrepancy_created",
]);

export function classifyRedisSignal(...values: unknown[]): RedisSignalKind {
  const text = joinSignalText(values);
  if (!text.trim()) return "generic";
  if (DOCUMENT_ACTIVITY_KINDS.has(text.trim()) || /document_(indexed|vector|chunks|source|fact|discrepancy)/.test(text)) {
    return "document";
  }
  if (/\b(error|failed|failure|warning|blocked)\b/.test(text)) return "warning";
  if (/reconcil|discrepanc|variance|provenance|freshness|needs review|mismatch|source inventory/.test(text)) {
    return "reconciliation";
  }
  if (/vector|rag|embedding|policy|precedent|similarity|knn/.test(text)) return "vector";
  if (/stream|pubsub|pub\/sub|channel|audit event|event id|append|publish/.test(text)) return "stream";
  if (/search|index|ft\.search|vendor|contract|renewal|supplier|procurement/.test(text)) return "redisearch";
  if (/json|system of record|ledger|financial|company|cash|record|document/.test(text)) return "redisjson";
  if (/tool|model|plan|state|context|runway/.test(text)) return "tool";
  return "generic";
}

export function redisSignalMeta(kind: RedisSignalKind): RedisSignalMeta {
  return REDIS_SIGNAL_META[kind] ?? REDIS_SIGNAL_META.generic;
}
