import type { ReactNode } from "react";

export function Card({ className = "", children }: { className?: string; children: ReactNode }) {
  return <div className={`rounded-xl border border-border bg-surface ${className}`}>{children}</div>;
}

export function SectionTitle({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <h2 className={`text-[11px] font-semibold uppercase tracking-[0.08em] text-subtle-foreground ${className}`}>
      {children}
    </h2>
  );
}

export function Monogram({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div className={`grid shrink-0 place-items-center rounded-md font-semibold ${className}`}>{text}</div>
  );
}

export function Pill({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${className}`}>
      {children}
    </span>
  );
}
