"use client";

import { useState } from "react";
import { useCoAgent, useCopilotChatHeadless_c } from "@copilotkit/react-core";
import { ArrowUp, Loader2 } from "lucide-react";
import type { DebateState, TranscriptTurn } from "@/lib/types";
import { decisionStyle, resolveMember, ROSTER_BY_ID, STANCE_STYLE } from "@/lib/agents";
import { Card, Monogram, Pill, SectionTitle } from "@/components/ui";
import { fmtMonths, fmtSignedMonths } from "@/lib/format";

const EXAMPLES = [
  "Should we renew the $180k/yr Datadog contract as-is, or renegotiate it down?",
  "Should we hire 5 engineers next quarter (~$95k/mo) or extend runway?",
  "A vendor wants $250k upfront for a year of an analytics platform — approve it?",
];

const NODE_LABEL: Record<string, string> = {
  intake: "Convening the committee",
  treasury: "Treasury is forming its position",
  fpna: "FP&A is forming its position",
  risk: "Risk & Audit is forming its position",
  procurement: "Procurement is forming its position",
  debate: "Committee cross-examination",
  synthesis: "The CFO is deliberating",
  persist: "Recording the decision",
};

export default function DecisionsPage() {
  const [input, setInput] = useState("");
  const { state, running, nodeName } = useCoAgent<DebateState>({ name: "finance_department" });
  const { sendMessage } = useCopilotChatHeadless_c();

  const transcript: TranscriptTurn[] = state?.transcript ?? [];
  const recommendation = state?.recommendation;
  const started = transcript.length > 0 || running;

  async function submit(text: string) {
    const content = text.trim();
    if (!content || running) return;
    setInput("");
    await sendMessage({ id: crypto.randomUUID(), role: "user", content });
  }

  return (
    <div className="mx-auto max-w-[940px] px-8 py-8">
      <SectionTitle>Decision Room</SectionTitle>
      <h1 className="mt-1.5 text-[22px] font-semibold tracking-tight">
        Bring a decision to the committee
      </h1>
      <p className="mt-1 text-[13px] text-muted-foreground">
        Pose any financial decision. Treasury, FP&amp;A, Risk &amp; Audit, and Procurement weigh in,
        debate it, and the CFO issues a board-ready recommendation.
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit(input);
        }}
        className="mt-5"
      >
        <div className="flex items-end gap-2 rounded-xl border border-border bg-surface p-2 shadow-sm focus-within:border-border-strong">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit(input);
              }
            }}
            rows={2}
            disabled={running}
            placeholder="e.g. Should we sign the $250k enterprise security audit, or defer to next quarter?"
            className="max-h-40 min-h-[44px] flex-1 resize-none bg-transparent px-2 py-1.5 text-[14px] leading-relaxed outline-none placeholder:text-subtle-foreground disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={running || !input.trim()}
            className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accent text-accent-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
            aria-label="Submit decision"
          >
            {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowUp className="h-4 w-4" strokeWidth={2.25} />}
          </button>
        </div>
      </form>

      {!started && (
        <div className="mt-4 flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => submit(ex)}
              className="rounded-full border border-border bg-surface px-3 py-1.5 text-left text-[12px] text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground"
            >
              {ex}
            </button>
          ))}
        </div>
      )}

      {started && (
        <Boardroom
          decision={state?.decision}
          transcript={transcript}
          running={running}
          nodeName={nodeName}
          recommendation={recommendation}
        />
      )}
    </div>
  );
}

function Boardroom({
  decision,
  transcript,
  running,
  nodeName,
  recommendation,
}: {
  decision?: string;
  transcript: TranscriptTurn[];
  running: boolean;
  nodeName?: string;
  recommendation?: DebateState["recommendation"];
}) {
  const turns = transcript.filter((t) => t.type !== "decision");
  return (
    <div className="mt-8">
      {decision && (
        <div className="mb-5 border-l-2 border-accent pl-4">
          <div className="text-[11px] font-medium uppercase tracking-wider text-subtle-foreground">
            Decision under review
          </div>
          <div className="mt-1 text-[15px] font-medium leading-snug">{decision}</div>
        </div>
      )}

      <div className="space-y-3">
        {turns.map((t, i) => (
          <TurnView key={i} turn={t} />
        ))}
      </div>

      {running && (
        <div className="mt-4 flex items-center gap-2.5 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span>{NODE_LABEL[nodeName ?? ""] ?? "Deliberating"}…</span>
        </div>
      )}

      {recommendation?.decision && <Resolution rec={recommendation} />}
    </div>
  );
}

