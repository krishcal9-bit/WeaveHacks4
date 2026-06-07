"use client";

import { memo, useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  AlertTriangle,
  ChevronDown,
  Database,
  ExternalLink,
  FileJson2,
  FileSearch,
  GitCompareArrows,
  Network,
  Radio,
  Search,
  Wrench,
  type LucideProps,
} from "lucide-react";
import { cx } from "@/components/ui";
import type { RedisActivity } from "@/lib/types";
import { EmptyState, Panel } from "./primitives";
import {
  REDIS_SIGNAL_ORDER,
  classifyRedisSignal,
  redisSignalMeta,
  type RedisSignalKind,
} from "./redis-event-style";
import {
  EASE_OUT_EXPO,
  motionDuration,
  springSnappy,
  staggerDelay,
} from "@/components/motion/variants";

interface ActivityRow {
  id: string;
  item: RedisActivity;
  kind: RedisSignalKind;
  source: SourceDescriptor;
}

interface ActivityGroup {
  kind: RedisSignalKind;
  rows: ActivityRow[];
}

interface SourceDescriptor {
  label: string;
  value: string;
  href?: string;
  meta: string[];
}

function asString(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return undefined;
}

function sourceFromActivity(item: RedisActivity, kind: RedisSignalKind): SourceDescriptor {
  const record = item as RedisActivity & { url?: unknown; redis_key?: unknown; source?: unknown };
  const url = asString(record.url);
  const key = asString(item.key ?? record.redis_key);
  const stream = asString(item.stream ?? item.channel ?? item.last_id);
  const source = asString(record.source);
  const value = url ?? key ?? stream ?? source ?? item.detail ?? redisSignalMeta(kind).label;
  const href = url && /^https?:\/\//.test(url) ? url : undefined;
  const meta = [
    typeof item.count === "number" ? `${item.count} items` : undefined,
    typeof item.length === "number" ? `${item.length} entries` : undefined,
    typeof item.memory_bytes === "number" ? `${Math.round(item.memory_bytes / 1024)} KB` : undefined,
    typeof item.ttl_seconds === "number" ? `${item.ttl_seconds}s TTL` : undefined,
  ].filter(Boolean) as string[];

  return {
    label: href ? "Source URL" : key ? "Redis key" : stream ? "Stream link" : source ? "Source" : "Trace source",
    value,
    href,
    meta,
  };
}

const DOCUMENT_KIND_LABELS: Record<string, string> = {
  document_indexed: "Document indexed",
  document_vector_query: "Vector query",
  document_chunks_retrieved: "Chunks retrieved",
  document_source_used: "Source cited",
  document_fact_promoted: "Fact promoted",
  document_discrepancy_created: "Discrepancy",
};

function activityTitle(item: RedisActivity, kind: RedisSignalKind): string {
  const rawKind = typeof item.kind === "string" ? item.kind : "";
  if (DOCUMENT_KIND_LABELS[rawKind]) return DOCUMENT_KIND_LABELS[rawKind];
  return item.label ?? item.name ?? redisSignalMeta(kind).label;
}

function KindIcon({ kind, ...props }: { kind: RedisSignalKind } & LucideProps) {
  switch (kind) {
    case "redisjson":
      return <FileJson2 {...props} />;
    case "redisearch":
      return <Search {...props} />;
    case "vector":
      return <Network {...props} />;
    case "document":
      return <FileSearch {...props} />;
    case "stream":
      return <Radio {...props} />;
    case "reconciliation":
      return <GitCompareArrows {...props} />;
    case "tool":
      return <Wrench {...props} />;
    case "warning":
      return <AlertTriangle {...props} />;
    default:
      return <Database {...props} />;
  }
}

const VISIBLE_ACTIVITY_LIMIT = 40;

