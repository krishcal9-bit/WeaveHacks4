"use client";

import { memo, useEffect, useMemo, useRef, type CSSProperties } from "react";
import { AnimatePresence, motion, useReducedMotion, type Variants } from "motion/react";
import { CheckCircle2, CornerDownRight, Loader2, MessagesSquare, Sparkles } from "lucide-react";
import {
  EASE_OUT_EXPO,
  motionDuration,
  springSnappy,
  springSoft,
  staggerDelay,
  transitionReduced,
} from "@/components/motion/variants";
import { cx } from "@/components/ui";
import { AGENT_TONE, resolveMember, ROSTER_BY_ID } from "@/lib/agents";
import {
  activeCouncilWorkers,
  NODE_LABEL,
  stanceTone,
  toneClasses,
} from "@/lib/council";
import type { AgentStatus, DebateState, EvidenceItem, TranscriptTurn } from "@/lib/types";
import { agentIcon } from "./agent-visuals";
import { CfoRulingCard } from "./cfo-ruling";
import { EmptyState, Panel, SkeletonText, StatusBadge, type IconType } from "./primitives";

const TYPE_LABEL: Record<string, string> = {
  framing: "Framing",
  thinking: "Thinking",
  position: "Position",
  rebuttal: "Cross-exam",
  decision: "Ruling",
  influence: "Influence",
  reliability: "Eval",
};

const CHALLENGE_TYPE_LABEL: Record<string, string> = {
  cash_timing: "Cash timing",
  forecast_assumptions: "Forecast assumptions",
  controls_policy: "Controls / policy",
  vendor_terms: "Vendor terms",
  synthesis_question: "Synthesis question",
};

const ROLE_ACCENT_VAR: Record<string, string> = {
  cfo: "var(--accent)",
  treasury: "var(--info)",
  fpna: "var(--warning)",
  risk: "var(--risk)",
  procurement: "var(--positive)",
  reliability: "var(--border-strong)",
};

const ROLE_ENTRY_X: Record<string, number> = {
  cfo: 0,
  treasury: -18,
  fpna: 18,
  risk: -22,
  procurement: 22,
  reliability: 0,
};

type TranscriptMotionKind = "thinking" | "rebuttal" | "decision" | "reliability" | "evidence" | "default";
type TranscriptAccentStyle = CSSProperties & { "--transcript-accent": string };

function transcriptAccentStyle(seatId: string): TranscriptAccentStyle {
  return { "--transcript-accent": ROLE_ACCENT_VAR[seatId] ?? "var(--border-strong)" };
}

function transcriptEntrance(kind: TranscriptMotionKind, seatId: string): Variants {
  const x = kind === "decision" || kind === "reliability" ? 0 : (ROLE_ENTRY_X[seatId] ?? -10);
  const y = kind === "decision" ? 16 : kind === "thinking" ? 8 : 10;
  return {
    hidden: {
      opacity: 0,
      x,
      y,
      scale: kind === "decision" ? 0.98 : 0.992,
      filter: kind === "thinking" ? "blur(5px)" : "blur(3px)",
    },
    show: {
      opacity: 1,
      x: 0,
      y: 0,
      scale: 1,
      filter: "blur(0px)",
      transition: { duration: kind === "decision" ? motionDuration.emphasis : motionDuration.reveal, ease: EASE_OUT_EXPO },
    },
  };
}

function reducedFadeProps() {
  return {
    initial: { opacity: 0 },
    animate: { opacity: 1 },
    exit: { opacity: 0, transition: transitionReduced },
    transition: transitionReduced,
  };
}

