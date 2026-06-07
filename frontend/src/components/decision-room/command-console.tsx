"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ComponentType, FormEvent, RefObject } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  ArrowUp,
  CheckCircle2,
  Clock3,
  Loader2,
  Mic,
  MicOff,
  Radio,
  Sparkles,
  Terminal,
  Volume2,
  XCircle,
} from "lucide-react";
import { cx } from "@/components/ui";
import {
  EASE_OUT_EXPO,
  motionDuration,
  pressTap,
  springSnappy,
  staggerDelay,
} from "@/components/motion/variants";
import type { Tone, RealtimeView } from "@/lib/council";
import type { CouncilCommand, VoiceTranscriptEntry } from "@/lib/types";
import { Panel, StatusBadge, Waveform } from "./primitives";

type SubmissionState = "idle" | "pending" | "sent" | "blocked";
type VoicePhase = "idle" | "connecting" | "muted" | "listening" | "processing" | "speaking" | "live" | "blocked";

function voicePhase(realtime: RealtimeView, voiceTranscript: VoiceTranscriptEntry[]): VoicePhase {
  const latestAssistant = [...voiceTranscript].reverse().find((entry) => entry.role === "assistant");
  if (realtime.status === "blocked") return "blocked";
  if (realtime.status === "connecting") return "connecting";
  if (realtime.status === "idle") return "idle";
  if (realtime.micMuted) return "muted";
  if (realtime.listening) return "listening";
  if (realtime.processing) return "processing";
  if (realtime.speaking || (latestAssistant && !latestAssistant.final)) return "speaking";
  return "live";
}

function voiceTone(phase: VoicePhase): Tone {
  switch (phase) {
    case "blocked":
      return "risk";
    case "connecting":
    case "processing":
      return "info";
    case "muted":
      return "warning";
    case "listening":
    case "speaking":
    case "live":
      return "positive";
    default:
      return "neutral";
  }
}

function voiceLabel(phase: VoicePhase): string {
  switch (phase) {
    case "blocked":
      return "Voice blocked";
    case "connecting":
      return "Connecting";
    case "muted":
      return "Mic muted";
    case "listening":
      return "Listening";
    case "processing":
      return "Processing";
    case "speaking":
      return "Speaking";
    case "live":
      return "Voice live";
    default:
      return "Voice idle";
  }
}

function submissionCopy(state: SubmissionState, running: boolean, healthReady: boolean): string {
  if (!healthReady) return "Locked by preflight";
  if (running) return "Council deliberating";
  if (state === "pending") return "Dispatching";
  if (state === "sent") return "Sent to council";
  if (state === "blocked") return "Not sent";
  return "Send to council";
}

