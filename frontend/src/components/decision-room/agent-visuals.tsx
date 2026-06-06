"use client";

import { Activity, BarChart3, Landmark, ServerCog, ShieldCheck, ShoppingCart, Vault } from "lucide-react";
import { cx } from "@/components/ui";
import { reliabilityColor, toneClasses, type Tone } from "@/lib/council";
import type { IconType } from "./primitives";

export const AGENT_ICONS: Record<string, IconType> = {
  cfo: Landmark,
  treasury: Vault,
  fpna: BarChart3,
  risk: ShieldCheck,
  procurement: ShoppingCart,
  reliability: Activity,
};

export function agentIcon(id: string): IconType {
  return AGENT_ICONS[id] ?? ServerCog;
}

const SIZES = {
  sm: { outer: "h-11 w-11", icon: "h-5 w-5" },
  md: { outer: "h-14 w-14", icon: "h-6 w-6" },
  lg: { outer: "h-16 w-16", icon: "h-7 w-7" },
} as const;

// Icon disc wrapped by a reliability gauge. The ring fills proportional to the
// score (conic gradient); before a score exists it falls back to the seat's
// faint identity tone. An "EVAL" badge marks a pending score.
export function ReliabilityRing({
  icon: Icon,
  value,
  accentTone = "info",
  active = false,
  size = "md",
}: {
  icon: IconType;
  value?: number;
  accentTone?: Tone;
  active?: boolean;
  size?: keyof typeof SIZES;
}) {
  const dims = SIZES[size];
  const scored = typeof value === "number";
  const ringStyle = scored
    ? { background: `conic-gradient(${reliabilityColor(value)} ${value * 3.6}deg, var(--border) 0deg)` }
    : undefined;

  return (
    <div
      className={cx(
        "relative grid shrink-0 place-items-center rounded-full p-[3px]",
        dims.outer,
        !scored && cx("border-[3px]", toneClasses(accentTone).ring),
        active && "ring-2 ring-info/40 ring-offset-2 ring-offset-surface",
      )}
      style={ringStyle}
      title={scored ? `Reliability ${value}%` : "Reliability pending"}
    >
      <div className="grid h-full w-full place-items-center rounded-full border border-border bg-surface text-foreground">
        <Icon className={dims.icon} strokeWidth={1.9} />
      </div>
      <span className="absolute -bottom-1 -right-1 rounded-full border border-border bg-background px-1 py-0.5 text-[9px] font-bold tabular-nums leading-none text-foreground shadow-sm">
        {scored ? `${value}%` : "EVAL"}
      </span>
    </div>
  );
}