function TurnView({ turn }: { turn: TranscriptTurn }) {
  if (turn.type === "rebuttal") {
    const from = resolveMember(turn.from_role);
    const to = resolveMember(turn.to_role);
    return (
      <div className="flex gap-3 pl-1">
        <div className="mt-1 flex items-center gap-1 text-[11px] font-medium text-subtle-foreground">
          <span className="rounded bg-surface-muted px-1.5 py-0.5">{from?.label ?? turn.from_role}</span>
          <span>→</span>
          <span className="rounded bg-surface-muted px-1.5 py-0.5">{to?.label ?? turn.to_role}</span>
        </div>
        <p className="flex-1 text-[13px] leading-relaxed text-muted-foreground">{turn.point}</p>
      </div>
    );
  }

  const member = turn.agent ? ROSTER_BY_ID[turn.agent] : undefined;
  const isFraming = turn.type === "framing";
  return (
    <Card className={`p-4 ${isFraming ? "bg-surface-muted/40" : ""}`}>
      <div className="flex items-start gap-3">
        <Monogram
          text={turn.monogram ?? member?.monogram ?? "··"}
          className="mt-0.5 h-8 w-8 bg-foreground/[0.06] text-[11px] text-foreground"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <div className="text-[13px] font-semibold">{turn.label ?? member?.label}</div>
            {turn.stance && (
              <Pill className={STANCE_STYLE[turn.stance]?.cls ?? "border-border text-muted-foreground"}>
                {STANCE_STYLE[turn.stance]?.label ?? turn.stance}
              </Pill>
            )}
          </div>
          {turn.headline && <div className="mt-0.5 text-[13px] font-medium">{turn.headline}</div>}
          {turn.argument && (
            <p className="mt-1.5 text-[13px] leading-relaxed text-muted-foreground">{turn.argument}</p>
          )}
          {turn.key_points && turn.key_points.length > 0 && (
            <ul className="mt-2 space-y-1">
              {turn.key_points.map((p, i) => (
                <li key={i} className="flex gap-2 text-[12px] text-muted-foreground">
                  <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-subtle-foreground" />
                  {p}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Card>
  );
}

function Resolution({ rec }: { rec: NonNullable<DebateState["recommendation"]> }) {
  const impact = rec.impact;
  return (
    <Card className="mt-5 overflow-hidden">
      <div className="border-b border-border bg-surface-muted/50 px-5 py-3">
        <SectionTitle>Committee Resolution</SectionTitle>
      </div>
      <div className="p-5">
        <div className="flex items-center gap-3">
          <Pill className={`${decisionStyle(rec.decision)} text-[12px]`}>{rec.decision}</Pill>
          {typeof rec.confidence === "number" && (
            <span className="text-[13px] text-muted-foreground tabular-nums">
              {rec.confidence}% confidence
            </span>
          )}
        </div>

        {rec.rationale && (
          <p className="mt-3 text-[14px] leading-relaxed">{rec.rationale}</p>
        )}

        {impact && typeof impact.scenario_runway_months === "number" && (
          <div className="mt-4 flex items-center gap-6 rounded-lg border border-border bg-background px-4 py-3">
            <Metric label="Runway today" value={fmtMonths(impact.current_runway_months)} />
            <span className="text-subtle-foreground">→</span>
            <Metric label="After this decision" value={fmtMonths(impact.scenario_runway_months)} />
            <Metric
              label="Impact"
              value={fmtSignedMonths(impact.delta_months)}
              tone={(impact.delta_months ?? 0) < 0 ? "risk" : "positive"}
            />
          </div>
        )}

        <div className="mt-4 grid grid-cols-2 gap-6">
          {rec.key_risks && rec.key_risks.length > 0 && (
            <ResolutionList title="Key risks" items={rec.key_risks} dot="bg-risk" />
          )}
          {rec.conditions && rec.conditions.length > 0 && (
            <ResolutionList title="Conditions" items={rec.conditions} dot="bg-info" />
          )}
        </div>
      </div>
    </Card>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "risk" | "positive" }) {
  const cls = tone === "risk" ? "text-risk" : tone === "positive" ? "text-positive" : "text-foreground";
  return (
    <div>
      <div className="text-[11px] text-subtle-foreground">{label}</div>
      <div className={`mt-0.5 text-[15px] font-semibold tabular-nums ${cls}`}>{value}</div>
    </div>
  );
}

function ResolutionList({ title, items, dot }: { title: string; items: string[]; dot: string }) {
  return (
    <div>
      <div className="text-[11px] font-medium uppercase tracking-wider text-subtle-foreground">{title}</div>
      <ul className="mt-2 space-y-1.5">
        {items.map((it, i) => (
          <li key={i} className="flex gap-2 text-[12px] leading-relaxed text-muted-foreground">
            <span className={`mt-1.5 h-1 w-1 shrink-0 rounded-full ${dot}`} />
            {it}
          </li>
        ))}
      </ul>
    </div>
  );
}