function buildRows(activity: RedisActivity[]): ActivityRow[] {
  return [...activity]
    .slice(-VISIBLE_ACTIVITY_LIMIT)
    .map((item, index) => {
      const kind = classifyRedisSignal(item.kind, item.type, item.label, item.name, item.detail, item.key, item.stream, item.channel);
      return {
        id: `${item.key ?? item.last_id ?? item.label ?? item.kind ?? "redis"}-${item.at ?? index}-${index}`,
        item,
        kind,
        source: sourceFromActivity(item, kind),
      };
    })
    .reverse();
}

function groupRows(rows: ActivityRow[]): ActivityGroup[] {
  const groups = new Map<RedisSignalKind, ActivityRow[]>();
  for (const row of rows) {
    groups.set(row.kind, [...(groups.get(row.kind) ?? []), row]);
  }
  return REDIS_SIGNAL_ORDER.map((kind) => ({ kind, rows: groups.get(kind) ?? [] })).filter((group) => group.rows.length > 0);
}

export function RedisActivityRail({ activity, active = false }: { activity: RedisActivity[]; active?: boolean }) {
  const prefersReducedMotion = useReducedMotion();
  const reduced = Boolean(prefersReducedMotion);
  const rows = useMemo(() => buildRows(activity), [activity]);
  const groups = useMemo(() => groupRows(rows), [rows]);

  return (
    <Panel
      id="redis-activity"
      icon={Database}
      visualIcon="memory"
      title="Redis activity"
      count={activity.length}
      scroll
    >
      {groups.length === 0 ? (
        <EmptyState icon={Database} visualIcon="memory">
          RedisJSON, RediSearch, vector RAG, streams, and reconciliation writes appear here.
        </EmptyState>
      ) : (
        <div className="space-y-2.5" data-redis-activity-groups={groups.length}>
          <AnimatePresence initial={false} mode="popLayout">
            {groups.map((group, index) => (
              <ActivityFamily
                key={group.kind}
                group={group}
                active={active && index < 4}
                reduced={reduced}
                index={index}
              />
            ))}
          </AnimatePresence>
        </div>
      )}
    </Panel>
  );
}