function submitTone(state: SubmissionState, running: boolean): Tone {
  if (running || state === "pending") return "info";
  if (state === "sent") return "positive";
  if (state === "blocked") return "risk";
  return "accent";
}

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
  onSubmit: (value: string) => void | Promise<void>;
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
  const prefersReducedMotion = useReducedMotion();
  const reduced = Boolean(prefersReducedMotion);
  const [submissionState, setSubmissionState] = useState<SubmissionState>("idle");
  const [submitMessage, setSubmitMessage] = useState("");
  const transcriptScrollRef = useRef<HTMLDivElement>(null);

  const phase = voicePhase(realtime, voiceTranscript);
  const realtimeTone = voiceTone(phase);
  const quickCommands = Array.isArray(commands) ? commands.filter((command) => command?.label) : [];
  const lastTranscriptKey = voiceTranscript.map((entry) => `${entry.id}:${entry.text}:${entry.final}`).join("|");
  const sendDisabled = running || !healthReady || !input.trim() || submissionState === "pending";
  const voiceConnected = realtime.status === "connected";
  const voiceMuted = voiceConnected && realtime.micMuted === true;
  const voiceButtonLabel = voiceConnected ? (voiceMuted ? "Unmute microphone" : "Mute microphone") : "Start voice";
  const submitButtonTone = submitTone(submissionState, running);
  const submitButtonClass =
    submitButtonTone === "positive"
      ? "border-positive/30 bg-positive text-accent-foreground"
      : submitButtonTone === "risk"
        ? "border-risk/30 bg-risk text-accent-foreground"
        : submitButtonTone === "info"
          ? "border-info/30 bg-info text-accent-foreground"
          : "border-accent bg-accent text-accent-foreground";

  const statusStrip = useMemo(() => {
    if (submissionState === "idle" && !running) return null;
    if (running) return { tone: "info" as Tone, icon: Clock3, text: "Council run is active. New prompts wait until the debate finishes." };
    if (submissionState === "pending") return { tone: "info" as Tone, icon: Loader2, text: "Dispatching your decision to the live council stream." };
    if (submissionState === "sent") return { tone: "positive" as Tone, icon: CheckCircle2, text: submitMessage || "Decision dispatched." };
    if (submissionState === "blocked") return { tone: "risk" as Tone, icon: XCircle, text: submitMessage || "Decision was not sent." };
    return null;
  }, [running, submissionState, submitMessage]);

  useEffect(() => {
    const el = transcriptScrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [lastTranscriptKey]);

  useEffect(() => {
    if (submissionState !== "sent" && submissionState !== "blocked") return;
    const timeout = window.setTimeout(() => {
      setSubmissionState("idle");
      setSubmitMessage("");
    }, 2400);
    return () => window.clearTimeout(timeout);
  }, [submissionState]);

  async function handleSubmit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const content = input.trim();
    if (!content) {
      setSubmissionState("blocked");
      setSubmitMessage("Type a decision before dispatching.");
      return;
    }
    if (!healthReady || running) {
      setSubmissionState("blocked");
      setSubmitMessage(!healthReady ? "Strict-live preflight is not ready." : "The council is already deliberating.");
      return;
    }

    setSubmissionState("pending");
    setSubmitMessage("");
    try {
      await onSubmit(content);
      setSubmissionState("sent");
      setSubmitMessage("Decision dispatched to the council.");
    } catch (err) {
      setSubmissionState("blocked");
      setSubmitMessage(err instanceof Error ? err.message : "Dispatch failed.");
    }
  }

  return (
    <Panel
      className={cx("command-console-shell shrink-0", className)}
      icon={Terminal}
      visualIcon="terminal"
      title="Operator"
      action={
        <StatusBadge tone={realtimeTone} pulse={!reduced && ["connecting", "listening", "processing", "speaking"].includes(phase)}>
          {voiceLabel(phase)}
        </StatusBadge>
      }
    >
      <form onSubmit={handleSubmit} aria-label="Send a decision to the Atlas council">
        <textarea
          value={input}
          onChange={(event) => onInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void handleSubmit();
            }
          }}
          rows={3}
          disabled={running || submissionState === "pending"}
          aria-label="Decision prompt"
          placeholder={
            healthReady
              ? started
                ? "Ask a follow-up or frame a new decision..."
                : 'Frame a decision - e.g. "Should we sign the $240K Datadog renewal?"'
              : "Locked until the system is ready..."
          }
          className="min-h-[72px] w-full resize-none rounded-md border border-border bg-background px-3 py-2.5 text-[13px] leading-relaxed outline-none placeholder:text-subtle-foreground transition-colors focus:border-border-strong disabled:opacity-50"
        />

        <div className="mt-2 flex items-center gap-2">
          <motion.button
            type="submit"
            disabled={sendDisabled}
            data-command-submit-state={running ? "running" : submissionState}
            aria-busy={submissionState === "pending" || running}
            whileTap={reduced || sendDisabled ? undefined : pressTap}
            className={cx(
              "command-send-button inline-flex h-9 flex-1 items-center justify-center gap-1.5 rounded-md border px-3 text-[13px] font-semibold transition-colors disabled:opacity-40",
              submitButtonClass,
              !sendDisabled && "hover:brightness-[0.98]",
              (submissionState === "pending" || running) && "command-send-button--pending",
              submissionState === "sent" && "command-send-button--sent",
              submissionState === "blocked" && "command-send-button--blocked",
            )}
          >
            <AnimatePresence mode="wait" initial={false}>
              <motion.span
                key={running ? "running" : submissionState}
                className="inline-flex items-center gap-1.5"
                initial={reduced ? { opacity: 0 } : { opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduced ? { opacity: 0 } : { opacity: 0, y: -4 }}
                transition={reduced ? { duration: motionDuration.instant } : { duration: motionDuration.fast, ease: EASE_OUT_EXPO }}
              >
                {running || submissionState === "pending" ? (
                  <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.25} />
                ) : submissionState === "sent" ? (
                  <CheckCircle2 className="h-4 w-4" strokeWidth={2.25} />
                ) : submissionState === "blocked" ? (
                  <XCircle className="h-4 w-4" strokeWidth={2.25} />
                ) : (
                  <ArrowUp className="h-4 w-4" strokeWidth={2.25} />
                )}
                {submissionCopy(submissionState, running, healthReady)}
              </motion.span>
            </AnimatePresence>
          </motion.button>
          <motion.button
            type="button"
            onClick={onVoiceButton}
            disabled={!healthReady || realtime.status === "connecting"}
            title={voiceButtonLabel}
            aria-label={voiceButtonLabel}
            aria-pressed={voiceConnected && !voiceMuted}
            data-voice-state={phase}
            whileTap={reduced || !healthReady ? undefined : pressTap}
            className={cx(
              "voice-control-button inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-md border px-3 text-[12px] font-semibold transition-colors disabled:opacity-40",
              phase === "blocked"
                ? "border-risk/25 bg-risk-bg text-risk"
                : phase === "muted"
                  ? "border-warning/30 bg-warning-bg text-warning"
                  : voiceConnected
                    ? "border-positive/30 bg-positive-bg text-positive"
                    : "border-info/20 bg-info-bg text-info hover:bg-info-bg",
              !reduced && ["listening", "speaking"].includes(phase) && "voice-control-button--live",
            )}
          >
            {realtime.status === "connecting" ? (
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.25} />
            ) : voiceConnected && voiceMuted ? (
              <MicOff className="h-4 w-4" strokeWidth={2.25} />
            ) : phase === "speaking" ? (
              <Volume2 className="h-4 w-4" strokeWidth={2.25} />
            ) : (
              <Mic className="h-4 w-4" strokeWidth={2.25} />
            )}
            {voiceConnected ? (voiceMuted ? "Unmute" : "Mute") : "Voice"}
          </motion.button>
        </div>
      </form>

      <AnimatePresence initial={false}>
        {statusStrip && (
          <motion.div
            key={`${statusStrip.tone}-${statusStrip.text}`}
            role="status"
            aria-live="polite"
            className={cx(
              "mt-2 flex items-start gap-2 rounded-md border px-2.5 py-2 text-[11px] leading-relaxed",
              statusStrip.tone === "positive"
                ? "border-positive/20 bg-positive-bg text-positive"
                : statusStrip.tone === "risk"
                  ? "border-risk/20 bg-risk-bg text-risk"
                  : "border-info/20 bg-info-bg text-info",
            )}
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: -4 }}
            transition={reduced ? { duration: motionDuration.instant } : { duration: motionDuration.quick, ease: EASE_OUT_EXPO }}
          >
            <StatusStripIcon icon={statusStrip.icon} spinning={statusStrip.icon === Loader2} />
            <span>{statusStrip.text}</span>
          </motion.div>
        )}
      </AnimatePresence>

      <audio ref={audioRef} autoPlay playsInline className="sr-only" aria-hidden />

      <VoiceStatusPanel realtime={realtime} phase={phase} reduced={reduced} />

      {(voiceConnected || voiceTranscript.length > 0) && (
        <motion.div
          layout="position"
          className="mt-3 border-t border-border pt-2.5"
          initial={reduced ? { opacity: 0 } : { opacity: 0, y: 5 }}
          animate={{ opacity: 1, y: 0 }}
          transition={reduced ? { duration: motionDuration.instant } : springSnappy}
        >
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
              <div className="h-[72px]" aria-hidden />
            ) : (
              <ol className="space-y-2">
                <AnimatePresence initial={false}>
                  {voiceTranscript.map((entry) => (
                    <motion.li
                      key={entry.id}
                      layout="position"
                      className="min-w-0"
                      initial={reduced ? { opacity: 0 } : { opacity: 0, x: entry.role === "user" ? -6 : 6 }}
                      animate={{ opacity: 1, x: 0 }}
                      exit={reduced ? { opacity: 0 } : { opacity: 0, y: -4 }}
                      transition={reduced ? { duration: motionDuration.instant } : { duration: motionDuration.quick, ease: EASE_OUT_EXPO }}
                    >
                      <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-subtle-foreground">
                        <span className={entry.role === "user" ? "text-info" : "text-positive"}>
                          {entry.role === "user" ? "You" : "Atlas voice"}
                        </span>
                        {!entry.final && <span className="normal-case tracking-normal text-warning">streaming...</span>}
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
                    </motion.li>
                  ))}
                </AnimatePresence>
              </ol>
            )}
          </div>
        </motion.div>
      )}

      {quickCommands.length > 0 && (
        <div className="mt-3 border-t border-border pt-2.5">
          <div className="text-[12px] font-semibold text-foreground">Suggestions</div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {quickCommands.slice(0, 6).map((command, index) => (
              <motion.button
                key={command.id ?? `${command.label}-${index}`}
                type="button"
                disabled={running}
                title={command.description}
                onClick={() => onInput(command.prompt ?? command.label)}
                initial={reduced ? { opacity: 0 } : { opacity: 0, scale: 0.96 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={reduced ? { duration: motionDuration.instant } : { ...springSnappy, delay: staggerDelay(index, 0.035, 0.18) }}
                className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-surface px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground disabled:opacity-40"
              >
                <Sparkles className="h-3 w-3 shrink-0" strokeWidth={2} />
                <span className="truncate">{command.label}</span>
              </motion.button>
            ))}
          </div>
        </div>
      )}
    </Panel>
  );
}

