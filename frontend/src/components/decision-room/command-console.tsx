"use client";

import { useEffect, useRef } from "react";
import type { RefObject } from "react";
import { ArrowUp, Loader2, Mic, MicOff, Sparkles, Terminal } from "lucide-react";
import { cx } from "@/components/ui";
import type { RealtimeView } from "@/lib/council";
import type { CouncilCommand, VoiceTranscriptEntry } from "@/lib/types";
import { Panel, StatusBadge } from "./primitives";

export function CommandConsole({
  input,
  onInput,
  onSubmit,
  running,
  healthReady,
  started,
  realtime,
  onVoiceButton,
  voiceTranscript,
  commands,
  className,
  audioRef,
}: {
  input: string;
  onInput: (value: string) => void;
  onSubmit: (value: string) => void;
  running: boolean;
  healthReady: boolean;
  started: boolean;
  realtime: RealtimeView;
  onVoiceButton: () => void;
  voiceTranscript: VoiceTranscriptEntry[];
  commands?: CouncilCommand[];
  className?: string;
  audioRef?: RefObject<HTMLAudioElement | null>;
}) {
  const sendDisabled = running || !healthReady || !input.trim();
  const voiceConnected = realtime.status === "connected";
  const voiceMuted = voiceConnected && realtime.micMuted === true;
  const realtimeTone =
    realtime.status === "connected"
      ? voiceMuted
        ? "warning"
        : "positive"
      : realtime.status === "connecting"
        ? "info"
        : realtime.status === "blocked"
          ? "risk"
          : "neutral";
  const quickCommands = Array.isArray(commands) ? commands.filter((command) => command?.label) : [];
  const transcriptScrollRef = useRef<HTMLDivElement>(null);
  const lastTranscriptKey = voiceTranscript.map((entry) => `${entry.id}:${entry.text}:${entry.final}`).join("|");

  useEffect(() => {
    const el = transcriptScrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [lastTranscriptKey]);

  return (
    <Panel
      className={className}
      icon={Terminal}
      visualIcon="terminal"
      title="Operator"
      action={
        <StatusBadge tone={realtimeTone} pulse={realtime.status === "connecting" || realtime.listening === true}>
          {realtime.status === "connected"
            ? voiceMuted
              ? "Voice muted"
              : realtime.listening
                ? "Listening"
                : "Voice live"
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
                ? "Ask a follow-up or frame a new decision…"
                : "Frame a decision — e.g. “Should we sign the $240K Datadog renewal?”"
              : "Locked until the system is ready…"
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
            onClick={onVoiceButton}
            disabled={!healthReady || realtime.status === "connecting"}
            title={
              voiceConnected
                ? voiceMuted
                  ? "Unmute microphone"
                  : "Mute microphone"
                : "Start voice"
            }
            className={cx(
              "inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-md border px-3 text-[12px] font-semibold transition-colors disabled:opacity-40",
              voiceConnected
                ? voiceMuted
                  ? "border-warning/30 bg-warning-bg text-warning"
                  : "border-positive/30 bg-positive-bg text-positive"
                : "border-info/20 bg-info-bg text-info hover:bg-info-bg",
            )}
          >
            {realtime.status === "connecting" ? (
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.25} />
            ) : voiceConnected && voiceMuted ? (
              <MicOff className="h-4 w-4" strokeWidth={2.25} />
            ) : (
              <Mic className="h-4 w-4" strokeWidth={2.25} />
            )}
            {voiceConnected ? (voiceMuted ? "Unmute" : "Mute") : "Voice"}
          </button>
        </div>
      </form>

      <audio ref={audioRef} autoPlay playsInline className="sr-only" aria-hidden />

      {realtime.detail && realtime.status !== "idle" && (
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">{realtime.detail}</p>
      )}

      {(voiceConnected || voiceTranscript.length > 0) && (
        <div className="mt-3 border-t border-border pt-2.5">
          <div className="flex items-center justify-between gap-2">
            <div className="text-[12px] font-semibold text-foreground">Voice transcript</div>
            {voiceTranscript.length > 0 && (
              <span className="text-[10px] tabular-nums text-subtle-foreground">{voiceTranscript.length} turns</span>
            )}
          </div>
          <div
            ref={transcriptScrollRef}
            className="room-scroll mt-1.5 max-h-[220px] min-h-[88px] overflow-y-auto rounded-md border border-border bg-background px-2.5 py-2"
          >
            {voiceTranscript.length === 0 ? (
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                Spoken turns appear here and stay for the session.
              </p>
            ) : (
              <ol className="space-y-2">
                {voiceTranscript.map((entry) => (
                  <li key={entry.id} className="min-w-0">
                    <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-subtle-foreground">
                      <span className={entry.role === "user" ? "text-info" : "text-positive"}>
                        {entry.role === "user" ? "You" : "Atlas voice"}
                      </span>
                      {!entry.final && <span className="normal-case tracking-normal text-warning">typing…</span>}
                      <span className="ml-auto tabular-nums font-medium normal-case tracking-normal">{entry.at}</span>
                    </div>
                    <p
                      className={cx(
                        "mt-0.5 break-words text-[12px] leading-relaxed",
                        entry.final ? "text-foreground" : "text-muted-foreground italic",
                      )}
                    >
                      {entry.text}
                    </p>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </div>
      )}

      {quickCommands.length > 0 && (
        <div className="mt-3 border-t border-border pt-2.5">
          <div className="text-[12px] font-semibold text-foreground">Suggestions</div>
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
                <Sparkles className="h-3 w-3 shrink-0" strokeWidth={2} />
                <span className="truncate">{command.label}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </Panel>
  );
}
