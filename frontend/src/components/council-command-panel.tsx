"use client";

import { useEffect, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Anchor,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  GitCompareArrows,
  Loader2,
  MessageCircleQuestion,
  Pause,
  Play,
  Pin,
  Radio,
  Send,
  ShieldQuestion,
  Split,
  XCircle,
} from "lucide-react";
import type {
  ActiveCommand,
  AgentFocus,
  CommandType,
  CommandResult,
  CommandState,
  ExportStatus,
  OperatorCommand,
  Recommendation,
  RequestedScenario,
  RunwayImpact,
  TranscriptTurn,
} from "@/lib/types";
import { ROSTER, ROSTER_BY_ID } from "@/lib/agents";
import { cx, SectionTitle } from "@/components/ui";
import {
  EASE_OUT_EXPO,
  motionDuration,
  pressTap,
  springSnappy,
} from "@/components/motion/variants";

// The operator steering channel. Every control posts a structured command to the
// server-side dispatcher (/api/command) via `dispatch`; nothing here computes a
// council answer in the browser. Results stream back through DebateState
// (useCoAgent) and are mirrored optimistically by the page via setState.
interface CouncilCommandPanelProps {
  healthReady: boolean;
  running: boolean;
  decision?: string;
  recommendation?: Recommendation;
  transcript: TranscriptTurn[];
  commandState: CommandState;
  dispatch: (command: OperatorCommand) => Promise<CommandResult | undefined>;
}

type ScenarioForm = {
  label: string;
  extra_monthly_spend: string;
  one_time_cost: string;
  added_monthly_revenue: string;
};

const EMPTY_A: ScenarioForm = { label: "Option A", extra_monthly_spend: "", one_time_cost: "", added_monthly_revenue: "" };
const EMPTY_B: ScenarioForm = { label: "Option B", extra_monthly_spend: "", one_time_cost: "", added_monthly_revenue: "" };

const PIN_KINDS = ["policy", "vendor", "financial", "custom"] as const;
type PinKind = (typeof PIN_KINDS)[number];

const STATUS_TONE: Record<string, string> = {
  executed: "border-positive/20 bg-positive-bg text-positive",
  accepted: "border-info/20 bg-info-bg text-info",
  queued: "border-info/20 bg-info-bg text-info",
  rejected: "border-warning/20 bg-warning-bg text-warning",
  failed: "border-risk/20 bg-risk-bg text-risk",
};

const ROLE_COMMAND_CUES: Record<string, { title: string; cue: string; placeholder: string; rerun: string }> = {
  cfo: {
    title: "chair synthesis",
    cue: "Tradeoffs, dissent, conditions, analyst influence, and board-ready ruling logic.",
    placeholder: "Ask the CFO to clarify conditions, defend confidence, or rerun the ruling logic...",
    rerun: "Rerun the CFO synthesis from the analyst record and resolve dissent into conditions.",
  },
  treasury: {
    title: "liquidity mechanics",
    cue: "Cash runway, cash timing, payment terms, burn sensitivity, financing delay, and late-cash downside.",
    placeholder: "Ask about cash arriving late, payment timing, renewal cash, or runway buffer...",
    rerun: "Rerun Treasury using cash forecast, invoices, payment terms, burn, and late-cash timing.",
  },
  fpna: {
    title: "forecastability",
    cue: "ARR movement, pipeline probability, ROI, CAC/payback, margin, sensitivity math, and plan-vs-actual.",
    placeholder: "Ask whether the case is forecastable, which assumption breaks, or how ARR math changes...",
    rerun: "Rerun FP&A using forecast quality, ARR, pipeline probability, ROI, margin, and sensitivity ranges.",
  },
  risk: {
    title: "controls adversary",
    cue: "Policy blockers, approvals, audit trail, source provenance, data quality, fraud/error risk, and hidden obligations.",
    placeholder: "Ask which policy, approval, audit trail, provenance, or hidden obligation blocks support...",
    rerun: "Rerun Risk & Audit as a controls adversary and condition the case on missing evidence.",
  },
  procurement: {
    title: "vendor negotiation",
    cue: "Supplier leverage, renewal dates, auto-renewal, benchmarks, switching cost, SLAs, termination, and discounts.",
    placeholder: "Ask about renewal leverage, benchmark gaps, termination terms, SLAs, or commercial counters...",
    rerun: "Rerun Procurement using vendor exports, invoices, contract metadata, terms, benchmarks, and levers.",
  },
  reliability: {
    title: "evaluator scorecard",
    cue: "Evidence grounding, calibration, policy compliance, debate value, trace quality, weaknesses, replay cases, and prompt directives.",
    placeholder: "Ask Reliability to clarify a score, replay case, trace gap, or prompt-improvement directive...",
    rerun: "Rerun Reliability as an evaluator scorecard only; do not re-decide the case.",
  },
};

