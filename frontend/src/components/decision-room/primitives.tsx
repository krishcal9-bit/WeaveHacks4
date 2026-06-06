"use client";

import { useState, type ComponentType, type ReactNode } from "react";
import { Check, ChevronDown, Copy } from "lucide-react";
import { cx } from "@/components/ui";
import { toneClasses, type Tone } from "@/lib/council";

export type IconType = ComponentType<{ className?: string; strokeWidth?: number }>;

// --------------------------------------------------------------------------- //
// Panel — the command-room surface. Optional icon, eyebrow, action, count, and
// collapse/expand. Used by every rail and section so chrome stays consistent.
// --------------------------------------------------------------------------- //
export function Panel({
  id,
  title,
  icon: Icon,
  eyebrow,
  action,
  count,
  collapsible = false,
  defaultOpen = true,
  children,
  className = "",
  bodyClassName = "",
  scroll = false,
}: {
  id?: string;
  title: ReactNode;
  icon?: IconType;
  eyebrow?: ReactNode;
  action?: ReactNode;
  count?: number;
  collapsible?: boolean;
  defaultOpen?: boolean;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  scroll?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const showBody = !collapsible || open;

  return (
    <section
      id={id}
      className={cx("flex min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-sm", className)}
    >
      <header className="flex items-center gap-2 border-b border-border px-3 py-2.5">
        {Icon && (
          <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-surface-muted text-muted-foreground">
            <Icon className="h-3.5 w-3.5" strokeWidth={2} />
          </span>
        )}
        <div className="min-w-0 flex-1">
          {eyebrow && (
            <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-subtle-foreground">{eyebrow}</div>
          )}
          <h2 className="truncate text-[13px] font-semibold leading-tight">{title}</h2>
        </div>
        {typeof count === "number" && (
          <span className="shrink-0 rounded-full border border-border bg-background px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-muted-foreground">
            {count}
          </span>
        )}
        {action}
        {collapsible && (
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-label={open ? "Collapse" : "Expand"}
            className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground"
          >
            <ChevronDown className={cx("h-4 w-4 transition-transform", !open && "-rotate-90")} strokeWidth={2} />
          </button>
        )}
      </header>
      {showBody && (
        <div className={cx("p-3", scroll && "room-scroll max-h-[360px] overflow-y-auto", bodyClassName)}>{children}</div>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Atoms
// --------------------------------------------------------------------------- //
export function ToneDot({ tone = "neutral", pulse = false, className = "" }: { tone?: Tone; pulse?: boolean; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cx("inline-block h-1.5 w-1.5 shrink-0 rounded-full", toneClasses(tone).dot, pulse && "animate-pulse", className)}
    />
  );
}

export function StatusBadge({
  tone = "neutral",
  children,
  pulse = false,
  icon: Icon,
  className = "",
}: {
  tone?: Tone;
  children: ReactNode;
  pulse?: boolean;
  icon?: IconType;
  className?: string;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-semibold leading-5",
        toneClasses(tone).soft,
        className,
      )}
    >
      {Icon ? <Icon className="h-3 w-3" strokeWidth={2.25} /> : <span className={cx("h-1.5 w-1.5 rounded-full bg-current", pulse && "animate-pulse")} />}
      <span className="truncate">{children}</span>
    </span>
  );
}

export function SectionLabel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx("text-[10px] font-semibold uppercase tracking-[0.08em] text-subtle-foreground", className)}>
      {children}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={cx("animate-pulse rounded-md bg-surface-muted", className)} />;
}

