import type { ComponentType, ReactNode } from "react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { AtlasIcon, type AtlasIconName } from "@/components/atlas-icon";
import { cx, Pill, StatusDot } from "@/components/ui";
import { decisionStyle } from "@/lib/agents";
import { titleCase } from "@/lib/format";

// Information-only tones shared across dashboard primitives.
export type Tone = "neutral" | "positive" | "warning" | "risk" | "info";

const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-foreground",
  positive: "text-positive",
  warning: "text-warning",
  risk: "text-risk",
  info: "text-info",
};

const TONE_PILL: Record<Tone, string> = {
  neutral: "border-border bg-surface-muted text-muted-foreground",
  positive: "border-positive/20 bg-positive-bg text-positive",
  warning: "border-warning/20 bg-warning-bg text-warning",
  risk: "border-risk/20 bg-risk-bg text-risk",
  info: "border-info/20 bg-info-bg text-info",
};

const TONE_SOLID: Record<Tone, string> = {
  neutral: "#5a6573",
  positive: "#18794e",
  warning: "#b54708",
  risk: "#b42318",
  info: "#2f5bb7",
};

// Map a free-text severity to an information tone (defensive against unknown values).
export function severityTone(severity?: string | null): Tone {
  const s = (severity ?? "").toLowerCase();
  if (/(critical|severe|high|blocker)/.test(s)) return "risk";
  if (/(medium|moderate|warn|elevated)/.test(s)) return "warning";
  if (/(low|minor|info)/.test(s)) return "info";
  return "neutral";
}

/**
 * The single card surface for a dashboard section: a bordered panel with a header
 * (title + optional icon/action) and a body. Content inside uses rows/tables, never
 * nested cards. A fixed header keeps panels visually aligned across the grid.
 */
export function Panel({
  title,
  icon: Icon,
  visualIcon,
  action,
  children,
  className = "",
  bodyClassName = "",
}: {
  title: ReactNode;
  icon?: ComponentType<{ className?: string; strokeWidth?: number }>;
  visualIcon?: AtlasIconName;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cx("command-surface flex min-w-0 flex-col", className)}>
      <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-1.5 text-[12.5px] font-semibold text-foreground">
            {visualIcon ? (
              <AtlasIcon name={visualIcon} size="xs" className="atlas-icon-badge--quiet" />
            ) : Icon ? (
              <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" strokeWidth={1.85} />
            ) : null}
            <span className="truncate">{title}</span>
          </h2>
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </header>
      <div className={cx("min-w-0 flex-1 px-4 py-3", bodyClassName)}>{children}</div>
    </section>
  );
}

/** KPI tile — fixed min-height so the strip never reflows as live values load. */
export function MetricTile({
  label,
  value,
  sub,
  tone = "neutral",
  icon: Icon,
  visualIcon,
}: {
  label: string;
  value: string;
  sub?: ReactNode;
  tone?: Tone;
  icon?: ComponentType<{ className?: string; strokeWidth?: number }>;
  visualIcon?: AtlasIconName;
}) {
  return (
    <div className="command-surface flex min-h-[94px] flex-col justify-between p-3.5">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-[11px] font-medium text-muted-foreground">{label}</span>
        {visualIcon ? (
          <AtlasIcon name={visualIcon} size="xs" className="atlas-icon-badge--quiet" />
        ) : Icon ? (
          <Icon className="h-3.5 w-3.5 shrink-0 text-subtle-foreground" strokeWidth={1.85} />
        ) : null}
      </div>
      <div className={cx("mt-2 text-[22px] font-semibold leading-none tracking-tight tabular-nums", TONE_TEXT[tone])}>
        {value}
      </div>
      {sub ? <div className="mt-1.5 truncate text-[11px] tabular-nums text-subtle-foreground">{sub}</div> : null}
    </div>
  );
}

/** Signed change indicator with a directional glyph. */
export function Delta({
  value,
  direction = "flat",
  tone = "neutral",
}: {
  value: string;
  direction?: "up" | "down" | "flat";
  tone?: Tone;
}) {
  const Icon = direction === "up" ? ArrowUpRight : direction === "down" ? ArrowDownRight : Minus;
  return (
    <span className={cx("inline-flex items-center gap-0.5 tabular-nums", TONE_TEXT[tone])}>
      <Icon className="h-3 w-3 shrink-0" strokeWidth={2} />
      {value}
    </span>
  );
}

