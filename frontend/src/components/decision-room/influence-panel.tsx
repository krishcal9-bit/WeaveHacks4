"use client";

import { Scale } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { Stagger, StaggerItem } from "@/components/motion/stagger";
import { motionDuration, springBar, springSnappy } from "@/components/motion/variants";
import { cx } from "@/components/ui";
import { reliabilityColor, toneClasses } from "@/lib/council";
import { ROSTER_BY_ID } from "@/lib/agents";
import type { AgentInfluence, CouncilInfluenceReport, ReliabilityScore } from "@/lib/types";
import { EmptyState, Panel, SkeletonText, StatusBadge } from "./primitives";

export function InfluencePanel({
  councilInfluence,
  reliabilityScores,
  running,
  started,
  phase,
}: {
  councilInfluence?: CouncilInfluenceReport;
  reliabilityScores: ReliabilityScore[];
  running: boolean;
  started: boolean;
  phase?: string;
}) {
  const weights = councilInfluence?.ranked_weights ?? councilInfluence?.weights ?? [];
  const leader = councilInfluence?.leader;
  const spread = councilInfluence?.spread;
  const hasInfluence = weights.length > 0;
  const awaitingInfluence = started && !hasInfluence && (running || ["debate", "influence", "synthesis"].includes(phase ?? ""));

  return (
    <Panel
      id="council-influence"
      icon={Scale}
      visualIcon="council"
      title="Deliberation influence"
      action={
        leader ? (
          <StatusBadge tone="info">
            {ROSTER_BY_ID[leader.agent_id]?.label ?? leader.agent_id} leads
          </StatusBadge>
        ) : undefined
      }
    >
      {awaitingInfluence ? (
        <div>
          <p className="mb-2 text-[12px] font-semibold text-info">Council is scoring who earned the most weight…</p>
          <SkeletonText lines={3} />
        </div>
      ) : !hasInfluence ? (
        <EmptyState icon={Scale} visualIcon="council">
          {started
            ? "After cross-examination, the council assigns unequal influence weights before the CFO rules."
            : "Influence weights appear once the council convenes and cross-examines."}
        </EmptyState>
      ) : (
        <>
          {councilInfluence?.summary && (
            <p className="break-words text-[12px] leading-relaxed text-muted-foreground">{councilInfluence.summary}</p>
          )}

          {typeof spread === "number" && (
            <p className="mt-2 text-[11px] text-subtle-foreground">
              Influence spread <span className="font-semibold text-foreground">{spread}</span>
              {spread >= 16 ? " — a clear leader is steering the ruling." : spread <= 8 ? " — council is flat; CFO must break ties." : " — mixed council with differentiated seats."}
            </p>
          )}

          <Stagger fast className="mt-3 grid gap-2">
            {weights.map((weight, index) => (
              <StaggerItem key={weight.agent_id}>
                <InfluenceBar
                  weight={weight}
                  rank={index + 1}
                  isLeader={leader?.agent_id === weight.agent_id}
                  postHocReliability={reliabilityScores.find((score) => score.agent_id === weight.agent_id)?.reliability}
                />
              </StaggerItem>
            ))}
          </Stagger>

          {councilInfluence?.decision_type_fit && (
            <p className="mt-3 rounded-md border border-border bg-background px-2.5 py-2 text-[11px] leading-relaxed text-muted-foreground">
              <span className="font-semibold text-foreground">Decision fit:</span> {councilInfluence.decision_type_fit}
            </p>
          )}
        </>
      )}
    </Panel>
  );
}

function InfluenceBar({
  weight,
  rank,
  isLeader,
  postHocReliability,
}: {
  weight: AgentInfluence;
  rank: number;
  isLeader: boolean;
  postHocReliability?: number;
}) {
  const reduced = useReducedMotion();
  const label = ROSTER_BY_ID[weight.agent_id]?.label ?? weight.agent_id;
  const value = Math.max(0, Math.min(100, weight.influence_weight));
  const color = reliabilityColor(value);

  return (
    <motion.div
      layout
      className={cx("rounded-md border px-2.5 py-2", isLeader ? "border-info/35 bg-info-bg/20" : "border-border bg-background")}
      whileHover={reduced ? undefined : { y: -1 }}
      transition={springSnappy}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] font-bold tabular-nums text-subtle-foreground">#{rank}</span>
            <span className="truncate text-[12px] font-semibold">{label}</span>
            {isLeader && <StatusBadge tone="info">Lead</StatusBadge>}
          </div>
          {weight.rationale && <p className="mt-0.5 line-clamp-2 text-[10px] leading-relaxed text-muted-foreground">{weight.rationale}</p>}
        </div>
        <span className="shrink-0 text-[13px] font-bold tabular-nums" style={{ color }}>
          {value}%
        </span>
      </div>

      <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-muted">
        <motion.span
          className={cx("block h-full rounded-full", isLeader && "council-influence-shimmer")}
          initial={reduced ? false : { width: 0 }}
          animate={{ width: `${value}%` }}
          transition={reduced ? { duration: motionDuration.quick } : springBar}
          style={{ background: color }}
        />
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5">
        <SignalChip label="Grounding" value={weight.grounding_signal} />
        <SignalChip label="Debate" value={weight.debate_signal} />
        <SignalChip label="History" value={weight.historical_reliability} />
        {typeof postHocReliability === "number" && (
          <span className="rounded border border-border bg-surface px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
            Post-hoc <span className="font-semibold text-foreground">{postHocReliability}%</span>
          </span>
        )}
      </div>
    </motion.div>
  );
}

function SignalChip({ label, value }: { label: string; value?: number }) {
  if (typeof value !== "number") return null;
  const tone = toneClasses(value >= 70 ? "positive" : value >= 55 ? "info" : "warning");
  return (
    <span className={cx("rounded border px-1.5 py-0.5 text-[10px] font-medium", tone.soft)}>
      {label} <span className="font-semibold">{value}</span>
    </span>
  );
}
