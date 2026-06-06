"use client";

import { ArrowUp, Loader2, Mic, MicOff, Sparkles, Terminal } from "lucide-react";
import { cx } from "@/components/ui";
import { toneClasses, type RealtimeView } from "@/lib/council";
import type { CouncilCommand } from "@/lib/types";
import { Panel, SectionLabel, StatusBadge } from "./primitives";

export function CommandConsole({
  input,
  onInput,
  onSubmit,
  running,
  healthReady,
  started,
  realtime,
  onStartRealtime,
  onStopRealtime,
  commands,
}: {
  input: string;
  onInput: (value: string) => void;
  onSubmit: (value: string) => void;
  running: boolean;
  healthReady: boolean;
  started: boolean;
  realtime: RealtimeView;
  onStartRealtime: () => void;
  onStopRealtime: () => void;
  commands?: CouncilCommand[];
}) {
  const sendDisabled = running || !healthReady || !input.trim();
  const realtimeTone =
    realtime.status === "connected"
      ? "positive"
      : realtime.status === "connecting"
        ? "info"
        : realtime.status === "blocked"
          ? "risk"
          : "neutral";
  const quickCommands = Array.isArray(commands) ? commands.filter((command) => command?.label) : [];

  return (
    <Panel
      icon={Terminal}
      eyebrow="Operator"
      title="Decision command"
      action={
        <StatusBadge tone={realtimeTone} pulse={realtime.status === "connecting"}>
          {realtime.status === "connected"
            ? "Voice live"
            : realtime.status === "connecting"
              ? "Connecting"
              : realtime.status === "blocked"
                ? "Voice blocked"
                : "Voice idle"}
        </StatusBadge>
      }
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(input);
        }}
      >
        <textarea
          value={input}
          onChange={(event) => onInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSubmit(input);
            }
          }}
          rows={3}
          disabled={running}
          placeholder={
            healthReady
              ? started
                ? "Ask the council a follow-up, or frame a new decision…"
                : "Frame a decision for the council — e.g. “Should we sign the $240K Datadog renewal?”"
              : "Locked until strict live preflight passes…"
          }
          className="min-h-[72px] w-full resize-none rounded-md border border-border bg-background px-3 py-2.5 text-[13px] leading-relaxed outline-none placeholder:text-subtle-foreground focus:border-border-strong disabled:opacity-50"
        />

        <div className="mt-2 flex items-center gap-2">
          <button
            type="submit"
            disabled={sendDisabled}
            className="inline-flex h-9 flex-1 items-center justify-center gap-1.5 rounded-md bg-accent px-3 text-[13px] font-semibold text-accent-foreground transition-opacity disabled:opacity-40"
          >
            {running ? <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.25} /> : <ArrowUp className="h-4 w-4" strokeWidth={2.25} />}
            {running ? "Council deliberating" : "Send to council"}
          </button>
          <button
            type="button"
            onClick={realtime.status === "connected" ? onStopRealtime : onStartRealtime}
            disabled={!healthReady || realtime.status === "connecting"}
            title="OpenAI Realtime 2 voice"
            className={cx(
              "inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-md border px-3 text-[12px] font-semibold transition-colors disabled:opacity-40",
              realtime.status === "connected"
                ? "border-positive/30 bg-positive-bg text-positive"
                : "border-info/20 bg-info-bg text-info hover:bg-info-bg",
            )}
          >
            {realtime.status === "connecting" ? (
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.25} />
            ) : realtime.status === "connected" ? (
              <MicOff className="h-4 w-4" strokeWidth={2.25} />
            ) : (
              <Mic className="h-4 w-4" strokeWidth={2.25} />
            )}
            Realtime 2
          </button>
        </div>

        <div className="mt-1.5 flex items-start justify-between gap-2">
          <p className={cx("line-clamp-2 min-w-0 text-[11px] leading-relaxed", toneClasses(realtimeTone).text)}>{realtime.detail}</p>
          {realtime.model && (
            <span className="shrink-0 text-[10px] text-subtle-foreground">{realtime.voice ? `${realtime.model} · ${realtime.voice}` : realtime.model}</span>
          )}
        </div>
      </form>

      {quickCommands.length > 0 && (
        <div className="mt-3 border-t border-border pt-2.5">
          <SectionLabel>
            <span className="inline-flex items-center gap-1.5">
              <Sparkles className="h-3.5 w-3.5" strokeWidth={2} />
              Suggested commands
            </span>
          </SectionLabel>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {quickCommands.slice(0, 6).map((command, index) => (
              <button
                key={command.id ?? `${command.label}-${index}`}
                type="button"
                disabled={running}
                title={command.description}
                onClick={() => onInput(command.prompt ?? command.label)}
                className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-surface px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground disabled:opacity-40"
              >
                <span className="truncate">{command.label}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </Panel>
  );
}