function ActivityFamily({
  group,
  active,
  reduced,
  index,
}: {
  group: ActivityGroup;
  active: boolean;
  reduced: boolean;
  index: number;
}) {
  const [open, setOpen] = useState(true);
  const meta = redisSignalMeta(group.kind);
  const visibleRows = open ? group.rows.slice(0, 8) : group.rows.slice(0, 2);
  const latest = group.rows[0]?.item.at;
  const pulsing = active && !reduced;

  return (
    <motion.section
      layout="position"
      data-activity-group={group.kind}
      data-activity-pulse={pulsing ? "active" : "idle"}
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={reduced ? { opacity: 0 } : { opacity: 0, y: -4 }}
      transition={reduced ? { duration: motionDuration.instant } : { ...springSnappy, delay: staggerDelay(index, 0.035, 0.16) }}
      className={cx(
        "redis-activity-family rounded-md border bg-background/80",
        meta.borderClass,
        pulsing && "redis-activity-family--active",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full min-w-0 items-center gap-2 px-2.5 py-2 text-left"
        aria-expanded={open}
      >
        <SignalGlyph kind={group.kind} active={pulsing} />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-2">
            <span className="truncate text-[12px] font-semibold">{meta.label}</span>
            <span className="shrink-0 text-[10px] tabular-nums text-subtle-foreground">
              {group.rows.length} event{group.rows.length === 1 ? "" : "s"}
            </span>
          </span>
          <span className="mt-0.5 flex min-w-0 items-center gap-2 text-[10px] leading-tight text-subtle-foreground">
            <KindIcon kind={group.kind} className="h-3 w-3 shrink-0" strokeWidth={2.2} />
            <span className="truncate">{latest ? `latest ${latest}` : "grouped by Redis workload"}</span>
          </span>
        </span>
        <ChevronDown
          className={cx("h-4 w-4 shrink-0 text-subtle-foreground transition-transform", !open && "-rotate-90")}
          strokeWidth={2}
        />
      </button>

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
            <div className="grid gap-1.5 p-2">
              {visibleRows.map((row, rowIndex) => (
                <ActivityEventRow
                  key={row.id}
                  row={row}
                  active={pulsing && rowIndex === 0}
                  reduced={reduced}
                  index={rowIndex}
                />
              ))}
              {group.rows.length > visibleRows.length && (
                <div className="px-1 pb-1 text-[10px] text-subtle-foreground">
                  {group.rows.length - visibleRows.length} older events grouped above the fold.
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.section>
  );
}

const ActivityEventRow = memo(function ActivityEventRow({
  row,
  active,
  reduced,
  index,
}: {
  row: ActivityRow;
  active: boolean;
  reduced: boolean;
  index: number;
}) {
  const meta = redisSignalMeta(row.kind);
  const title = activityTitle(row.item, row.kind);
  const detail = row.item.detail;

  return (
    <motion.article
      layout="position"
      data-activity-kind={row.kind}
      data-activity-pulse={active && !reduced ? "active" : "idle"}
      className={cx(
        "redis-activity-row min-w-0 rounded border border-border/70 bg-surface px-2 py-1.5",
        active && "redis-activity-row--fresh",
      )}
      initial={reduced ? { opacity: 0 } : { opacity: 0, x: -6 }}
      animate={{ opacity: 1, x: 0 }}
      transition={reduced ? { duration: motionDuration.instant } : { duration: motionDuration.normal, ease: EASE_OUT_EXPO, delay: staggerDelay(index, 0.025, 0.12) }}
    >
      <div className="flex min-w-0 gap-2">
        <span className={cx("mt-1 h-1.5 w-1.5 shrink-0 rounded-full", meta.accentClass)} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <div className="truncate text-[12px] font-semibold">{title}</div>
            {typeof row.item.at === "string" && (
              <div className="shrink-0 text-[10px] tabular-nums text-subtle-foreground">{row.item.at}</div>
            )}
          </div>
          {detail && <div className="break-words text-[11px] leading-relaxed text-muted-foreground">{detail}</div>}
          <SourceReveal source={row.source} reduced={reduced} />
        </div>
      </div>
    </motion.article>
  );
});

function SignalGlyph({ kind, active }: { kind: RedisSignalKind; active: boolean }) {
  const meta = redisSignalMeta(kind);
  return (
    <span
      aria-hidden="true"
      className={cx(
        "redis-signal-glyph grid h-8 w-8 shrink-0 place-items-center rounded-md border",
        meta.softClass,
        active && "redis-signal-glyph--active",
        active && `redis-signal-glyph--active-${kind}`,
      )}
    >
      <KindIcon kind={kind} className="h-4 w-4" strokeWidth={2.15} />
    </span>
  );
}

function SourceReveal({ source, reduced }: { source: SourceDescriptor; reduced: boolean }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-1" data-source-reveal={open ? "open" : "closed"}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="inline-flex max-w-full items-center gap-1 rounded border border-transparent px-1 py-0.5 text-[10px] font-semibold text-subtle-foreground transition-colors hover:border-border hover:bg-background hover:text-foreground"
      >
        <ExternalLink className="h-3 w-3 shrink-0" strokeWidth={2.2} />
        <span className="truncate">{open ? "Hide source" : "Reveal source"}</span>
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
              <a
                href={source.href}
                target="_blank"
                rel="noreferrer"
                className="mt-0.5 block break-all text-[11px] font-medium text-info underline-offset-2 hover:underline"
              >
                {source.value}
              </a>
            ) : (
              <code className="mt-0.5 block break-all rounded bg-surface-muted px-1.5 py-1 text-[10px] text-muted-foreground">
                {source.value}
              </code>
            )}
            {source.meta.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {source.meta.map((item) => (
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
