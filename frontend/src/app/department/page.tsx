"use client";

import { useEffect, useState } from "react";
import { ROSTER, ROSTER_BY_ID } from "@/lib/agents";
import { api } from "@/lib/api";
import type { CompanyFinancials, PromptVersion, RosterMember } from "@/lib/types";
import { AtlasIcon, type AtlasIconName } from "@/components/atlas-icon";
import { cx, Card } from "@/components/ui";

interface TrackRecord {
  count: number;
  avg: number;
}

const MEMBER_ICONS: Record<string, AtlasIconName> = {
  cfo: "memo",
  treasury: "runway",
  fpna: "scenario",
  risk: "risk",
  procurement: "evidence",
  reliability: "health",
};

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
  for (const p of co?.prompt_versions ?? []) {
    const key = p.agent ?? p.role;
    if (key) promptByAgent[key] = p;
  }

  const trackByMember = buildTrackRecords(co);

  return (
    <div className="mx-auto w-full max-w-[1180px] px-4 py-5 sm:px-6">
      <div className="flex items-center gap-3">
        <AtlasIcon name="council" size="sm" className="atlas-icon-badge--quiet" />
        <h1 className="text-[20px] font-semibold tracking-tight text-foreground">Your finance team</h1>
      </div>

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
  const promptSummary = prompt ? summarizePrompt(prompt) : null;

  return (
    <Card className={cx("flex flex-col p-4", highlight ? "w-full max-w-sm border-border-strong" : "")}>
      <div className="flex items-center gap-3">
        <AtlasIcon
          name={MEMBER_ICONS[member.id] ?? "council"}
          size={highlight ? "md" : "sm"}
          className={cx(!highlight && "atlas-icon-badge--quiet")}
        />
        <div className="min-w-0">
          <div className="truncate text-[14px] font-semibold leading-tight text-foreground">{formatMemberLabel(member.label)}</div>
          <div className="truncate text-[11px] text-subtle-foreground">{formatRole(member.role)}</div>
        </div>
      </div>

      {(track || prompt) && (
        <div className="mt-3 space-y-2.5 border-t border-border pt-3">
          {track && (
            <div>
              <div className="mt-1 flex items-center gap-2 text-[12px]">
                <span className="tabular-nums text-muted-foreground">
                  {track.count} decision{track.count === 1 ? "" : "s"}
                </span>
                <span
                  className={cx(
                    "rounded px-1.5 py-0.5 text-[11px] font-semibold",
                    CAL_HEAT(track.avg),
                  )}
                >
                  {track.avg.toFixed(0)}% reliability
                </span>
              </div>
            </div>
          )}
          {promptSummary && (
            <div>
              <div className="mt-1 text-[11.5px] font-medium leading-relaxed text-foreground">
                Current: {promptSummary.current}
              </div>
              <div className="mt-1 text-[10.5px] font-semibold text-info">
                Primary check: {promptSummary.primaryCheck}
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function formatRole(role: string): string {
  return formatDisplayText(role).replace(/\s*&\s*/g, " and ");
}

function formatMemberLabel(label: string): string {
  if (label === "FP&A") return "Financial Planning";
  return label.replace(/\s*&\s*/g, " and ");
}

function summarizePrompt(prompt: PromptVersion) {
  const dimensions = (prompt.reliability_dimensions?.length ? prompt.reliability_dimensions : [prompt.gate_metric])
    .filter(isNonEmptyString)
    .map((dimension) => humanizeKey(dimension));

  return {
    current: formatPromptVersion(prompt.current ?? prompt.version, "Active playbook"),
    candidate: formatPromptVersion(prompt.candidate, "No candidate queued"),
    primaryCheck: humanizeKey(prompt.gate_metric ?? dimensions[0] ?? "Reliability"),
    focusAreas: formatList(dimensions.slice(0, 3)),
  };
}

function isNonEmptyString(value: string | undefined): value is string {
  return Boolean(value);
}

function formatPromptVersion(value: string | undefined, fallback: string): string {
  if (!value) return fallback;
  const match = value.match(/(?:^|\.)v(\d+)[.-](.+)$/i);
  if (match) return `${humanizeKey(match[2] ?? value)}, version ${match[1] ?? ""}`.trim();
  return humanizeKey(value.split(".").at(-1) ?? value);
}

function humanizeKey(value: string): string {
  const friendlyLabels: Record<string, string> = {
    analyst_influence_weighting: "Analyst influence weighting",
    approval_route_accuracy: "Approval route accuracy",
    arr_bridge_accuracy: "ARR bridge accuracy",
    benchmark_grounding: "Benchmark grounding",
    board_chair_ruling: "Board chair ruling",
    "board-chair-ruling": "Board chair ruling",
    board_ruling_quality: "Board ruling quality",
    cash_timing_recall: "Cash timing recall",
    commercial_negotiator: "Commercial negotiation",
    "commercial-negotiator": "Commercial negotiation",
    condition_dissent_chair: "Condition and dissent review",
    "condition-dissent-chair": "Condition and dissent review",
    condition_specificity: "Condition specificity",
    control_gap_detection: "Control gap detection",
    controls_adversary: "Controls review",
    "controls-adversary": "Controls review",
    dissent_resolution: "Dissent resolution",
    downside_evidence_pressure: "Downside evidence pressure",
    evaluator_scorecard: "Evaluator scorecard",
    "evaluator-scorecard": "Evaluator scorecard",
    financing_delay_coverage: "Financing delay coverage",
    forecast_unit_economics: "Forecast and unit economics",
    "forecast-unit-economics": "Forecast and unit economics",
    forecastability_challenge: "Forecastability challenge",
    forecastability_sensitivity: "Forecastability sensitivity",
    "forecastability-sensitivity": "Forecastability sensitivity",
    hidden_obligation_recall: "Hidden obligation recall",
    late_cash_covenants: "Late cash covenants",
    "late-cash-covenants": "Late cash covenants",
    liquidity_mechanics: "Liquidity mechanics",
    "liquidity-mechanics": "Liquidity mechanics",
    negotiation_strategy_quality: "Negotiation strategy quality",
    payment_term_grounding: "Payment term grounding",
    plan_vs_actual_calibration: "Plan versus actual calibration",
    prompt_directive_usefulness: "Prompt directive usefulness",
    provenance_policy_adversary: "Policy provenance review",
    "provenance-policy-adversary": "Policy provenance review",
    renewal_clause_recall: "Renewal clause recall",
    renewal_leverage_redlines: "Renewal leverage review",
    "renewal-leverage-redlines": "Renewal leverage review",
    replay_case_generation: "Replay case generation",
    runway_impact_basis: "Runway impact basis",
    runway_sensitivity: "Runway sensitivity",
    scenario_math_quality: "Scenario math quality",
    scorecard_completeness: "Scorecard completeness",
    scorecard_replay_directives: "Scorecard replay guidance",
    "scorecard-replay-directives": "Scorecard replay guidance",
    source_provenance_coverage: "Source provenance coverage",
    stance_prohibition: "Stance prohibition",
    supplier_leverage_specificity: "Supplier leverage specificity",
    termination_sla_redlines: "Termination and SLA redlines",
    trace_quality_audit: "Trace quality audit",
    unit_economics_grounding: "Unit economics grounding",
    working_capital_precision: "Working capital precision",
  };
  const key = value.toLowerCase();
  if (friendlyLabels[key]) return friendlyLabels[key];

  const acronymLabels: Record<string, string> = {
    ap: "AP",
    arr: "ARR",
    cac: "CAC",
    cfo: "CFO",
    fpna: "FP&A",
    rag: "RAG",
    roi: "ROI",
    sla: "SLA",
  };
  const words = value
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .split(" ")
    .filter(Boolean);

  return words
    .map((word, index) => {
      const normalized = word.toLowerCase();
      if (acronymLabels[normalized]) return acronymLabels[normalized];
      if (index > 0 && ["and", "or", "vs"].includes(normalized)) return normalized;
      return normalized.charAt(0).toUpperCase() + normalized.slice(1);
    })
    .join(" ");
}

function formatDisplayText(value: string): string {
  return value
    .replace(/\s*·\s*/g, ", ")
    .replace(/\bplan-vs-actual\b/gi, "plan versus actual")
    .replace(/\bCAC\/payback\b/g, "CAC payback")
    .replace(/\bfraud\/error\b/gi, "fraud and error")
    .replace(/\btermination\/SLA\b/g, "termination and SLA")
    .replace(/\s*\/\s*/g, " and ")
    .replace(/([a-z])\s*-\s*([a-z])/gi, "$1 $2")
    .replace(/\s+/g, " ")
    .trim();
}

function formatList(values: string[]): string {
  if (values.length === 0) return "General reliability";
  if (values.length === 1) return values[0] ?? "General reliability";
  if (values.length === 2) return `${values[0] ?? ""} and ${values[1] ?? ""}`.trim();
  return `${values.slice(0, -1).join(", ")}, and ${values[values.length - 1] ?? ""}`.trim();
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