function latestPositionFor(agentId: string, transcript: TranscriptTurn[]): Partial<TranscriptTurn> | undefined {
  for (let index = transcript.length - 1; index >= 0; index -= 1) {
    const turn = transcript[index];
    const isMine =
      turn.agent === agentId ||
      (agentId === "cfo" && (turn.type === "framing" || turn.type === "decision"));
    if (isMine && (turn.type === "position" || turn.type === "decision" || turn.type === "framing")) {
      return { stance: turn.stance, headline: turn.headline, argument: turn.argument, key_points: turn.key_points };
    }
  }
  return undefined;
}

function toNumber(value: string): number {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function scenarioParams(form: ScenarioForm) {
  return {
    label: form.label,
    extra_monthly_spend: toNumber(form.extra_monthly_spend),
    one_time_cost: toNumber(form.one_time_cost),
    added_monthly_revenue: toNumber(form.added_monthly_revenue),
  };
}

function downloadMemo(status: ExportStatus) {
  if (typeof window === "undefined" || !status.memo) return;
  const blob = new Blob([status.memo], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${status.id || "atlas-board-memo"}.md`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function statusLabel(status?: string) {
  if (!status) return "Idle";
  return status.replace(/_/g, " ");
}

function CommandDispatchBanner({
  active,
  localResult,
  pending,
  queueLength,
  reduced,
}: {
  active: ActiveCommand;
  localResult: { status: "queued" | "executed" | "rejected" | "failed" | "accepted"; label: string; message: string } | null;
  pending: string | null;
  queueLength: number;
  reduced: boolean;
}) {
  const status = pending ? "queued" : localResult?.status ?? active.status;
  const label = pending ?? localResult?.label ?? active.type ?? "Command channel";
  const message =
    pending
      ? "Dispatching command to the role-specific AG-UI handler."
      : localResult?.message ?? active.message ?? "Command controls are ready.";
  const tone = STATUS_TONE[status ?? ""] ?? "border-border bg-background text-muted-foreground";

  return (
    <motion.div
      role="status"
      aria-live="polite"
      data-command-dispatch-state={status ?? "idle"}
      className={cx("command-dispatch-banner mt-3 rounded-md border px-2.5 py-2", tone)}
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: -5 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduced ? { duration: motionDuration.instant } : { duration: motionDuration.quick, ease: EASE_OUT_EXPO }}
    >
      <div className="flex items-start gap-2">
        <span className={cx("command-dispatch-icon mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md border border-current/20", pending && !reduced && "command-dispatch-icon--pending")}>
          {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CommandStatusIcon status={status} className="h-3.5 w-3.5" />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <div className="truncate text-[11px] font-semibold capitalize">{label}</div>
            <div className="shrink-0 text-[10px] font-semibold uppercase tracking-[0.08em]">{statusLabel(status)}</div>
          </div>
          <p className="mt-0.5 break-words text-[11px] leading-relaxed opacity-85">{message}</p>
        </div>
      </div>
      {queueLength > 0 && (
        <div className="mt-1.5 rounded border border-current/15 bg-background/45 px-2 py-1 text-[10px] font-semibold">
          {queueLength} pending command{queueLength === 1 ? "" : "s"} in queue
        </div>
      )}
    </motion.div>
  );
}

function CommandStatusIcon({ status, className = "" }: { status?: string; className?: string }) {
  switch (status) {
    case "executed":
      return <CheckCircle2 className={className} strokeWidth={2.25} />;
    case "rejected":
      return <AlertTriangle className={className} strokeWidth={2.25} />;
    case "failed":
      return <XCircle className={className} strokeWidth={2.25} />;
    case "accepted":
      return <Radio className={className} strokeWidth={2.25} />;
    case "queued":
      return <Clock3 className={className} strokeWidth={2.25} />;
    default:
      return <Radio className={className} strokeWidth={2.25} />;
  }
}

export function CouncilCommandPanel({
  healthReady,
  running,
  decision,
  recommendation,
  transcript,
  commandState,
  dispatch,
}: CouncilCommandPanelProps) {
  const prefersReducedMotion = useReducedMotion();
  const reduced = Boolean(prefersReducedMotion);
  const [agent, setAgent] = useState("treasury");
  const [ask, setAsk] = useState("");
  const [pinKind, setPinKind] = useState<PinKind>("policy");
  const [pinText, setPinText] = useState("");
  const [optionA, setOptionA] = useState<ScenarioForm>(EMPTY_A);
  const [optionB, setOptionB] = useState<ScenarioForm>(EMPTY_B);
  const [pending, setPending] = useState<string | null>(null);
  const [localResult, setLocalResult] = useState<{
    status: "queued" | "executed" | "rejected" | "failed" | "accepted";
    label: string;
    message: string;
  } | null>(null);

  const disabled = !healthReady;
  // All command sub-states default to an empty object (every field is optional),
  // so an unstarted council renders cleanly.
  const active: ActiveCommand = commandState.active_command ?? {};
  const focus: AgentFocus = commandState.agent_focus ?? {};
  const scenario: RequestedScenario = commandState.requested_scenario ?? {};
  const exportStatus: ExportStatus = commandState.export_status ?? {};
  const pins = commandState.pinned_evidence ?? [];
  const audit = commandState.command_audit_log ?? [];
  const queue = commandState.command_queue ?? [];
  const paused = commandState.phase_controls?.paused ?? false;
  const canExport = Boolean(recommendation?.decision) || Boolean(exportStatus.ready);
  const selectedMember = ROSTER_BY_ID[agent] ?? ROSTER[0];
  const selectedCue = ROLE_COMMAND_CUES[agent] ?? ROLE_COMMAND_CUES.treasury;

  async function run(key: string, command: OperatorCommand) {
    if (disabled || pending) return;
    setPending(key);
    setLocalResult({ status: "queued", label: command.type, message: "Command queued for AG-UI dispatch." });
    try {
      const result = await dispatch(command);
      if (result) {
        setLocalResult({
          status: result.status,
          label: result.command?.type ?? command.type,
          message: result.message ?? result.reason ?? "Command completed.",
        });
      } else {
        setLocalResult({ status: "failed", label: command.type, message: "Command dispatch returned no result." });
      }
    } catch (err) {
      setLocalResult({
        status: "failed",
        label: command.type,
        message: err instanceof Error ? err.message : "Command dispatch failed.",
      });
    } finally {
      setPending(null);
    }
  }

  useEffect(() => {
    if (!localResult || localResult.status === "queued") return;
    const timeout = window.setTimeout(() => setLocalResult(null), 4200);
    return () => window.clearTimeout(timeout);
  }, [localResult]);

  const directContext = () => ({ decision: decision ?? "", position: latestPositionFor(agent, transcript) });

  const askAgent = (type: Extract<CommandType, "clarify" | "route_question" | "challenge_claim" | "defend_position" | "rerun_role">) => {
    const text = ask.trim();
    const optionalText = type === "defend_position" || type === "rerun_role";
    if (!text && !optionalText) return;
    const payload =
      type === "challenge_claim"
        ? { point: text, context: directContext() }
        : type === "defend_position"
          ? { point: text || `Defend the ${selectedCue.title} position.`, context: directContext() }
          : type === "rerun_role"
            ? { reason: text || selectedCue.rerun, context: directContext() }
            : { question: text, context: directContext() };
    run(type, { type, agent, payload, source: "panel" });
  };

  const busy = (key: string) => pending === key;

  return (
    <motion.section
      id="agui-command-panel"
      data-command-panel-state={pending ? "pending" : active.status ?? localResult?.status ?? "idle"}
      className="operator-command-panel shrink-0 rounded-lg border border-border bg-surface p-3 shadow-sm"
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduced ? { duration: motionDuration.instant } : springSnappy}
    >
      <div className="flex items-center justify-between gap-3">
        <div>
          <SectionTitle>Operator Command</SectionTitle>
          <h2 className="mt-0.5 text-[14px] font-semibold">Steer the council (AG-UI)</h2>
        </div>
        <div className="flex items-center gap-2">
          {paused && (
            <span className="inline-flex items-center gap-1 rounded-md border border-warning/20 bg-warning-bg px-2 py-1 text-[11px] font-semibold text-warning">
              <Pause className="h-3 w-3" /> Paused
            </span>
          )}
          <span
            className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-semibold ${
              healthReady ? "border-positive/20 bg-positive-bg text-positive" : "border-risk/20 bg-risk-bg text-risk"
            }`}
          >
            <span className={`h-1.5 w-1.5 rounded-full bg-current ${running && !reduced ? "animate-pulse" : ""}`} />
            {healthReady ? (running ? "Streaming" : "Live") : "Locked"}
          </span>
        </div>
      </div>

      <CommandDispatchBanner
        active={active}
        localResult={localResult}
        pending={pending}
        queueLength={queue.length}
        reduced={reduced}
      />

      {!healthReady && (
        <p className="mt-2 rounded-md border border-dashed border-border bg-background px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
          Commands are gated by the same strict-live preflight as decision submission. They unlock once
          /api/health reports green.
        </p>
      )}

      {/* Direct an agent: clarify / route / challenge / defend / rerun */}
      <div className="mt-3 rounded-md border border-border bg-background p-2.5">
        <div className="flex items-center gap-2">
          <label className="text-[11px] font-semibold text-muted-foreground" htmlFor="cmd-agent">
            Target role
          </label>
          <select
            id="cmd-agent"
            value={agent}
            onChange={(event) => setAgent(event.target.value)}
            disabled={disabled}
            className="min-w-0 flex-1 rounded-md border border-border bg-surface px-2 py-1 text-[12px] outline-none focus:border-border-strong disabled:opacity-50"
          >
            {ROSTER.map((member) => (
              <option key={member.id} value={member.id}>
                {member.label}
              </option>
            ))}
          </select>
        </div>
        <div className="mt-2 rounded border border-info/20 bg-info-bg/20 px-2 py-1.5">
          <div className="text-[10px] font-semibold uppercase tracking-[0.06em] text-info">
            Targeting {selectedMember.label} - {selectedCue.title}
          </div>
          <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{selectedCue.cue}</p>
        </div>
        <textarea
          value={ask}
          onChange={(event) => setAsk(event.target.value)}
          disabled={disabled}
          rows={2}
          placeholder={selectedCue.placeholder}
          className="mt-2 min-h-[48px] w-full resize-none rounded-md border border-border bg-surface px-2.5 py-2 text-[12px] leading-relaxed outline-none placeholder:text-subtle-foreground focus:border-border-strong disabled:opacity-50"
        />
        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-5">
          <CommandButton
            icon={<MessageCircleQuestion className="h-3.5 w-3.5" />}
            label="Clarify"
            title={`Clarify via ${selectedMember.label}: ${selectedCue.title}`}
            busy={busy("clarify")}
            disabled={disabled || !ask.trim()}
            onClick={() => askAgent("clarify")}
          />
          <CommandButton
            icon={<Send className="h-3.5 w-3.5" />}
            label="Route"
            title={`Route to ${selectedMember.label}: ${selectedCue.title}`}
            busy={busy("route_question")}
            disabled={disabled || !ask.trim()}
            onClick={() => askAgent("route_question")}
          />
          <CommandButton
            icon={<ShieldQuestion className="h-3.5 w-3.5" />}
            label="Challenge"
            title={`Challenge ${selectedMember.label}: ${selectedCue.title}`}
            busy={busy("challenge_claim")}
            disabled={disabled || !ask.trim()}
            onClick={() => askAgent("challenge_claim")}
          />
          <CommandButton
            icon={<ShieldQuestion className="h-3.5 w-3.5" />}
            label="Defend"
            title={`Ask ${selectedMember.label} to defend through ${selectedCue.title}`}
            busy={busy("defend_position")}
            disabled={disabled}
            onClick={() => askAgent("defend_position")}
          />
          <CommandButton
            icon={<Send className="h-3.5 w-3.5" />}
            label="Rerun"
            title={`Rerun ${selectedMember.label} with ${selectedCue.title}`}
            busy={busy("rerun_role")}
            disabled={disabled}
            onClick={() => askAgent("rerun_role")}
          />
        </div>
      </div>

      {/* Scenario fork + compare */}
      <div className="mt-2 rounded-md border border-border bg-background p-2.5">
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-semibold text-muted-foreground">Scenario fork & compare</span>
          <span className="text-[10px] text-subtle-foreground">Delta vs. live runway</span>
        </div>
        <ScenarioRow form={optionA} onChange={setOptionA} disabled={disabled} />
        <ScenarioRow form={optionB} onChange={setOptionB} disabled={disabled} />
        <div className="mt-2 grid grid-cols-2 gap-2">
          <CommandButton
            icon={<Split className="h-3.5 w-3.5" />}
            label="Project A"
            busy={busy("scenario_fork")}
            disabled={disabled}
            onClick={() => run("scenario_fork", { type: "scenario_fork", payload: scenarioParams(optionA), source: "panel" })}
          />
          <CommandButton
            icon={<GitCompareArrows className="h-3.5 w-3.5" />}
            label="Compare A vs B"
            busy={busy("compare_options")}
            disabled={disabled}
            onClick={() =>
              run("compare_options", {
                type: "compare_options",
                payload: { options: [scenarioParams(optionA), scenarioParams(optionB)] },
                source: "panel",
              })
            }
          />
        </div>
      </div>

      {/* Pin evidence */}
      <div className="mt-2 rounded-md border border-border bg-background p-2.5">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-semibold text-muted-foreground">Pin evidence</span>
          <select
            value={pinKind}
            onChange={(event) => setPinKind(event.target.value as PinKind)}
            disabled={disabled}
            className="rounded-md border border-border bg-surface px-2 py-1 text-[12px] outline-none focus:border-border-strong disabled:opacity-50"
          >
            {PIN_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <input
            value={pinText}
            onChange={(event) => setPinText(event.target.value)}
            disabled={disabled}
            placeholder={pinKind === "custom" ? "Note to pin..." : pinKind === "financial" ? "field e.g. runway_months" : `Search ${pinKind}...`}
            className="min-w-0 flex-1 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12px] outline-none placeholder:text-subtle-foreground focus:border-border-strong disabled:opacity-50"
          />
          <CommandButton
            icon={<Pin className="h-3.5 w-3.5" />}
            label="Pin"
            busy={busy("pin_evidence")}
            disabled={disabled}
            onClick={() => {
              const value = pinText.trim();
              const payload =
                pinKind === "custom"
                  ? { kind: pinKind, note: value }
                  : pinKind === "financial"
                    ? { kind: pinKind, ref: value, query: value }
                    : { kind: pinKind, query: value };
              run("pin_evidence", { type: "pin_evidence", payload, source: "panel" });
            }}
          />
        </div>
      </div>

      {/* Phase control + export */}
      <div className="mt-2 grid grid-cols-2 gap-2">
        <CommandButton
          icon={paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
          label={paused ? "Resume council" : "Pause council"}
          busy={busy("phase")}
          disabled={disabled}
          onClick={() =>
            run("phase", {
              type: paused ? "resume_phase" : "pause_phase",
              payload: { reason: "operator" },
              source: "panel",
            })
          }
        />
        <CommandButton
          icon={<Anchor className="h-3.5 w-3.5" />}
          label="Export memo"
          busy={busy("export_memo")}
          disabled={disabled || !canExport}
          onClick={() => run("export_memo", { type: "export_memo", payload: {}, source: "panel" })}
        />
      </div>

      {/* Live result strip */}
      <AnimatePresence initial={false}>
        {active.type && (
          <motion.div
            key={`${active.type}-${active.status}-${active.at}`}
            role="status"
            aria-live="polite"
            data-command-result-state={active.status ?? "unknown"}
            className={`command-result-card mt-3 rounded-md border px-3 py-2 text-[12px] leading-relaxed ${STATUS_TONE[active.status ?? ""] ?? "border-border bg-background text-muted-foreground"}`}
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: -4 }}
            transition={reduced ? { duration: motionDuration.instant } : springSnappy}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold">
                {active.type}
                {active.agent ? ` - ${active.agent}` : ""}
              </span>
              <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide">
                <CommandStatusIcon status={active.status} className="h-3 w-3" />
                {active.status}
              </span>
            </div>
            {active.message && <p className="mt-1">{active.message}</p>}
            {active.role_lens && (
              <p className="mt-1 text-[11px] font-medium text-muted-foreground">
                Lens: {active.role_lens}
              </p>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Agent focus reply (clarify / route / challenge / defend / rerun) */}
      <AnimatePresence initial={false}>
        {focus.response && (
          <motion.div
            key={`${focus.agent}-${focus.mode}-${focus.at ?? focus.response}`}
            className="command-focus-card mt-2 rounded-md border border-info/20 bg-info-bg/40 px-3 py-2"
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: -4 }}
            transition={reduced ? { duration: motionDuration.instant } : springSnappy}
          >
            <div className="text-[11px] font-semibold text-info">
              {focus.label ?? focus.agent} - {focus.mode}
              {focus.revised_stance && focus.revised_stance !== "unchanged" ? ` -> ${focus.revised_stance}` : ""}
            </div>
            {focus.role_lens && (
              <div className="mt-0.5 text-[10.5px] font-semibold text-muted-foreground">
                {focus.role_lens}
              </div>
            )}
            {focus.headline && <div className="mt-0.5 text-[12px] font-semibold">{focus.headline}</div>}
            <p className="mt-1 text-[12px] leading-relaxed text-foreground">{focus.response}</p>
            {focus.key_points && focus.key_points.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {focus.key_points.slice(0, 3).map((point) => (
                  <span key={point} className="rounded border border-info/20 bg-background px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                    {point}
                  </span>
                ))}
              </div>
            )}
            {focus.role_instruction && (
              <p className="mt-1.5 border-t border-info/15 pt-1.5 text-[10.5px] leading-relaxed text-muted-foreground">
                Command mandate: {focus.role_instruction}
              </p>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Requested scenario / comparison */}
      {scenario.mode === "single" && scenario.impact && (
        <ScenarioImpact label={scenario.label} impact={scenario.impact} />
      )}
      {scenario.mode === "compare" && scenario.options && scenario.options.length > 0 && (
        <div className="mt-2 rounded-md border border-border bg-background p-2.5">
          <div className="text-[11px] font-semibold text-muted-foreground">Option comparison (live runway)</div>
          <div className="mt-1.5 grid gap-1.5">
            {scenario.options.map((option, index) => (
              <div key={`${option.label}-${index}`} className="flex items-center justify-between gap-2 text-[12px]">
                <span className="truncate font-semibold">{option.label}</span>
                <span className="tabular-nums text-muted-foreground">
                  {formatRunway(option.impact?.scenario_runway_months)} -{" "}
                  <span className={deltaTone(option.impact?.delta_months)}>{formatDelta(option.impact?.delta_months)}</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Pinned evidence */}
      {pins.length > 0 && (
        <div className="mt-2">
          <div className="text-[11px] font-semibold text-muted-foreground">Pinned evidence ({pins.length})</div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {pins.slice(-6).map((pin) => (
              <span
                key={pin.id}
                title={pin.detail}
                className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground"
              >
                <Pin className="h-3 w-3 shrink-0" />
                <span className="truncate">{pin.title}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Export download */}
      {exportStatus.ready && (
        <button
          type="button"
          onClick={() => downloadMemo(exportStatus)}
          className="mt-2 inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-positive/20 bg-positive-bg px-2.5 py-1.5 text-[12px] font-semibold text-positive"
        >
          <Anchor className="h-3.5 w-3.5" />
          Download {exportStatus.title ?? "board memo"}
        </button>
      )}

      {/* Audit log */}
      {audit.length > 0 && (
        <div className="mt-3 border-t border-border pt-2">
          <div className="text-[11px] font-semibold text-muted-foreground">Command log</div>
          <ul className="mt-1.5 space-y-1">
            {audit.slice(-4).reverse().map((entry) => (
              <li key={entry.id} className="flex items-center justify-between gap-2 text-[11px]">
                <span className="truncate text-muted-foreground">
                  {entry.at} - {entry.type}
                  {entry.agent ? ` - ${entry.agent}` : ""}
                </span>
                <span className={`shrink-0 font-semibold ${entry.status === "executed" ? "text-positive" : entry.status === "rejected" ? "text-warning" : entry.status === "failed" ? "text-risk" : "text-info"}`}>
                  {entry.status}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      </motion.section>
  );
}

function CommandButton({
  icon,
  label,
  title,
  busy,
  disabled,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  title?: string;
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  const prefersReducedMotion = useReducedMotion();
  const reduced = Boolean(prefersReducedMotion);
  return (
    <motion.button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title ?? label}
      aria-busy={busy}
      disabled={disabled || busy}
      data-command-button-state={busy ? "pending" : disabled ? "disabled" : "ready"}
      whileTap={reduced || disabled || busy ? undefined : pressTap}
      className={cx(
        "command-panel-button inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-border bg-surface px-2 text-[12px] font-semibold text-foreground transition-colors hover:border-border-strong disabled:opacity-40",
        busy && "command-panel-button--pending border-info/25 bg-info-bg text-info",
      )}
    >
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : icon}
      <span className="truncate">{label}</span>
    </motion.button>
  );
}

function ScenarioRow({
  form,
  onChange,
  disabled,
}: {
  form: ScenarioForm;
  onChange: (next: ScenarioForm) => void;
  disabled: boolean;
}) {
  return (
    <div className="mt-2 grid grid-cols-[1fr_repeat(3,minmax(0,0.7fr))] gap-1.5">
      <input
        value={form.label}
        onChange={(event) => onChange({ ...form, label: event.target.value })}
        disabled={disabled}
        className="min-w-0 rounded-md border border-border bg-surface px-2 py-1 text-[11px] outline-none focus:border-border-strong disabled:opacity-50"
      />
      <NumberInput value={form.extra_monthly_spend} placeholder="+spend" onChange={(value) => onChange({ ...form, extra_monthly_spend: value })} disabled={disabled} />
      <NumberInput value={form.one_time_cost} placeholder="1x cost" onChange={(value) => onChange({ ...form, one_time_cost: value })} disabled={disabled} />
      <NumberInput value={form.added_monthly_revenue} placeholder="+rev" onChange={(value) => onChange({ ...form, added_monthly_revenue: value })} disabled={disabled} />
    </div>
  );
}

function NumberInput({
  value,
  placeholder,
  onChange,
  disabled,
}: {
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  return (
    <input
      value={value}
      onChange={(event) => onChange(event.target.value)}
      disabled={disabled}
      inputMode="decimal"
      placeholder={placeholder}
      className="min-w-0 rounded-md border border-border bg-surface px-2 py-1 text-[11px] tabular-nums outline-none placeholder:text-subtle-foreground focus:border-border-strong disabled:opacity-50"
    />
  );
}

function ScenarioImpact({ label, impact }: { label?: string; impact: RunwayImpact }) {
  return (
    <div className="mt-2 rounded-md border border-border bg-background p-2.5">
      <div className="flex items-center justify-between text-[12px]">
        <span className="truncate font-semibold">{label ?? "Scenario"}</span>
        <span className="tabular-nums text-muted-foreground">
          {formatRunway(impact.current_runway_months)}
          {" -> "}
          {formatRunway(impact.scenario_runway_months)} -{" "}
          <span className={deltaTone(impact.delta_months)}>{formatDelta(impact.delta_months)}</span>
        </span>
      </div>
      {impact.note && <p className="mt-1 text-[11px] text-muted-foreground">{impact.note}</p>}
    </div>
  );
}

function formatRunway(value?: number | null) {
  return typeof value === "number" ? `${value}m` : "n/a";
}

function formatDelta(value?: number | null) {
  if (typeof value !== "number") return "n/a";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value}m`;
}

function deltaTone(value?: number | null) {
  if (typeof value !== "number") return "text-muted-foreground";
  return value < 0 ? "text-risk" : "text-positive";
}
