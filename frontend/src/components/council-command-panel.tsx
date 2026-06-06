"use client";

import { useState } from "react";
import {
  Anchor,
  GitCompareArrows,
  Loader2,
  MessageCircleQuestion,
  Pause,
  Play,
  Pin,
  Send,
  ShieldQuestion,
  Split,
} from "lucide-react";
import type {
  ActiveCommand,
  AgentFocus,
  CommandResult,
  CommandState,
  ExportStatus,
  OperatorCommand,
  Recommendation,
  RequestedScenario,
  RunwayImpact,
  TranscriptTurn,
} from "@/lib/types";
import { ROSTER } from "@/lib/agents";
import { SectionTitle } from "@/components/ui";

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

export function CouncilCommandPanel({
  healthReady,
  running,
  decision,
  recommendation,
  transcript,
  commandState,
  dispatch,
}: CouncilCommandPanelProps) {
  const [agent, setAgent] = useState("treasury");
  const [ask, setAsk] = useState("");
  const [pinKind, setPinKind] = useState<PinKind>("policy");
  const [pinText, setPinText] = useState("");
  const [optionA, setOptionA] = useState<ScenarioForm>(EMPTY_A);
  const [optionB, setOptionB] = useState<ScenarioForm>(EMPTY_B);
  const [pending, setPending] = useState<string | null>(null);

  const disabled = !healthReady;
  // All command sub-states default to an empty object (every field is optional),
  // so an unstarted council renders cleanly.
  const active: ActiveCommand = commandState.active_command ?? {};
  const focus: AgentFocus = commandState.agent_focus ?? {};
  const scenario: RequestedScenario = commandState.requested_scenario ?? {};
  const exportStatus: ExportStatus = commandState.export_status ?? {};
  const pins = commandState.pinned_evidence ?? [];
  const audit = commandState.command_audit_log ?? [];
  const paused = commandState.phase_controls?.paused ?? false;
  const canExport = Boolean(recommendation?.decision) || Boolean(exportStatus.ready);

  async function run(key: string, command: OperatorCommand) {
    if (disabled || pending) return;
    setPending(key);
    try {
      await dispatch(command);
    } finally {
      setPending(null);
    }
  }

  const directContext = () => ({ decision: decision ?? "", position: latestPositionFor(agent, transcript) });

  const askAgent = (type: "clarify" | "route_question" | "challenge_claim") => {
    const text = ask.trim();
    if (!text) return;
    const payload =
      type === "challenge_claim"
        ? { point: text, context: directContext() }
        : { question: text, context: directContext() };
    run(type, { type, agent, payload, source: "panel" });
  };

  const busy = (key: string) => pending === key;

  return (
    <section className="rounded-lg border border-border bg-surface p-3 shadow-sm">
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
            <span className={`h-1.5 w-1.5 rounded-full bg-current ${running ? "animate-pulse" : ""}`} />
            {healthReady ? (running ? "Streaming" : "Live") : "Locked"}
          </span>
        </div>
      </div>

      {!healthReady && (
        <p className="mt-2 rounded-md border border-dashed border-border bg-background px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
          Commands are gated by the same strict-live preflight as decision submission. They unlock once
          /api/health reports green.
        </p>
      )}

      {/* Direct an agent: clarify / route / challenge */}
      <div className="mt-3 rounded-md border border-border bg-background p-2.5">
        <div className="flex items-center gap-2">
          <label className="text-[11px] font-semibold text-muted-foreground" htmlFor="cmd-agent">
            Direct
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
        <textarea
          value={ask}
          onChange={(event) => setAsk(event.target.value)}
          disabled={disabled}
          rows={2}
          placeholder="Ask to clarify, route a question, or challenge a claim…"
          className="mt-2 min-h-[48px] w-full resize-none rounded-md border border-border bg-surface px-2.5 py-2 text-[12px] leading-relaxed outline-none placeholder:text-subtle-foreground focus:border-border-strong disabled:opacity-50"
        />
        <div className="mt-2 grid grid-cols-3 gap-2">
          <CommandButton
            icon={<MessageCircleQuestion className="h-3.5 w-3.5" />}
            label="Clarify"
            busy={busy("clarify")}
            disabled={disabled || !ask.trim()}
            onClick={() => askAgent("clarify")}
          />
          <CommandButton
            icon={<Send className="h-3.5 w-3.5" />}
            label="Route"
            busy={busy("route_question")}
            disabled={disabled || !ask.trim()}
            onClick={() => askAgent("route_question")}
          />
          <CommandButton
            icon={<ShieldQuestion className="h-3.5 w-3.5" />}
            label="Challenge"
            busy={busy("challenge_claim")}
            disabled={disabled || !ask.trim()}
            onClick={() => askAgent("challenge_claim")}
          />
        </div>
      </div>

      {/* Scenario fork + compare */}
      <div className="mt-2 rounded-md border border-border bg-background p-2.5">
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-semibold text-muted-foreground">Scenario fork & compare</span>
          <span className="text-[10px] text-subtle-foreground">Δ vs. live runway</span>
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
            placeholder={pinKind === "custom" ? "Note to pin…" : pinKind === "financial" ? "field e.g. runway_months" : `Search ${pinKind}…`}
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
      {active.type && (
        <div className={`mt-3 rounded-md border px-3 py-2 text-[12px] leading-relaxed ${STATUS_TONE[active.status ?? ""] ?? "border-border bg-background text-muted-foreground"}`}>
          <div className="flex items-center justify-between gap-2">
            <span className="font-semibold">
              {active.type}
              {active.agent ? ` · ${active.agent}` : ""}
            </span>
            <span className="text-[10px] uppercase tracking-wide">{active.status}</span>
          </div>
          {active.message && <p className="mt-1">{active.message}</p>}
        </div>
      )}

      {/* Agent focus reply (clarify / route / challenge) */}
      {focus.response && (
        <div className="mt-2 rounded-md border border-info/20 bg-info-bg/40 px-3 py-2">
          <div className="text-[11px] font-semibold text-info">
            {focus.label ?? focus.agent} · {focus.mode}
            {focus.revised_stance && focus.revised_stance !== "unchanged" ? ` → ${focus.revised_stance}` : ""}
          </div>
          {focus.headline && <div className="mt-0.5 text-[12px] font-semibold">{focus.headline}</div>}
          <p className="mt-1 text-[12px] leading-relaxed text-foreground">{focus.response}</p>
        </div>
      )}

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
                  {formatRunway(option.impact?.scenario_runway_months)} ·{" "}
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
                  {entry.at} · {entry.type}
                  {entry.agent ? ` · ${entry.agent}` : ""}
                </span>
                <span className={`shrink-0 font-semibold ${entry.status === "executed" ? "text-positive" : entry.status === "rejected" ? "text-warning" : entry.status === "failed" ? "text-risk" : "text-info"}`}>
                  {entry.status}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function CommandButton({
  icon,
  label,
  busy,
  disabled,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-border bg-surface px-2 text-[12px] font-semibold text-foreground transition-colors hover:border-border-strong disabled:opacity-40"
    >
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : icon}
      <span className="truncate">{label}</span>
    </button>
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
      <NumberInput value={form.one_time_cost} placeholder="1×cost" onChange={(value) => onChange({ ...form, one_time_cost: value })} disabled={disabled} />
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
          {formatRunway(impact.current_runway_months)} → {formatRunway(impact.scenario_runway_months)} ·{" "}
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
  if (typeof value !== "number") return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value}m`;
}

function deltaTone(value?: number | null) {
  if (typeof value !== "number") return "text-muted-foreground";
  return value < 0 ? "text-risk" : "text-positive";
}
