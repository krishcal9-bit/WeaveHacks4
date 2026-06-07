"use client";

import { useEffect, useRef, useState } from "react";
import { FileText } from "lucide-react";
import { onDemoReset } from "@/lib/demo-reset";
import { api } from "@/lib/api";
import type { DecisionEvent } from "@/lib/types";
import { DecisionRow, MetricTile, NotAvailable, Panel, type Tone } from "@/components/dashboard";
import { AtlasIcon } from "@/components/atlas-icon";

const POLL_MS = 30_000;

function avg(nums: number[]): number {
  const xs = nums.filter((n) => !Number.isNaN(n));
  return xs.length ? xs.reduce((s, n) => s + n, 0) / xs.length : NaN;
}

export default function ActivityPage() {
  const [decisions, setDecisions] = useState<DecisionEvent[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const got = useRef(false);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const d = await api.decisions();
        if (!active) return;
        got.current = true;
        setDecisions(d);
        setErr(null);
      } catch (e) {
        if (active && !got.current) setErr(String(e));
      } finally {
        if (active) setLoaded(true);
      }
    };
    load();
    const id = setInterval(load, POLL_MS);
    const unsubscribe = onDemoReset(() => {
      got.current = false;
      void load();
    });
    return () => {
      active = false;
      clearInterval(id);
      unsubscribe();
    };
  }, []);

  const verdicts = decisions.reduce<Record<string, number>>((acc, d) => {
    const k = (d.decision ?? "").toUpperCase();
    if (k) acc[k] = (acc[k] ?? 0) + 1;
    return acc;
  }, {});
  const avgConfidence = avg(
    decisions.map((d) => (typeof d.confidence === "number" ? d.confidence : NaN)),
  );

  return (
    <div className="mx-auto w-full max-w-[1080px] px-4 py-5 sm:px-6">
      <div className="flex items-center gap-3">
        <AtlasIcon name="memo" size="sm" className="atlas-icon-badge--quiet" />
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.08em] text-subtle-foreground">
            <FileText className="h-3.5 w-3.5" strokeWidth={1.85} />
            Decision log
          </div>
          <h1 className="mt-1 text-[20px] font-semibold tracking-tight text-foreground">Decision activity</h1>
        </div>
      </div>
      <p className="mt-0.5 text-[12px] text-muted-foreground">
        Every recommendation the committee has issued, newest first.
      </p>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <MetricTile label="Total decisions" value={String(decisions.length)} visualIcon="memo" />
        <MetricTile label="Approved" value={String(verdicts.APPROVE ?? 0)} tone="positive" visualIcon="reconcile" />
        <MetricTile label="Conditional" value={String(verdicts.CONDITIONAL ?? 0)} tone="warning" visualIcon="scenario" />
        <MetricTile label="Rejected" value={String(verdicts.REJECT ?? 0)} tone="risk" visualIcon="risk" />
        <MetricTile
          label="Avg confidence"
          value={Number.isNaN(avgConfidence) ? "—" : `${avgConfidence.toFixed(0)}%`}
          visualIcon="health"
        />
      </div>

      <div className="mt-3">
        <Panel title="All decisions" eyebrow="History" icon={FileText} visualIcon="memo">
          {err && decisions.length === 0 ? (
            <NotAvailable label="Could not reach the finance service to load the decision log." />
          ) : !loaded ? (
            <ul className="divide-y divide-border">
              {Array.from({ length: 5 }).map((_, i) => (
                <li key={i} className="py-3">
                  <div className="h-4 w-2/3 animate-pulse rounded bg-surface-muted" />
                  <div className="mt-2 h-3 w-1/3 animate-pulse rounded bg-surface-muted" />
                </li>
              ))}
            </ul>
          ) : decisions.length === 0 ? (
            <p className="py-8 text-center text-[12px] text-subtle-foreground">No decisions recorded yet.</p>
          ) : (
            <ul className="divide-y divide-border">
              {decisions.map((d) => {
                const source =
                  d.source === "debate" ? "Committee decision" : d.source === "history" ? "Historical" : d.source;
                const rel = reliabilityLabel(d);
                const appr = approvalLabel(d);
                const trailing =
                  appr || rel ? (
                    <div className="flex flex-col items-end gap-0.5">
                      {appr && (
                        <span
                          className={`text-[10.5px] font-medium ${appr.tone}`}
                          title={d.approval_status_label ?? undefined}
                        >
                          {appr.text}
                        </span>
                      )}
                      {rel && <span className={`text-[10.5px] tabular-nums ${rel.tone}`}>{rel.text}</span>}
                    </div>
                  ) : undefined;
                return (
                  <li key={d._id}>
                    <DecisionRow
                      title={d.title}
                      summary={d.summary}
                      decision={d.decision}
                      confidence={d.confidence}
                      source={source}
                      trailing={trailing}
                    />
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}

// Read-only governance status, surfaced only when the backend attached one.
// Mirrors the lifecycle in agent/src/governance_models.py; "approved" decisions
// reached without a human are system-cleared (see the title tooltip), never
// presented as a human sign-off.
function approvalLabel(d: DecisionEvent): { text: string; tone: string } | null {
  const status = d.approval_status;
  if (!status) return null;
  const map: Record<string, { text: string; tone: string }> = {
    pending_approval: { text: "Pending approval", tone: "text-warning" },
    approved: { text: "Approved", tone: "text-positive" },
    conditionally_approved: { text: "Conditional approval", tone: "text-info" },
    rejected: { text: "Rejected", tone: "text-risk" },
    expired: { text: "Expired", tone: "text-subtle-foreground" },
    superseded: { text: "Superseded", tone: "text-subtle-foreground" },
    draft: { text: "Draft", tone: "text-subtle-foreground" },
  };
  return map[String(status)] ?? { text: String(status), tone: "text-subtle-foreground" };
}

// Surface a reliability summary only when the backend actually attached one (defensive).
function reliabilityLabel(d: DecisionEvent): { text: string; tone: string } | null {
  const scores = d.reliability_scores;
  if (!scores?.length) return null;
  const values = scores.map((s) => s.reliability).filter((n) => typeof n === "number" && !Number.isNaN(n));
  if (!values.length) return null;
  const mean = values.reduce((s, n) => s + n, 0) / values.length;
  const tone: Tone = mean >= 85 ? "positive" : mean >= 70 ? "warning" : "risk";
  const toneClass = tone === "positive" ? "text-positive" : tone === "warning" ? "text-warning" : "text-risk";
  return { text: `${mean.toFixed(0)} reliability`, tone: toneClass };
}