/** Generic tone pill. */
export function TonePill({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <Pill className={TONE_PILL[tone]}>{children}</Pill>;
}

/** Severity → toned pill, label title-cased. */
export function SeverityPill({ severity }: { severity?: string | null }) {
  return <TonePill tone={severityTone(severity)}>{severity ? titleCase(severity) : "—"}</TonePill>;
}

/** Board-constraint badge: a status dot + label inside a toned pill. */
export function ConstraintBadge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={cx(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-5",
        TONE_PILL[tone],
      )}
    >
      <StatusDot tone={tone} className="!shadow-none" />
      {children}
    </span>
  );
}

/** Live sponsor health chip — color + glyph convey state without relying on hover. */
export function HealthChip({
  label,
  ready,
  detail,
}: {
  label: string;
  ready?: boolean | null;
  detail?: string | null;
}) {
  const tone: Tone = ready === true ? "positive" : ready === false ? "risk" : "neutral";
  const glyph = ready === true ? "✓" : ready === false ? "✕" : "•";
  return (
    <span
      title={detail ?? undefined}
      className="inline-flex max-w-full shrink-0 items-center gap-1.5 rounded-md border border-border bg-surface px-2 py-1 text-[11px]"
    >
      <span className={cx("font-semibold leading-none", TONE_TEXT[tone])} aria-hidden="true">
        {glyph}
      </span>
      <span className="truncate font-medium text-foreground">{label}</span>
    </span>
  );
}

/** A single risk/finding row: tone dot + title (+ optional badge), right-aligned meta, detail below. */
export function RiskRow({
  tone = "neutral",
  title,
  detail,
  meta,
  badge,
}: {
  tone?: Tone;
  title: ReactNode;
  detail?: ReactNode;
  meta?: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <div className="flex items-start gap-2.5 py-2.5">
      <StatusDot tone={tone} className="mt-[5px]" />
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="truncate text-[12.5px] font-medium text-foreground">{title}</span>
            {badge}
          </div>
          {meta && <span className="shrink-0 text-[11px] tabular-nums text-subtle-foreground">{meta}</span>}
        </div>
        {detail && (
          <div className="mt-0.5 line-clamp-2 text-[11.5px] leading-relaxed text-muted-foreground">{detail}</div>
        )}
      </div>
    </div>
  );
}

/** Decision timeline row — title, summary, decision verdict pill + confidence. */
export function DecisionRow({
  title,
  summary,
  decision,
  confidence,
  source,
  trailing,
}: {
  title: string;
  summary?: string;
  decision?: string;
  confidence?: number;
  source?: string;
  trailing?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 py-2.5">
      <div className="min-w-0">
        <div className="line-clamp-1 text-[12.5px] font-medium text-foreground">{title}</div>
        {summary && <div className="mt-0.5 line-clamp-1 text-[11.5px] text-muted-foreground">{summary}</div>}
        {source && (
          <div className="mt-1 text-[10px] uppercase tracking-[0.08em] text-subtle-foreground">{source}</div>
        )}
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1">
        {decision && (
          <Pill className={decisionStyle(decision)}>
            {decision}
            {typeof confidence === "number" ? ` · ${confidence}%` : ""}
          </Pill>
        )}
        {trailing}
      </div>
    </div>
  );
}

/** Inline progress/proportion bar with a stable height. */
export function Bar({
  value,
  max,
  tone = "neutral",
  className = "",
}: {
  value: number;
  max: number;
  tone?: Tone;
  className?: string;
}) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className={cx("h-1.5 w-full overflow-hidden rounded-full bg-surface-muted", className)}>
      <div className="h-full rounded-full" style={{ width: `${pct}%`, background: TONE_SOLID[tone] }} />
    </div>
  );
}

/** Compact label/value pair for meta strips. */
export function MetaItem({ label, value, tone = "neutral" }: { label: string; value: ReactNode; tone?: Tone }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-[0.08em] text-subtle-foreground">{label}</div>
      <div className={cx("mt-0.5 truncate text-[12.5px] font-semibold tabular-nums", TONE_TEXT[tone])}>{value}</div>
    </div>
  );
}

/** Graceful "field absent" state — used when an optional backend field is missing. */
export function NotAvailable({ label = "Not available", className = "" }: { label?: string; className?: string }) {
  return (
    <div
      className={cx(
        "flex min-h-[64px] items-center justify-center rounded-md border border-dashed border-border bg-surface-quiet px-3 py-4 text-center text-[11.5px] text-subtle-foreground",
        className,
      )}
    >
      {label}
    </div>
  );
}

/** Horizontal-scroll wrapper so dense tables never overlap or squish on narrow viewports. */
export function ScrollX({
  children,
  minWidth = 560,
  className = "",
}: {
  children: ReactNode;
  minWidth?: number;
  className?: string;
}) {
  return (
    <div className={cx("scroll-x -mx-1 overflow-x-auto px-1", className)}>
      <div style={{ minWidth }}>{children}</div>
    </div>
  );
}
