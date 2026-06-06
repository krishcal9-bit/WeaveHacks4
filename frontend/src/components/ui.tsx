import type { ReactNode } from "react";

type Tone = "neutral" | "positive" | "warning" | "risk" | "info";
type PreflightTone = Tone | "ready" | "running" | "blocked";

export function cx(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}

const toneClass: Record<Tone, string> = {
  neutral: "neutral",
  positive: "positive",
  warning: "warning",
  risk: "risk",
  info: "info",
};

const preflightDotTone: Record<PreflightTone, Tone> = {
  neutral: "neutral",
  positive: "positive",
  warning: "warning",
  risk: "risk",
  info: "info",
  ready: "positive",
  running: "info",
  blocked: "risk",
};

export function Card({ className = "", children }: { className?: string; children: ReactNode }) {
  return <div className={cx("command-surface", className)}>{children}</div>;
}

export function CommandSurface({
  className = "",
  children,
  quiet = false,
  flat = false,
}: {
  className?: string;
  children: ReactNode;
  quiet?: boolean;
  flat?: boolean;
}) {
  return (
    <section
      className={cx(
        "command-surface",
        quiet && "command-surface--quiet",
        flat && "command-surface--flat",
        className,
      )}
    >
      {children}
    </section>
  );
}

export function SectionTitle({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <h2 className={cx("text-[11px] font-semibold uppercase tracking-[0.08em] text-subtle-foreground", className)}>
      {children}
    </h2>
  );
}

export function Monogram({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div className={cx("grid shrink-0 place-items-center rounded-md font-semibold", className)}>{text}</div>
  );
}

export function Pill({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cx(
        "inline-flex max-w-full shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-5",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function StatusDot({
  tone = "neutral",
  label,
  pulse = false,
  className = "",
}: {
  tone?: Tone;
  label?: string;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden={label ? undefined : true}
      aria-label={label}
      className={cx("status-dot", `status-dot--${toneClass[tone]}`, pulse && "status-dot--pulse", className)}
      role={label ? "img" : undefined}
    />
  );
}

export function StatusRing({
  tone = "neutral",
  children,
  className = "",
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}) {
  return <span className={cx("status-ring", `status-ring--${toneClass[tone]}`, className)}>{children}</span>;
}

export function CommandRail({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={cx("command-rail", className)}>{children}</div>;
}

export function RailNode({
  children,
  state = "neutral",
  className = "",
}: {
  children: ReactNode;
  state?: "neutral" | "active" | "complete" | "risk";
  className?: string;
}) {
  return <div className={cx("rail-node", state !== "neutral" && `rail-node--${state}`, className)}>{children}</div>;
}

export function PreflightState({
  label,
  detail,
  tone = "neutral",
  className = "",
}: {
  label: ReactNode;
  detail?: ReactNode;
  tone?: PreflightTone;
  className?: string;
}) {
  return (
    <div className={cx("preflight-state", `preflight-state--${tone}`, className)}>
      <StatusDot tone={preflightDotTone[tone]} pulse={tone === "running"} className="mt-1.5" />
      <div className="min-w-0">
        <div className="text-[13px] font-medium leading-5 text-foreground">{label}</div>
        {detail && <div className="mt-0.5 text-[12px] leading-relaxed text-muted-foreground">{detail}</div>}
      </div>
    </div>
  );
}
