"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CornerDownRight, Loader2, MessagesSquare } from "lucide-react";
import { cx } from "@/components/ui";
import { AGENT_TONE, resolveMember, ROSTER_BY_ID } from "@/lib/agents";
import { NODE_LABEL, NODE_TO_AGENT, stanceTone, toneClasses } from "@/lib/council";
import type { DebateState, EvidenceItem, TranscriptTurn } from "@/lib/types";
import { agentIcon } from "./agent-visuals";
import { EmptyState, Panel, SkeletonText, StatusBadge, type IconType } from "./primitives";

const TYPE_LABEL: Record<string, string> = {
  framing: "Framing",
  position: "Position",
  rebuttal: "Cross-exam",
  decision: "Ruling",
  reliability: "Eval",
};

type FilterId = "all" | "debate" | "cfo" | "treasury" | "fpna" | "risk" | "procurement" | "reliability";

function turnMatchesAgent(turn: TranscriptTurn, agentId: string): boolean {
  if (turn.type === "rebuttal") {
    return resolveMember(turn.from_role)?.id === agentId || resolveMember(turn.to_role)?.id === agentId;
  }
  if (agentId === "cfo") return turn.agent === "cfo" || turn.type === "framing" || turn.type === "decision";
  return turn.agent === agentId;
}

export function TranscriptStream({
  transcript,
  recommendation,
  running,
  nodeName,
  healthReady,
  started,
}: {
  transcript: TranscriptTurn[];
  recommendation?: DebateState["recommendation"];
  running: boolean;
  nodeName?: string;
  healthReady: boolean;
  started: boolean;
}) {
  const [filter, setFilter] = useState<FilterId>("all");
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  const counts = useMemo(() => {
    const base: Record<string, number> = { all: transcript.length, debate: 0 };
    for (const id of ["cfo", "treasury", "fpna", "risk", "procurement", "reliability"]) base[id] = 0;
    for (const turn of transcript) {
      if (turn.type === "rebuttal") base.debate += 1;
      for (const id of ["cfo", "treasury", "fpna", "risk", "procurement", "reliability"]) {
        if (turnMatchesAgent(turn, id)) base[id] += 1;
      }
    }
    return base;
  }, [transcript]);

  const filtered = useMemo(() => {
    if (filter === "all") return transcript;
    if (filter === "debate") return transcript.filter((turn) => turn.type === "rebuttal");
    return transcript.filter((turn) => turnMatchesAgent(turn, filter));
  }, [transcript, filter]);

  useEffect(() => {
    if (pinned.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [transcript.length, filter]);

  const activeAgentId = running ? NODE_TO_AGENT[nodeName ?? ""] : undefined;
  const activeMember = activeAgentId ? ROSTER_BY_ID[activeAgentId] : undefined;

  const filterChips: { id: FilterId; label: string }[] = [
    { id: "all", label: "All" },
    { id: "cfo", label: "CFO" },
    { id: "treasury", label: "Treasury" },
    { id: "fpna", label: "FP&A" },
    { id: "risk", label: "Risk" },
    { id: "procurement", label: "Procurement" },
    { id: "debate", label: "Cross-exam" },
    { id: "reliability", label: "Eval" },
  ];

  return (
    <Panel
      id="council-transcript"
      icon={MessagesSquare}
      eyebrow="Live debate"
      title="Transcript stream"
      count={transcript.length}
      action={
        running ? (
          <StatusBadge tone="info" pulse>
            Streaming
          </StatusBadge>
        ) : undefined
      }
      bodyClassName="p-0"
    >
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-3 py-2">
        {filterChips.map((chip) => {
          const count = counts[chip.id] ?? 0;
          const isActive = filter === chip.id;
          const disabled = chip.id !== "all" && count === 0;
          return (
            <button
              key={chip.id}
              type="button"
              disabled={disabled}
              onClick={() => setFilter(chip.id)}
              className={cx(
                "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold transition-colors disabled:opacity-35",
                isActive
                  ? "border-accent bg-accent text-accent-foreground"
                  : "border-border bg-surface text-muted-foreground hover:bg-surface-muted hover:text-foreground",
              )}
            >
              {chip.label}
              {chip.id !== "all" && count > 0 && (
                <span className={cx("tabular-nums", isActive ? "opacity-80" : "text-subtle-foreground")}>{count}</span>
              )}
            </button>
          );
        })}
      </div>

      <div ref={scrollRef} onScroll={(event) => {
        const el = event.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
      }} className="room-scroll max-h-[560px] min-h-[260px] overflow-y-auto px-3 py-3">
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
        ) : filtered.length === 0 ? (
          <EmptyState icon={MessagesSquare}>No turns from this seat yet.</EmptyState>
        ) : (
          <ol className="space-y-2.5">
            {filtered.map((turn, index) => (
              <TurnRow key={turn.id ?? `${turn.type}-${turn.agent ?? turn.from_role}-${index}`} turn={turn} recommendation={recommendation} />
            ))}
          </ol>
        )}

        {running && activeMember && (
          <div className="mt-2.5 flex items-center gap-2 rounded-md border border-info/20 bg-info-bg/40 px-3 py-2 text-[12px] text-info">
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2.25} />
            <span className="truncate font-semibold">{NODE_LABEL[nodeName ?? ""] ?? `${activeMember.label} is working`}</span>
          </div>
        )}
      </div>
    </Panel>
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

// Optional per-turn grounding chips (only render if a worker attached evidence).
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

// Module-scope so the icon isn't treated as a component "created during render".
function RoleGlyph({ icon: Icon, className }: { icon: IconType; className?: string }) {
  return <Icon className={className} strokeWidth={2} />;
}

function SkeletonTurns() {
  return (
    <div className="space-y-2.5">
      {Array.from({ length: 3 }).map((_, index) => (
        <div key={index} className="rounded-md border border-border bg-background p-2.5">
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