function VoiceStatusPanel({ realtime, phase, reduced }: { realtime: RealtimeView; phase: VoicePhase; reduced: boolean }) {
  const connected = realtime.status === "connected";
  const activeWave = ["listening", "processing", "speaking"].includes(phase);
  return (
    <motion.div
      layout="position"
      data-voice-phase={phase}
      className={cx(
        "voice-status-card mt-3 rounded-md border bg-background px-2.5 py-2",
        phase === "blocked"
          ? "border-risk/25"
          : phase === "muted"
            ? "border-warning/25"
            : connected
              ? "border-positive/25"
              : "border-border",
      )}
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: 5 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduced ? { duration: motionDuration.instant } : springSnappy}
    >
      <div className="flex items-center gap-2">
        <span
          className={cx(
            "voice-orb grid h-9 w-9 shrink-0 place-items-center rounded-md border",
            phase === "blocked"
              ? "border-risk/25 bg-risk-bg text-risk"
              : phase === "muted"
                ? "border-warning/25 bg-warning-bg text-warning"
                : connected
                  ? "border-positive/25 bg-positive-bg text-positive"
                  : "border-border bg-surface-muted text-subtle-foreground",
            !reduced && `voice-orb--${phase}`,
          )}
          aria-hidden="true"
        >
          {phase === "speaking" ? <Volume2 className="h-4 w-4" strokeWidth={2.2} /> : <Radio className="h-4 w-4" strokeWidth={2.2} />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <div className="truncate text-[12px] font-semibold">{voiceLabel(phase)}</div>
            <Waveform active={!reduced && activeWave} />
          </div>
        </div>
      </div>
      {(realtime.model || realtime.voice) && (
        <div className="mt-2 flex flex-wrap gap-1">
          {realtime.model && <VoiceMeta label={realtime.model} />}
          {realtime.voice && <VoiceMeta label={realtime.voice} />}
        </div>
      )}
    </motion.div>
  );
}

function VoiceMeta({ label }: { label: string }) {
  return <span className="rounded border border-border bg-surface px-1.5 py-0.5 text-[10px] text-subtle-foreground">{label}</span>;
}

function StatusStripIcon({
  icon: Icon,
  spinning,
}: {
  icon: ComponentType<{ className?: string; strokeWidth?: number }>;
  spinning: boolean;
}) {
  return <Icon className={cx("mt-0.5 h-3.5 w-3.5 shrink-0", spinning && "animate-spin")} strokeWidth={2.25} />;
}
