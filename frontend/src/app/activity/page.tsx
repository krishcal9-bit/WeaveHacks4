"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DecisionEvent } from "@/lib/types";
import { decisionStyle } from "@/lib/agents";
import { Card, Pill, SectionTitle } from "@/components/ui";

export default function ActivityPage() {
  const [decisions, setDecisions] = useState<DecisionEvent[]>([]);

  useEffect(() => {
    api.decisions().then(setDecisions).catch(() => {});
  }, []);

  return (
    <div className="mx-auto max-w-[920px] px-8 py-8">
      <SectionTitle>Activity</SectionTitle>
      <h1 className="mt-1.5 text-[22px] font-semibold tracking-tight">Decision Log</h1>
      <p className="mt-1 text-[13px] text-muted-foreground">
        Every recommendation the committee has issued, newest first.
      </p>

      <Card className="mt-6 divide-y divide-border">
        {decisions.length === 0 && (
          <p className="p-6 text-[13px] text-subtle-foreground">No decisions recorded yet.</p>
        )}
        {decisions.map((d) => (
          <div key={d._id} className="flex items-start justify-between gap-4 p-4">
            <div className="min-w-0">
              <div className="text-[13px] font-medium">{d.title}</div>
              {d.summary && (
                <div className="mt-0.5 text-[12px] leading-relaxed text-muted-foreground">{d.summary}</div>
              )}
              <div className="mt-1 text-[11px] uppercase tracking-wider text-subtle-foreground">
                {d.source === "debate" ? "Committee decision" : "Historical"}
              </div>
            </div>
            {d.decision && (
              <Pill className={decisionStyle(d.decision)}>
                {d.decision}
                {typeof d.confidence === "number" ? ` · ${d.confidence}%` : ""}
              </Pill>
            )}
          </div>
        ))}
      </Card>
    </div>
  );
}
