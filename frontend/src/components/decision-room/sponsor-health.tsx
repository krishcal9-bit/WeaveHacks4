"use client";

import { ShieldCheck } from "lucide-react";
import { sponsorStatusTone, type HealthView, type SponsorView } from "@/lib/council";
import { Panel, ToneDot } from "./primitives";

export function SponsorHealthPanel({ sponsorRows, health }: { sponsorRows: SponsorView[]; health: HealthView }) {
  const readyCount = sponsorRows.filter((row) => row.status === "ready").length;

  return (
    <Panel
      icon={ShieldCheck}
      eyebrow="Strict live preflight"
      title="Sponsor readiness"
      count={readyCount}
      collapsible
      defaultOpen={false}
    >
      <div className="space-y-2.5">
        {sponsorRows.map((row) => {
          const meta =
            row.realtime?.model ??
            row.model ??
            (row.modules ? Object.keys(row.modules).join(" · ") : undefined) ??
            (row.capabilities && row.capabilities.length > 0 ? row.capabilities.slice(0, 3).join(" · ") : undefined);
          return (
            <div key={row.id} className="flex min-w-0 gap-2">
              <ToneDot tone={sponsorStatusTone(row.status)} className="mt-1.5" pulse={row.status === "checking"} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[12px] font-semibold">{row.label}</span>
                  <span className="shrink-0 text-[10px] font-semibold uppercase tracking-[0.06em] text-subtle-foreground">
                    {row.status}
                  </span>
                </div>
                <div className="break-words text-[11px] leading-relaxed text-muted-foreground">
                  {row.error || row.detail}
                </div>
                {meta && <div className="mt-0.5 truncate text-[10px] text-subtle-foreground">{meta}</div>}
              </div>
            </div>
          );
        })}
      </div>
      {health.data?.mode && (
        <div className="mt-2.5 border-t border-border pt-2 text-[10px] text-subtle-foreground">
          Mode: <span className="font-semibold text-muted-foreground">{health.data.mode}</span>
        </div>
      )}
    </Panel>
  );
}
