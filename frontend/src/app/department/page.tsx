"use client";

import { useEffect, useState } from "react";
import { Workflow } from "lucide-react";
import { ROSTER, ROSTER_BY_ID } from "@/lib/agents";
import { api } from "@/lib/api";
import type { CompanyFinancials, RosterMember } from "@/lib/types";
import { cx, Card, Monogram } from "@/components/ui";

interface PromptVersion {
  agent: string;
  current: string;
  candidate: string;
  promotion_gate: string;
}
interface TrackRecord {
  count: number;
  avg: number;
}

export default function DepartmentPage() {
  const [co, setCo] = useState<CompanyFinancials | null>(null);
  const cfo = ROSTER_BY_ID["cfo"];
  const analysts = ROSTER.filter((r) => r.id !== "cfo");

  useEffect(() => {
    let active = true;
    api
      .company()
      .then((c) => {
        if (active) setCo(c);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  const promptByAgent: Record<string, PromptVersion> = {};
  for (const p of co?.prompt_versions ?? []) promptByAgent[p.agent] = p;

  const trackByMember = buildTrackRecords(co);

  return (
    <div className="mx-auto w-full max-w-[1180px] px-4 py-5 sm:px-6">
      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.08em] text-subtle-foreground">
        <Workflow className="h-3.5 w-3.5" strokeWidth={1.85} />
        Council memory
      </div>
      <h1 className="mt-1 text-[20px] font-semibold tracking-tight text-foreground">Your finance team</h1>
      <p className="mt-0.5 text-[12px] text-muted-foreground">
        A standing committee of finance functions. Each reviews every decision from its own mandate before the CFO rules
        — with a reliability track record and prompt-promotion gate.
      </p>

      {/* Chair */}
      <div className="mt-6 flex justify-center">
        <MemberCard
          member={cfo}
          highlight
          prompt={promptByAgent[cfo.id]}
          track={trackByMember[cfo.id]}
        />
      </div>

      <div className="mx-auto my-2 h-6 w-px bg-border" />

      {/* Analysts + reliability auditor */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {analysts.map((m) => (
          <MemberCard key={m.id} member={m} prompt={promptByAgent[m.id]} track={trackByMember[m.id]} />
        ))}
      </div>
    </div>
  );
}

const CAL_HEAT = (score: number) =>
  score >= 85 ? "bg-positive-bg text-positive" : score >= 70 ? "bg-warning-bg text-warning" : "bg-risk-bg text-risk";

function MemberCard({
  member,
  highlight = false,
  prompt,
  track,
}: {
  member: RosterMember;
  highlight?: boolean;
  prompt?: PromptVersion;
  track?: TrackRecord;
}) {
  return (
    <Card className={cx("flex flex-col p-4", highlight ? "w-full max-w-sm border-border-strong" : "")}>
      <div className="flex items-center gap-3">
        <Monogram
          text={member.monogram}
          className={cx(
            "h-10 w-10 text-[13px]",
            highlight ? "bg-accent text-accent-foreground" : "bg-foreground/[0.06] text-foreground",
          )}
        />
        <div className="min-w-0">
          <div className="truncate text-[14px] font-semibold leading-tight text-foreground">{member.label}</div>
          <div className="truncate text-[11px] text-subtle-foreground">{member.role}</div>
        </div>
      </div>
      {member.mandate && (
        <p className="mt-3 line-clamp-3 text-[12px] leading-relaxed text-muted-foreground">{member.mandate}</p>
      )}

      {(track || prompt) && (
        <div className="mt-3 space-y-2.5 border-t border-border pt-3">
          {track && (
            <div>
              <div className="text-[10px] font-medium uppercase tracking-[0.08em] text-subtle-foreground">
                Track record
              </div>
              <div className="mt-1 flex items-center gap-2 text-[12px]">
                <span className="tabular-nums text-muted-foreground">
                  {track.count} decision{track.count === 1 ? "" : "s"}
                </span>
                <span
                  className={cx(
                    "rounded px-1.5 py-0.5 text-[11px] font-semibold tabular-nums",
                    CAL_HEAT(track.avg),
                  )}
                >
                  {track.avg.toFixed(0)} cal
                </span>
              </div>
            </div>
          )}
          {prompt && (
            <div>
              <div className="text-[10px] font-medium uppercase tracking-[0.08em] text-subtle-foreground">
                Prompt gate
              </div>
              <div className="mt-1 truncate text-[11.5px] font-medium tabular-nums text-foreground">
                {prompt.current} → {prompt.candidate}
              </div>
              <div className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
                {prompt.promotion_gate}
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

// Map a decision-outcome owner label to a roster id (self-contained; no hot-file coupling).
function ownerToId(owner: string): string | undefined {
  const o = owner.toLowerCase();
  if (o.includes("cfo") || o.includes("chief financial")) return "cfo";
  if (o.includes("treasury")) return "treasury";
  if (o.includes("fp") || o.includes("planning")) return "fpna";
  if (o.includes("risk") || o.includes("audit")) return "risk";
  if (o.includes("procure")) return "procurement";
  if (o.includes("reliab")) return "reliability";
  return undefined;
}

function buildTrackRecords(co: CompanyFinancials | null): Record<string, TrackRecord> {
  const acc: Record<string, { sum: number; n: number }> = {};
  for (const o of co?.decision_outcomes ?? []) {
    const id = ownerToId(o.owner);
    if (!id || o.calibration_score == null) continue;
    acc[id] = acc[id] ?? { sum: 0, n: 0 };
    acc[id].sum += o.calibration_score;
    acc[id].n += 1;
  }
  const out: Record<string, TrackRecord> = {};
  for (const [id, { sum, n }] of Object.entries(acc)) out[id] = { count: n, avg: sum / n };
  return out;
}