export function SkeletonText({ lines = 3, className = "" }: { lines?: number; className?: string }) {
  return (
    <div className={cx("space-y-2", className)}>
      {Array.from({ length: lines }).map((_, index) => (
        <Skeleton key={index} className={cx("h-3", index === lines - 1 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}

export function EmptyState({ icon: Icon, children, className = "" }: { icon?: IconType; children: ReactNode; className?: string }) {
  return (
    <div
      className={cx(
        "flex flex-col items-center gap-2 rounded-md border border-dashed border-border bg-background px-4 py-6 text-center",
        className,
      )}
    >
      {Icon && <Icon className="h-5 w-5 text-subtle-foreground" strokeWidth={1.75} />}
      <p className="text-[12px] leading-relaxed text-muted-foreground">{children}</p>
    </div>
  );
}

export function MetricTile({
  label,
  value,
  tone,
  hint,
  className = "",
}: {
  label: ReactNode;
  value: ReactNode;
  tone?: Tone;
  hint?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("min-w-0 rounded-md border border-border bg-background px-2.5 py-2", className)}>
      <div className="truncate text-[10px] text-subtle-foreground">{label}</div>
      <div className={cx("mt-0.5 break-words text-[15px] font-semibold leading-tight tabular-nums", tone && toneClasses(tone).text)}>
        {value}
      </div>
      {hint && <div className="mt-0.5 break-words text-[10px] leading-relaxed text-muted-foreground">{hint}</div>}
    </div>
  );
}

// Live event list row: tone dot + title + detail + optional timestamp/meta.
export function RailRow({
  tone = "info",
  title,
  detail,
  meta,
}: {
  tone?: Tone;
  title: ReactNode;
  detail?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <div className="flex min-w-0 gap-2">
      <ToneDot tone={tone} className="mt-1.5" />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <div className="truncate text-[12px] font-semibold">{title}</div>
          {meta && <div className="shrink-0 text-[10px] tabular-nums text-subtle-foreground">{meta}</div>}
        </div>
        {detail && <div className="break-words text-[11px] leading-relaxed text-muted-foreground">{detail}</div>}
      </div>
    </div>
  );
}

export function Waveform({ active, className = "" }: { active: boolean; className?: string }) {
  return (
    <div
      className={cx("flex h-3.5 items-end gap-[2px]", active ? "text-positive" : "text-subtle-foreground", className)}
      aria-hidden="true"
    >
      {[4, 9, 13, 7, 15, 6, 11, 5, 10].map((height, index) => (
        <span
          key={`${height}-${index}`}
          className={cx("w-[2px] rounded-full bg-current", active ? "animate-pulse" : "opacity-60")}
          style={{ height }}
        />
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Buttons
// --------------------------------------------------------------------------- //
export function IconButton({
  icon: Icon,
  label,
  onClick,
  disabled = false,
  active = false,
  title,
  className = "",
}: {
  icon: IconType;
  label?: string;
  onClick?: () => void;
  disabled?: boolean;
  active?: boolean;
  title?: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title ?? label}
      aria-label={title ?? label}
      className={cx(
        "inline-flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-md border px-2 text-[11px] font-semibold transition-colors disabled:opacity-40",
        active
          ? "border-accent bg-accent text-accent-foreground"
          : "border-border bg-surface text-muted-foreground hover:bg-surface-muted hover:text-foreground",
        !label && "w-7 px-0",
        className,
      )}
    >
      <Icon className="h-3.5 w-3.5" strokeWidth={2} />
      {label && <span>{label}</span>}
    </button>
  );
}

export function CopyButton({
  text,
  label = "Copy",
  copiedLabel = "Copied",
  className = "",
}: {
  text: string;
  label?: string;
  copiedLabel?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else if (typeof document !== "undefined") {
        const area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        document.body.removeChild(area);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard can be blocked; fail quietly rather than break the room.
    }
  };

  return (
    <button
      type="button"
      onClick={copy}
      className={cx(
        "inline-flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-md border border-border bg-surface px-2 text-[11px] font-semibold text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground",
        className,
      )}
    >
      {copied ? <Check className="h-3.5 w-3.5 text-positive" strokeWidth={2.25} /> : <Copy className="h-3.5 w-3.5" strokeWidth={2} />}
      {copied ? copiedLabel : label}
    </button>
  );
}