export function TranscriptStream({
  transcript,
  recommendation,
  running,
  nodeName,
  healthReady,
  started,
  agentStatuses,
}: {
  transcript: TranscriptTurn[];
  recommendation?: DebateState["recommendation"];
  running: boolean;
  nodeName?: string;
  healthReady: boolean;
  started: boolean;
  agentStatuses: AgentStatus[];
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const prefersReducedMotion = useReducedMotion();
  const reduced = Boolean(prefersReducedMotion);

  const workers = useMemo(
    () => activeCouncilWorkers(agentStatuses, running, nodeName),
    [agentStatuses, running, nodeName],
  );

  const cappedTranscript = useMemo(
    () => (transcript.length > 80 ? transcript.slice(-80) : transcript),
    [transcript],
  );
  const thinkingTurns = useMemo(
    () => cappedTranscript.filter((turn) => turn.type === "thinking"),
    [cappedTranscript],
  );
  const spokenTurns = useMemo(
    () => cappedTranscript.filter((turn) => turn.type !== "thinking"),
    [cappedTranscript],
  );
  const animateNewTurns = running && !reduced;

  useEffect(() => {
    if (pinned.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [transcript.length, workers.length]);

  const phaseLabel = running ? (NODE_LABEL[nodeName ?? ""] ?? "Council in session") : undefined;

  return (
    <Panel
      id="council-transcript"
      icon={MessagesSquare}
      visualIcon="council"
      title="Debate"
      count={transcript.length}
      action={
        running ? (
          <StatusBadge tone="info" pulse>
            Live
          </StatusBadge>
        ) : undefined
      }
      bodyClassName="p-0"
    >
      {running && (workers.length > 0 || phaseLabel) && (
        <LiveCouncilBar workers={workers} phaseLabel={phaseLabel} thinkingCount={thinkingTurns.length} />
      )}

      <div
        ref={scrollRef}
        onScroll={(event) => {
          const el = event.currentTarget;
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
        }}
        className="room-scroll max-h-[560px] min-h-[260px] overflow-y-auto px-3 py-3"
      >
        {transcript.length === 0 ? (
          started ? (
            <SkeletonTurns />
          ) : (
            <EmptyState icon={MessagesSquare} visualIcon={healthReady ? "council" : "health"}>
              {healthReady
                ? "Submit a decision to open the live council transcript."
                : "Strict preflight must pass before the council can convene."}
            </EmptyState>
          )
        ) : (
          <ol className="space-y-2.5">
            {animateNewTurns ? (
              <AnimatePresence initial={false} mode="popLayout">
                {thinkingTurns.map((turn, index) => (
                  <ThinkingRow key={turn.id ?? `thinking-${turn.agent}-${index}`} turn={turn} index={index} />
                ))}
                {spokenTurns.map((turn, index) => (
                  <TurnRow
                    key={turn.id ?? `${turn.type}-${turn.agent ?? turn.from_role}-${index}`}
                    turn={turn}
                    index={index}
                    recommendation={recommendation}
                  />
                ))}
              </AnimatePresence>
            ) : (
              <>
                {thinkingTurns.map((turn, index) => (
                  <ThinkingRow key={turn.id ?? `thinking-${turn.agent}-${index}`} turn={turn} index={index} staticRow />
                ))}
                {spokenTurns.map((turn, index) => (
                  <TurnRow
                    key={turn.id ?? `${turn.type}-${turn.agent ?? turn.from_role}-${index}`}
                    turn={turn}
                    index={index}
                    recommendation={recommendation}
                    staticRow
                  />
                ))}
              </>
            )}
          </ol>
        )}
      </div>
    </Panel>
  );
}

function LiveCouncilBar({
  workers,
  phaseLabel,
  thinkingCount,
}: {
  workers: AgentStatus[];
  phaseLabel?: string;
  thinkingCount: number;
}) {
  const reduced = useReducedMotion();

  return (
    <div className="border-b border-border bg-info-bg/25 px-3 py-2.5">
      {phaseLabel && (
        <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold text-info">
          <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2.25} />
          <span className="truncate">{phaseLabel}</span>
          {thinkingCount > 0 && (
            <span className="rounded-full border border-info/25 bg-background/80 px-1.5 py-0.5 text-[10px] tabular-nums">
              {thinkingCount} thinking
            </span>
          )}
        </div>
      )}
      <div className="grid gap-1.5 sm:grid-cols-2">
        {workers.map((worker) => (
          <motion.div
            key={worker.id}
            layout="position"
            style={transcriptAccentStyle(worker.id)}
            className={cx(
              "transcript-live-worker flex min-h-[58px] min-w-0 items-start gap-2 rounded-md border border-info/20 bg-background/90 px-2.5 py-2",
              !reduced && "transcript-streaming-shimmer",
            )}
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: 6, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={reduced ? transitionReduced : { duration: motionDuration.normal, ease: EASE_OUT_EXPO }}
          >
            <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-md border border-info/20 bg-info-bg text-info">
              <Sparkles className="h-3 w-3 animate-pulse" strokeWidth={2.25} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[11px] font-semibold">{worker.label}</div>
              <div className="line-clamp-2 text-[10px] leading-relaxed text-muted-foreground">
                {worker.detail ?? "Working…"}
              </div>
            </div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}

const ThinkingRow = memo(function ThinkingRow({
  turn,
  index,
  staticRow = false,
}: {
  turn: TranscriptTurn;
  index: number;
  staticRow?: boolean;
}) {
  const prefersReducedMotion = useReducedMotion();
  const reduced = Boolean(prefersReducedMotion) || staticRow;
  const speaker = turn.agent ? ROSTER_BY_ID[turn.agent] : resolveMember(turn.role);
  const seatId = speaker?.id ?? turn.agent ?? "cfo";
  const accent = toneClasses(AGENT_TONE[seatId] ?? "info");
  const motionProps = reduced
    ? reducedFadeProps()
    : {
        variants: transcriptEntrance("thinking", seatId),
        initial: "hidden",
        animate: "show",
        exit: { opacity: 0, y: -6, scale: 0.985, transition: { duration: motionDuration.fast } },
        transition: { ...springSnappy, delay: staggerDelay(index, 0.018, 0.12) },
      };

  return (
    <motion.li
      layout="position"
      {...motionProps}
      style={transcriptAccentStyle(seatId)}
      data-transcript-turn-type="thinking"
      className={cx(
        "transcript-turn-row transcript-turn-row--thinking min-h-[88px] min-w-0 rounded-md border border-dashed bg-background p-2.5",
        accent.border,
        "border-l-2",
        accent.ring,
        !reduced && "transcript-streaming-shimmer",
      )}
    >
      <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
        <div className="flex min-w-0 items-center gap-2">
          <span className={cx("grid h-6 w-6 shrink-0 place-items-center rounded-md border border-border bg-surface", accent.text)}>
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2.25} />
          </span>
          <span className="truncate text-[12px] font-semibold">{speaker?.label ?? turn.label ?? "Council"}</span>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-1.5 sm:justify-end">
          <StatusBadge tone="info" pulse>
            Thinking
          </StatusBadge>
        </div>
      </div>
      {turn.argument && (
        <p className="mt-2 break-words text-[12px] leading-relaxed text-muted-foreground">{turn.argument}</p>
      )}
    </motion.li>
  );
});

const TurnRow = memo(function TurnRow({
  turn,
  index,
  recommendation,
  staticRow = false,
}: {
  turn: TranscriptTurn;
  index: number;
  recommendation?: DebateState["recommendation"];
  staticRow?: boolean;
}) {
  const prefersReducedMotion = useReducedMotion();
  const reduced = Boolean(prefersReducedMotion) || staticRow;
  const isRebuttal = turn.type === "rebuttal";
  const isReliability = turn.type === "reliability";
  const from = isRebuttal ? resolveMember(turn.from_role) : undefined;
  const to = isRebuttal ? resolveMember(turn.to_role) : undefined;
  const speaker = isRebuttal ? from : turn.agent ? ROSTER_BY_ID[turn.agent] : resolveMember(turn.role);
  const seatId = speaker?.id ?? "cfo";
  const rowTone = isReliability ? "neutral" : turn.type === "influence" ? "info" : AGENT_TONE[seatId] ?? "neutral";
  const accent = toneClasses(rowTone);
  const body = turn.argument || turn.point || "";
  const stance = turn.stance ? toneClasses(stanceTone(String(turn.stance))) : null;
  const evidence = Array.isArray(turn.evidence) ? turn.evidence : [];
  const isDecision = turn.type === "decision";
  const motionKind: TranscriptMotionKind = isDecision
    ? "decision"
    : isRebuttal
      ? "rebuttal"
      : isReliability
        ? "reliability"
        : "default";
  const motionProps = reduced
    ? reducedFadeProps()
    : {
        variants: transcriptEntrance(motionKind, seatId),
        initial: "hidden",
        animate: "show",
        exit: { opacity: 0, y: -6, scale: 0.985, transition: { duration: motionDuration.fast } },
        transition: { ...springSoft, delay: staggerDelay(index, 0.024, 0.14) },
      };
  const rulingDecision = recommendation?.decision ?? (turn.headline?.split("·")[0]?.trim() || "");
  const rulingConfidence = recommendation?.confidence ?? turn.confidence;
  const forecastNotes = [
    ...(turn.forecast_assumptions ?? []),
    ...(turn.scenario_sensitivities ?? []),
    ...(turn.plan_vs_actual_deltas ?? []),
  ];
  const controlNotes = [
    ...(turn.control_findings ?? []),
    ...(turn.missing_evidence_requests ?? []),
    ...(turn.approval_or_policy_blockers ?? []),
  ];
  const negotiationLevers = turn.negotiation_levers ?? [];
  const challengeLabel =
    turn.challenge_label ?? (turn.challenge_type ? CHALLENGE_TYPE_LABEL[String(turn.challenge_type)] ?? String(turn.challenge_type) : undefined);
  const challengeFindings = turn.challenge_findings ?? [];

  if (isDecision && rulingDecision) {
    return (
      <motion.li
        layout="position"
        {...motionProps}
        style={transcriptAccentStyle("cfo")}
        data-transcript-turn-type="decision"
        className="transcript-turn-row transcript-turn-row--decision transcript-decision-shell relative min-h-[180px] min-w-0 overflow-hidden rounded-lg"
      >
        {!reduced && <span aria-hidden="true" className="transcript-decision-flare" />}
        <CfoRulingCard
          decision={rulingDecision}
          confidence={rulingConfidence}
          ruling={recommendation?.ruling ?? turn.ruling}
          rationale={recommendation?.rationale ?? turn.rationale ?? body}
          tradeoffs={recommendation?.tradeoffs ?? turn.tradeoffs}
          analystInfluence={recommendation?.analyst_influence ?? turn.analyst_influence}
          conditions={recommendation?.conditions ?? turn.conditions}
          dissent={recommendation?.dissent ?? turn.dissent}
          runwayImpactSummary={recommendation?.runway_impact_summary ?? turn.runway_impact_summary}
          keyPoints={turn.key_points}
          variant="transcript"
        />
      </motion.li>
    );
  }

  return (
    <motion.li
      layout="position"
      {...motionProps}
      style={transcriptAccentStyle(seatId)}
      data-transcript-turn-type={turn.type}
      data-transcript-agent={seatId}
      className={cx(
        "transcript-turn-row min-h-[104px] min-w-0 rounded-md border border-border bg-background p-2.5",
        "border-l-2",
        accent.ring,
        isRebuttal && "transcript-turn-row--rebuttal bg-surface/60",
        isReliability && "transcript-turn-row--reliability border-border-strong bg-surface-muted/35",
        turn.type === "influence" && "border-info/35 bg-info-bg/15",
      )}
    >
      <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
        <div className="flex min-w-0 items-center gap-2">
          <span className={cx("grid h-6 w-6 shrink-0 place-items-center rounded-md border border-border bg-surface", accent.text)}>
            <RoleGlyph icon={agentIcon(seatId)} className="h-3.5 w-3.5" />
          </span>
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="truncate text-[12px] font-semibold">{speaker?.label ?? turn.label ?? "Council"}</span>
            {isRebuttal && to && (
              <>
                <CornerDownRight className="h-3 w-3 shrink-0 text-subtle-foreground" strokeWidth={2} />
                <span className="truncate text-[12px] font-medium text-muted-foreground">{to.label}</span>
              </>
            )}
          </span>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-1.5 sm:justify-end">
          {isRebuttal && challengeLabel && (
            <span className="max-w-full rounded-full border border-info/25 bg-info-bg px-1.5 py-0.5 text-[10px] font-semibold text-info">
              {challengeLabel}
            </span>
          )}
          {turn.stance && stance && (
            <span className={cx("rounded-full px-1.5 py-0.5 text-[10px] font-semibold", stance.soft)}>
              {String(turn.stance).toUpperCase()}
            </span>
          )}
          <span className="rounded-full border border-border bg-surface px-1.5 py-0.5 text-[10px] font-semibold uppercase text-subtle-foreground">
            {TYPE_LABEL[turn.type] ?? turn.type}
          </span>
          {turn.at && <span className="hidden tabular-nums text-[10px] text-subtle-foreground sm:block">{turn.at}</span>}
        </div>
      </div>

      {turn.headline && turn.type !== "rebuttal" && (
        <p className="mt-2 break-words text-[13px] font-semibold leading-snug">{turn.headline}</p>
      )}
      {turn.role_specific_lens && (
        <p className="mt-1.5 break-words rounded border border-border bg-surface px-2 py-1 text-[10px] font-semibold uppercase text-subtle-foreground">
          {turn.role_specific_lens}
        </p>
      )}
      {turn.challenge_lens && (
        <p className="mt-1.5 break-words rounded border border-info/20 bg-info-bg/20 px-2 py-1 text-[10px] font-semibold uppercase text-info">
          {turn.challenge_lens}
        </p>
      )}
      {body && <p className="mt-1.5 break-words text-[12px] leading-relaxed text-muted-foreground">{body}</p>}

      {challengeFindings.length > 0 && (
        <div className="mt-2 grid gap-1.5">
          {challengeFindings.slice(0, 4).map((finding) => (
            <div key={`${finding.role}-${finding.challenge_type ?? finding.challenge}`} className="rounded border border-border bg-surface px-2 py-1.5">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[10px] font-semibold uppercase text-subtle-foreground">{finding.role}</span>
                {(finding.challenge_label || finding.challenge_type) && (
                  <span className="shrink-0 rounded border border-info/20 bg-info-bg px-1.5 py-0.5 text-[10px] font-semibold text-info">
                    {finding.challenge_label ?? CHALLENGE_TYPE_LABEL[String(finding.challenge_type)] ?? finding.challenge_type}
                  </span>
                )}
              </div>
              {finding.challenge_lens && (
                <div className="mt-1 break-words text-[10px] font-medium text-muted-foreground">{finding.challenge_lens}</div>
              )}
              {finding.challenge && (
                <div className="mt-1 break-words text-[11px] leading-relaxed text-foreground">{finding.challenge}</div>
              )}
            </div>
          ))}
        </div>
      )}

      {turn.key_points && turn.key_points.length > 0 && (
        <ul className="mt-2 grid gap-1">
          {turn.key_points.map((point) => (
            <li key={point} className="flex gap-1.5 text-[11px] leading-relaxed text-muted-foreground">
              <span className={cx("mt-1.5 h-1 w-1 shrink-0 rounded-full", accent.dot)} />
              <span className="break-words">{point}</span>
            </li>
          ))}
        </ul>
      )}

      {forecastNotes.length > 0 && (
        <div className="mt-2 rounded border border-warning/20 bg-warning-bg/20 px-2 py-1.5">
          <div className="text-[10px] font-semibold uppercase text-warning">Forecast check</div>
          <ul className="mt-1 grid gap-1">
            {forecastNotes.slice(0, 4).map((note) => (
              <li key={note} className="break-words text-[11px] leading-relaxed text-muted-foreground">
                {note}
              </li>
            ))}
          </ul>
        </div>
      )}

      {controlNotes.length > 0 && (
        <div className="mt-2 rounded border border-risk/20 bg-risk-bg/20 px-2 py-1.5">
          <div className="text-[10px] font-semibold uppercase text-risk">Controls check</div>
          <ul className="mt-1 grid gap-1">
            {controlNotes.slice(0, 4).map((note) => (
              <li key={note} className="break-words text-[11px] leading-relaxed text-muted-foreground">
                {note}
              </li>
            ))}
          </ul>
        </div>
      )}

      {negotiationLevers.length > 0 && (
        <div className="mt-2 rounded border border-positive/20 bg-positive-bg/20 px-2 py-1.5">
          <div className="text-[10px] font-semibold uppercase text-positive">Negotiation levers</div>
          <ul className="mt-1 grid gap-1">
            {negotiationLevers.slice(0, 4).map((lever) => (
              <li key={lever} className="break-words text-[11px] leading-relaxed text-muted-foreground">
                {lever}
              </li>
            ))}
          </ul>
        </div>
      )}

      {evidence.length > 0 && <EvidenceChips evidence={evidence} />}
    </motion.li>
  );
});

const EvidenceChips = memo(function EvidenceChips({ evidence }: { evidence: EvidenceItem[] }) {
  const prefersReducedMotion = useReducedMotion();
  const reduced = Boolean(prefersReducedMotion);

  return (
    <motion.div
      layout="position"
      className="mt-2 flex flex-wrap gap-1.5"
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduced ? transitionReduced : { duration: motionDuration.normal, ease: EASE_OUT_EXPO }}
      aria-label="Accepted evidence"
    >
      {evidence.slice(0, 6).map((item, index) => (
        <motion.span
          key={item.id ?? `${item.label}-${index}`}
          title={(item.detail ?? item.redis_key ?? "").slice(0, 240) || undefined}
          data-transcript-evidence-chip
          className="transcript-evidence-chip inline-flex max-w-full items-center gap-1 rounded border border-info/20 bg-info-bg px-1.5 py-0.5 text-[10px] font-medium text-info"
          initial={reduced ? false : { opacity: 0, scale: 0.94, y: 3 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          transition={reduced ? transitionReduced : { ...springSnappy, delay: staggerDelay(index, 0.035, 0.16) }}
        >
          <CheckCircle2 className="h-3 w-3 shrink-0" strokeWidth={2.35} />
          <span className="font-semibold">{item.source ?? item.kind ?? "Evidence"}</span>
          {(item.label || item.value != null) && (
            <span className="truncate opacity-80">
              {String(item.label ?? item.value).slice(0, 120)}
            </span>
          )}
        </motion.span>
      ))}
    </motion.div>
  );
});

function RoleGlyph({ icon: Icon, className }: { icon: IconType; className?: string }) {
  return <Icon className={className} strokeWidth={2} />;
}

function SkeletonTurns() {
  return (
    <div className="space-y-2.5">
      {Array.from({ length: 4 }).map((_, index) => (
        <div key={index} className="rounded-md border border-dashed border-info/25 bg-info-bg/20 p-2.5">
          <div className="flex items-center gap-2">
            <div className="h-6 w-6 animate-pulse rounded-md bg-surface-muted" />
            <div className="h-3 w-28 animate-pulse rounded bg-surface-muted" />
          </div>
          <div className="mt-2">
            <SkeletonText lines={2} />
          </div>
        </div>
      ))}
    </div>
  );
}
