"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCoAgent, useCopilotAction, useCopilotChat } from "@copilotkit/react-core";
import { MessageRole, TextMessage } from "@copilotkit/runtime-client-gql";
import { api } from "@/lib/api";
import { ROSTER_BY_ID } from "@/lib/agents";
import {
  buildTimeline,
  getCurrentPhaseLabel,
  getSponsorRows,
  latestSpeakerId,
  NODE_TO_AGENT,
  type HealthPayload,
  type HealthView,
  type RealtimeView,
} from "@/lib/council";
import type {
  CommandResult,
  DebateState,
  OperatorCommand,
} from "@/lib/types";
import { CouncilWeb } from "@/components/decision-room/council-web";
import { BoardMemo, ScenarioImpactCard } from "@/components/decision-room/board-memo";
import { CommandConsole } from "@/components/decision-room/command-console";
import { CouncilHeader, CouncilStatusBar, PreflightPanel } from "@/components/decision-room/council-chrome";
import { TranscriptStream } from "@/components/decision-room/transcript-stream";

import { agentBase } from "@/lib/agent-base";
import { connectRealtimeVoice, type RealtimeVoiceHandle } from "@/lib/realtime-voice";
import { useDeferredHealthReady, useMounted } from "@/lib/use-mounted";

export default function DecisionsPage() {
  const [input, setInput] = useState("");
  const [health, setHealth] = useState<HealthView>({ status: "loading", refreshing: true });
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [nowLabel, setNowLabel] = useState("");
  const [realtime, setRealtime] = useState<RealtimeView>({ status: "idle", detail: "Realtime 2 voice idle" });

  const realtimeAudioRef = useRef<HTMLAudioElement | null>(null);
  const realtimeVoiceRef = useRef<RealtimeVoiceHandle | null>(null);

  const { state, setState, running, nodeName } = useCoAgent<DebateState>({ name: "finance_department" });
  const { appendMessage } = useCopilotChat();

  // Defensive reads — every field is optional and may arrive incrementally.
  const transcript = useMemo(() => state?.transcript ?? [], [state?.transcript]);
  const agentStatuses = state?.agent_statuses ?? [];
  const recommendation = state?.recommendation;
  const reliabilityScores = state?.reliability_scores ?? [];
  const learningReport = state?.learning_report;
  const commands = state?.commands;
  const decision = state?.decision;
  const companyName = state?.context?.financials?.name ?? "the company";

  const mounted = useMounted();
  const started = transcript.length > 0 || running;
  const healthReady = health.status === "ready" && health.data?.ready === true;
  const displayHealthReady = useDeferredHealthReady(healthReady);
  const displayTranscript = useMemo(() => (mounted ? transcript : []), [mounted, transcript]);
  const displayRunning = mounted && running;
  const displayNodeName = mounted ? nodeName : undefined;
  const displayAgentStatuses = mounted ? agentStatuses : [];
  const displayRecommendation = mounted ? recommendation : undefined;
  const displayDecision = mounted ? decision : undefined;
  const displayStarted = mounted && started;
  const displayPhase = mounted ? state?.phase : undefined;

  const currentPhase = getCurrentPhaseLabel({
    health,
    healthReady: displayHealthReady,
    nodeName: displayNodeName,
    phase: displayPhase,
    recommendation: displayRecommendation,
    running: displayRunning,
  });

  const timeline = useMemo(
    () =>
      buildTimeline({
        health,
        healthReady: displayHealthReady,
        nodeName: displayNodeName,
        phase: displayPhase,
        recommendation: displayRecommendation,
        running: displayRunning,
        transcript: displayTranscript,
      }),
    [health, displayHealthReady, displayNodeName, displayPhase, displayRecommendation, displayRunning, displayTranscript],
  );

  const sponsorRows = useMemo(() => getSponsorRows(health), [health]);

  // Resolve the inspected seat: explicit selection → active roster seat → last speaker → CFO.
  const activeAgentId = NODE_TO_AGENT[nodeName ?? ""];
  const activeRosterId = activeAgentId && ROSTER_BY_ID[activeAgentId] ? activeAgentId : undefined;
  const candidateId = selectedAgentId ?? (running ? activeRosterId : undefined) ?? latestSpeakerId(transcript) ?? "cfo";
  const selectedMember = ROSTER_BY_ID[candidateId] ?? ROSTER_BY_ID.cfo;

  // ----------------------------------------------------------------------- //
  // Health polling (every 15s) — locks submissions until strict-live green.
  // ----------------------------------------------------------------------- //
  const loadHealth = useCallback(async () => {
    const agentBaseUrl = agentBase();
    setHealth((prev) => ({
      ...prev,
      status: prev.data || prev.error ? prev.status : "loading",
      refreshing: true,
    }));

    try {
      const res = await fetch(`${agentBaseUrl}/api/health`, { cache: "no-store" });
      const data = (await res.json().catch(() => null)) as HealthPayload | null;
      if (!data) throw new Error(`/api/health -> ${res.status}`);
      setHealth({ status: data.ready ? "ready" : "blocked", data, refreshing: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setHealth({
        status: "unavailable",
        error: message,
        refreshing: false,
        data: {
          ready: false,
          mode: "strict-live",
          blockers: [`Health endpoint unavailable at ${agentBaseUrl}/api/health`, message],
        },
      });
    }
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(loadHealth, 0);
    const interval = window.setInterval(loadHealth, 15000);
    return () => {
      window.clearTimeout(timeout);
      window.clearInterval(interval);
    };
  }, [loadHealth]);

  useEffect(() => {
    const update = () => {
      setNowLabel(
        new Intl.DateTimeFormat("en-US", {
          hour: "numeric",
          minute: "2-digit",
          second: "2-digit",
          timeZoneName: "short",
        }).format(new Date()),
      );
    };
    update();
    const interval = window.setInterval(update, 1000);
    return () => window.clearInterval(interval);
  }, []);

  // ----------------------------------------------------------------------- //
  // OpenAI Realtime 2 voice (WebRTC) — gated behind strict-live preflight.
  // ----------------------------------------------------------------------- //
  const stopRealtime = useCallback(() => {
    realtimeVoiceRef.current?.stop();
    realtimeVoiceRef.current = null;
    if (realtimeAudioRef.current) realtimeAudioRef.current.srcObject = null;
    setRealtime((prev) => ({
      status: "idle",
      detail: prev.status === "connected" ? "Realtime voice disconnected" : prev.detail,
      model: prev.model,
      voice: prev.voice,
    }));
  }, []);

  useEffect(() => () => stopRealtime(), [stopRealtime]);

  const startRealtime = useCallback(async () => {
    if (!healthReady) {
      setRealtime({ status: "blocked", detail: "Strict live preflight must pass before voice starts." });
      return;
    }
    const audioEl = realtimeAudioRef.current;
    if (!audioEl) {
      setRealtime({ status: "blocked", detail: "Voice audio element not ready — refresh and try again." });
      return;
    }

    stopRealtime();

    try {
      realtimeVoiceRef.current = await connectRealtimeVoice({
        agentBase: agentBase(),
        audioEl,
        callbacks: {
          onStatus: setRealtime,
          onSubmitDecision: async (decision) => {
            if (running || !healthReady) return;
            setInput(decision);
            await appendMessage(new TextMessage({ role: MessageRole.User, content: decision }));
          },
        },
      });
    } catch (err) {
      stopRealtime();
      setRealtime({ status: "blocked", detail: err instanceof Error ? err.message : String(err) });
    }
  }, [appendMessage, healthReady, running, stopRealtime]);

  // ----------------------------------------------------------------------- //
  // Submission — strict-gated, streams via CopilotKit / AG-UI.
  // ----------------------------------------------------------------------- //
  const submit = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || running || !healthReady) return;
      setInput("");
      await appendMessage(new TextMessage({ role: MessageRole.User, content }));
    },
    [appendMessage, healthReady, running],
  );

  // ----------------------------------------------------------------------- //
  // AG-UI command-and-control — operator steering of the live council.
  // Commands execute server-side (council_commands.dispatch_command); the
  // authoritative result is mirrored into the shared coagent state so the panel
  // updates immediately, and the same eight keys stream back through DebateState
  // while a debate is running.
  // ----------------------------------------------------------------------- //
  const dispatchCommand = useCallback(
    async (command: OperatorCommand): Promise<CommandResult | undefined> => {
      try {
        const result = await api.command(command);
        if (result?.state) {
          // Mirror the server-authoritative command state into the shared agent
          // state (never fabricated — this is the dispatcher's own response).
          setState((prev) => ({ ...(prev ?? {}), ...result.state }) as DebateState);
        }
        return result;
      } catch (err) {
        console.error("Council command dispatch failed", err);
        return undefined;
      }
    },
    [setState],
  );

  // Keep the panel fresh from the server when idle (e.g. after a reload, or a
  // command issued elsewhere). Skipped while a run streams its own state.
  useEffect(() => {
    if (running) return;
    let cancelled = false;
    const sync = async () => {
      try {
        const snapshot = await api.commandState();
        if (cancelled || !snapshot?.state) return;
        setState((prev) => ({ ...(prev ?? {}), ...snapshot.state }) as DebateState);
      } catch {
        // best-effort; the panel still works from streamed state.
      }
    };
    const interval = window.setInterval(sync, 6000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [running, setState]);

  // Frontend actions: let the CopilotKit agent (chat / Realtime voice) drive the
  // same server-side command dispatcher. Handlers only transport — no business
  // logic runs in the browser.
  useCopilotAction({
    name: "askCouncilToClarify",
    description:
      "Ask a finance council role to clarify its position on the decision under review (roles: cfo, treasury, fpna, risk, procurement).",
    parameters: [
      { name: "agent", type: "string", description: "council role id", required: true },
      { name: "question", type: "string", description: "what to clarify", required: true },
    ],
    handler: async ({ agent, question }) => {
      const result = await dispatchCommand({
        type: "clarify",
        agent: String(agent),
        payload: { question: String(question), context: { decision: decision ?? "" } },
        source: "copilot",
      });
      return result?.message ?? "Clarify command dispatched.";
    },
  });

  useCopilotAction({
    name: "challengeCouncilClaim",
    description: "Challenge a specific finance council role to defend or revise a claim with figures.",
    parameters: [
      { name: "agent", type: "string", description: "council role id", required: true },
      { name: "point", type: "string", description: "the claim to challenge", required: true },
    ],
    handler: async ({ agent, point }) => {
      const result = await dispatchCommand({
        type: "challenge_claim",
        agent: String(agent),
        payload: { point: String(point), context: { decision: decision ?? "" } },
        source: "copilot",
      });
      return result?.message ?? "Challenge command dispatched.";
    },
  });

  useCopilotAction({
    name: "requestScenarioFork",
    description:
      "Project the company runway under a what-if scenario by adjusting monthly spend, one-time cost, and/or added monthly revenue.",
    parameters: [
      { name: "label", type: "string", description: "short scenario label", required: false },
      { name: "extra_monthly_spend", type: "number", description: "incremental recurring monthly cost", required: false },
      { name: "one_time_cost", type: "number", description: "upfront one-time cost", required: false },
      { name: "added_monthly_revenue", type: "number", description: "incremental monthly revenue", required: false },
    ],
    handler: async ({ label, extra_monthly_spend, one_time_cost, added_monthly_revenue }) => {
      const result = await dispatchCommand({
        type: "scenario_fork",
        payload: {
          label: label ? String(label) : "Scenario",
          extra_monthly_spend: Number(extra_monthly_spend ?? 0),
          one_time_cost: Number(one_time_cost ?? 0),
          added_monthly_revenue: Number(added_monthly_revenue ?? 0),
        },
        source: "copilot",
      });
      return result?.message ?? "Scenario command dispatched.";
    },
  });

  useCopilotAction({
    name: "pinCouncilEvidence",
    description:
      "Pin supporting evidence to the board record. kind is one of policy, vendor, financial, or custom.",
    parameters: [
      { name: "kind", type: "string", description: "policy|vendor|financial|custom", required: true },
      { name: "query", type: "string", description: "search text, field name, or note", required: true },
    ],
    handler: async ({ kind, query }) => {
      const text = String(query);
      const result = await dispatchCommand({
        type: "pin_evidence",
        payload: {
          kind: String(kind) as "policy" | "vendor" | "financial" | "custom",
          query: text,
          note: text,
          ref: text,
        },
        source: "copilot",
      });
      return result?.message ?? "Pin command dispatched.";
    },
  });

  useCopilotAction({
    name: "exportBoardMemo",
    description: "Assemble and export the board-ready memo once the council has issued a ruling.",
    parameters: [],
    handler: async () => {
      const result = await dispatchCommand({ type: "export_memo", payload: {}, source: "copilot" });
      return result?.message ?? "Export command dispatched.";
    },
  });

  return (
    <main className="flex min-h-full flex-col bg-background">
      <CouncilStatusBar
        healthReady={displayHealthReady}
        learningReport={mounted ? learningReport : undefined}
        nowLabel={nowLabel}
        reliabilityScores={mounted ? reliabilityScores : []}
        sponsorRows={sponsorRows}
      />

      <CouncilHeader
        currentPhase={currentPhase}
        decision={displayDecision}
        healthReady={displayHealthReady}
        running={displayRunning}
        steps={timeline}
      />

      <div className="flex flex-1 flex-col gap-2 p-2 lg:p-3">
        {!displayHealthReady && <PreflightPanel health={health} onRefresh={loadHealth} />}

        <div className="grid min-h-0 flex-1 gap-2 xl:grid-cols-[minmax(0,1fr)_minmax(280px,340px)]">
          <div className="flex min-w-0 flex-col gap-2">
            <CouncilWeb
              agentStatuses={displayAgentStatuses}
              healthReady={displayHealthReady}
              nodeName={displayNodeName}
              onSelectAgent={setSelectedAgentId}
              recommendation={displayRecommendation}
              running={displayRunning}
              selectedAgentId={selectedMember.id}
              started={displayStarted}
              transcript={displayTranscript}
            />

            <TranscriptStream
              transcript={displayTranscript}
              recommendation={displayRecommendation}
              running={displayRunning}
              nodeName={displayNodeName}
              healthReady={displayHealthReady}
              started={displayStarted}
              agentStatuses={displayAgentStatuses}
            />

            <BoardMemo
              recommendation={displayRecommendation}
              decision={displayDecision}
              companyName={companyName}
              reliabilityScores={reliabilityScores}
              running={displayRunning}
              healthReady={displayHealthReady}
              started={displayStarted}
            />
            <ScenarioImpactCard impact={displayRecommendation?.impact} />
          </div>

          <aside className="min-w-0 xl:sticky xl:top-2 xl:self-start">
            <CommandConsole
              className="xl:min-h-[calc(100dvh-10rem)]"
              input={input}
              onInput={setInput}
              onSubmit={submit}
              running={displayRunning}
              healthReady={displayHealthReady}
              started={displayStarted}
              realtime={realtime}
              onStartRealtime={startRealtime}
              onStopRealtime={stopRealtime}
              commands={commands}
              audioRef={realtimeAudioRef}
            />
          </aside>
        </div>
      </div>
    </main>
  );
}
