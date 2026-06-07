"use client";

import { useEffect, useMemo, useRef } from "react";
import { CornerDownRight, Loader2, MessagesSquare, Sparkles } from "lucide-react";
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
import { EmptyState, Panel, SkeletonText, StatusBadge, type IconType } from "./primitives";

const TYPE_LABEL: Record<string, string> = {
  framing: "Framing",
  thinking: "Thinking",
  position: "Position",
  rebuttal: "Cross-exam",
  decision: "Ruling",
  reliability: "Eval",
};

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

  const workers = useMemo(
    () => activeCouncilWorkers(agentStatuses, running, nodeName),
    [agentStatuses, running, nodeName],
  );

  const thinkingTurns = useMemo(() => transcript.filter((turn) => turn.type === "thinking"), [transcript]);
  const spokenTurns = useMemo(() => transcript.filter((turn) => turn.type !== "thinking"), [transcript]);

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
            <EmptyState icon={MessagesSquare}>
              {healthReady
                ? "Submit a decision to open the live council transcript."
                : "Strict preflight must pass before the council can convene."}
            </EmptyState>
          )
        ) : (
          <ol className="space-y-2.5">
            {thinkingTurns.map((turn, index) => (
              <ThinkingRow key={turn.id ?? `thinking-${turn.agent}-${index}`} turn={turn} />
            ))}
            {spokenTurns.map((turn, index) => (
              <TurnRow
                key={turn.id ?? `${turn.type}-${turn.agent ?? turn.from_role}-${index}`}
                turn={turn}
                recommendation={recommendation}
              />
            ))}
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
          <div
            key={worker.id}
            className="flex min-w-0 items-start gap-2 rounded-md border border-info/20 bg-background/90 px-2.5 py-2"
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
          </div>
        ))}
      </div>
    </div>
  );
}

function ThinkingRow({ turn }: { turn: TranscriptTurn }) {
  const speaker = turn.agent ? ROSTER_BY_ID[turn.agent] : resolveMember(turn.role);
  const seatId = speaker?.id ?? turn.agent ?? "cfo";
  const accent = toneClasses(AGENT_TONE[seatId] ?? "info");

  return (
    <li
      className={cx(
        "min-w-0 rounded-md border border-dashed bg-background p-2.5",
        accent.border,
        "border-l-2",
        accent.ring,
      )}
    >
      <div className="flex min-w-0 items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className={cx("grid h-6 w-6 shrink-0 place-items-center rounded-md border border-border bg-surface", accent.text)}>
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2.25} />
          </span>
          <span className="truncate text-[12px] font-semibold">{speaker?.label ?? turn.label ?? "Council"}</span>
        </div>
        <StatusBadge tone="info" pulse>
          Thinking
        </StatusBadge>
      </div>
      {turn.argument && (
        <p className="mt-2 break-words text-[12px] leading-relaxed text-muted-foreground">{turn.argument}</p>
      )}
    </li>
  );
}

function TurnRow({ turn, recommendation }: { turn: TranscriptTurn; recommendation?: DebateState["recommendation"] }) {
  const isRebuttal = turn.type === "rebuttal";
  const from = isRebuttal ? resolveMember(turn.from_role) : undefined;
  const to = isRebuttal ? resolveMember(turn.to_role) : undefined;
  const speaker = isRebuttal ? from : turn.agent ? ROSTER_BY_ID[turn.agent] : resolveMember(turn.role);
  const seatId = speaker?.id ?? "cfo";
  const accent = toneClasses(AGENT_TONE[seatId] ?? "neutral");
  const body = turn.argument || turn.point || "";
  const stance = turn.stance ? toneClasses(stanceTone(String(turn.stance))) : null;
  const evidence = Array.isArray(turn.evidence) ? turn.evidence : [];

  return (
    <li className={cx("min-w-0 rounded-md border border-border bg-background p-2.5", "border-l-2", accent.ring)}>
      <div className="flex min-w-0 items-center justify-between gap-2">
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
        <div className="flex shrink-0 items-center gap-1.5">
          {turn.stance && stance && (
            <span className={cx("rounded-full px-1.5 py-0.5 text-[10px] font-semibold", stance.soft)}>
              {String(turn.stance).toUpperCase()}
            </span>
          )}
          <span className="rounded-full border border-border bg-surface px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-subtle-foreground">
            {TYPE_LABEL[turn.type] ?? turn.type}
          </span>
          {turn.at && <span className="hidden tabular-nums text-[10px] text-subtle-foreground sm:block">{turn.at}</span>}
        </div>
      </div>

      {turn.headline && turn.type !== "rebuttal" && (
        <p className="mt-2 break-words text-[13px] font-semibold leading-snug">{turn.headline}</p>
      )}
      {body && <p className="mt-1.5 break-words text-[12px] leading-relaxed text-muted-foreground">{body}</p>}

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

      {turn.type === "decision" && recommendation?.decision && (
        <div className="mt-2 text-[11px] font-semibold text-positive">
          Recorded: {recommendation.decision} · {recommendation.confidence ?? "--"}% confidence
        </div>
      )}

      {evidence.length > 0 && <EvidenceChips evidence={evidence} />}
    </li>
  );
}

function EvidenceChips({ evidence }: { evidence: EvidenceItem[] }) {
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {evidence.slice(0, 6).map((item, index) => (
        <span
          key={item.id ?? `${item.label}-${index}`}
          title={item.detail ?? item.redis_key ?? undefined}
          className="inline-flex max-w-full items-center gap-1 rounded border border-info/20 bg-info-bg px-1.5 py-0.5 text-[10px] font-medium text-info"
        >
          <span className="font-semibold">{item.source ?? item.kind ?? "Evidence"}</span>
          {(item.label || item.value != null) && (
            <span className="truncate opacity-80">{item.label ?? String(item.value)}</span>
          )}
        </span>
      ))}
    </div>
  );
}

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
