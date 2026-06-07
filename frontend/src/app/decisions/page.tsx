"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCoAgent, useCopilotAction, useCopilotChat } from "@copilotkit/react-core";
import { MessageRole, TextMessage } from "@copilotkit/runtime-client-gql";
import { Database, FileSpreadsheet, Play, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { ROSTER_BY_ID } from "@/lib/agents";
import {
  buildTimeline,
  getCurrentPhaseLabel,
  latestSpeakerId,
  NODE_TO_AGENT,
  type HealthPayload,
  type HealthView,
  type RealtimeView,
} from "@/lib/council";
import type {
  CommandResult,
  CommandState,
  DebateState,
  DemoScenarioPack,
  OperatorCommand,
  VoiceTranscriptEntry,
} from "@/lib/types";
import { CouncilWeb } from "@/components/decision-room/council-web";
import { BoardMemo, ScenarioImpactCard } from "@/components/decision-room/board-memo";
import { CommandConsole } from "@/components/decision-room/command-console";
import { CouncilCommandPanel } from "@/components/council-command-panel";
import { CouncilHeader, PreflightPanel } from "@/components/decision-room/council-chrome";
import { InfluencePanel } from "@/components/decision-room/influence-panel";
import { SelfImprovementPanel } from "@/components/decision-room/self-improvement-panel";
import { TranscriptStream } from "@/components/decision-room/transcript-stream";
import { EvidenceDrawer } from "@/components/decision-room/evidence-drawer";
import { RedisActivityRail } from "@/components/decision-room/activity-rails";
import { Panel, StatusBadge } from "@/components/decision-room/primitives";
import { Stagger, StaggerItem } from "@/components/motion/stagger";
import { cx } from "@/components/ui";

import { agentBase } from "@/lib/agent-base";
import { connectRealtimeVoice, type RealtimeVoiceHandle, type VoiceTranscriptUpdate } from "@/lib/realtime-voice";
import { useDemoResetListener } from "@/hooks/use-demo-reset";
import { useDeferredHealthReady, useMounted } from "@/lib/use-mounted";

function isRealtimeViewStatus(value: unknown): value is RealtimeView["status"] {
  return value === "idle" || value === "connecting" || value === "connected" || value === "blocked";
}

function booleanFlag(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function realtimeViewFromStream(status?: DebateState["realtime_status"]): RealtimeView | undefined {
  if (!status || Object.keys(status).length === 0) return undefined;
  const rawStatus = status.status;
  const viewStatus = isRealtimeViewStatus(rawStatus) ? rawStatus : status.ready === false ? "blocked" : "connected";
  const fallbackDetail = viewStatus === "blocked" ? "Realtime voice is blocked." : "Realtime voice status is live.";

  return {
    status: viewStatus,
    detail: typeof status.detail === "string" && status.detail.trim() ? status.detail : fallbackDetail,
    model: typeof status.model === "string" ? status.model : undefined,
    voice: typeof status.voice === "string" ? status.voice : undefined,
    micMuted: booleanFlag(status.micMuted ?? status.mic_muted),
    listening: booleanFlag(status.listening),
    speaking: booleanFlag(status.speaking),
    processing: booleanFlag(status.processing),
  };
}

export default function DecisionsPage() {
  const [input, setInput] = useState("");
  const [health, setHealth] = useState<HealthView>({ status: "loading", refreshing: true });
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [realtime, setRealtime] = useState<RealtimeView>({ status: "idle", detail: "Realtime 2 voice idle" });
  const [voiceTranscript, setVoiceTranscript] = useState<VoiceTranscriptEntry[]>([]);
  const [activityPulseActive, setActivityPulseActive] = useState(false);
  const [demoScenarios, setDemoScenarios] = useState<DemoScenarioPack[]>([]);
  const [selectedScenarioId, setSelectedScenarioId] = useState<string>("");

  const realtimeAudioRef = useRef<HTMLAudioElement | null>(null);
  const realtimeVoiceRef = useRef<RealtimeVoiceHandle | null>(null);
  const redisActivityCountRef = useRef(0);

  const mergeVoiceTranscript = useCallback((update: VoiceTranscriptUpdate) => {
    const at = new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date());

    setVoiceTranscript((prev) => {
      const index = prev.findIndex((entry) => entry.id === update.id);
      if (index >= 0) {
        const next = [...prev];
        next[index] = {
          ...next[index],
          text: update.text,
          final: update.final,
        };
        return next;
      }
      return [...prev, { id: update.id, role: update.role, text: update.text, final: update.final, at }];
    });
  }, []);

  const { state, setState, running, nodeName } = useCoAgent<DebateState>({ name: "finance_department" });
  const { appendMessage } = useCopilotChat();
  const [stateMirror, setStateMirror] = useState<Partial<DebateState>>({});

  // Defensive reads: every field is optional and may arrive incrementally.
  const transcript = useMemo(() => state?.transcript ?? [], [state?.transcript]);
  const agentStatuses = state?.agent_statuses ?? [];
  const recommendation = state?.recommendation;
  const reliabilityScores = state?.reliability_scores ?? [];
  const councilInfluence = state?.council_influence;
  const agentImprovements = state?.agent_improvements;
  const commands = state?.commands;
  const decision = state?.decision;
  const contextState = state?.context && Object.keys(state.context).length > 0 ? state.context : stateMirror.context;
  const redisActivityState = state?.redis_activity?.length ? state.redis_activity : stateMirror.redis_activity;
  const pinnedEvidenceState = state?.pinned_evidence?.length ? state.pinned_evidence : stateMirror.pinned_evidence;
  const commandQueueState = state?.command_queue?.length ? state.command_queue : stateMirror.command_queue;
  const activeCommandState =
    state?.active_command && Object.keys(state.active_command).length > 0 ? state.active_command : stateMirror.active_command;
  const requestedScenarioState =
    state?.requested_scenario && Object.keys(state.requested_scenario).length > 0 ? state.requested_scenario : stateMirror.requested_scenario;
  const agentFocusState =
    state?.agent_focus && Object.keys(state.agent_focus).length > 0 ? state.agent_focus : stateMirror.agent_focus;
  const phaseControlsState =
    state?.phase_controls && Object.keys(state.phase_controls).length > 0 ? state.phase_controls : stateMirror.phase_controls;
  const exportStatusState =
    state?.export_status && Object.keys(state.export_status).length > 0 ? state.export_status : stateMirror.export_status;
  const commandAuditState = state?.command_audit_log?.length ? state.command_audit_log : stateMirror.command_audit_log;
  const realtimeStatusState =
    state?.realtime_status && Object.keys(state.realtime_status).length > 0 ? state.realtime_status : stateMirror.realtime_status;
  const companyName = contextState?.financials?.name ?? "the company";
  const selectedDemoScenario = useMemo(
    () => demoScenarios.find((scenario) => scenario.id === selectedScenarioId) ?? demoScenarios[0],
    [demoScenarios, selectedScenarioId],
  );

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
  const displayContext = mounted ? contextState : undefined;
  const displayRedisActivity = useMemo(() => {
    if (!mounted) return [];
    const items = redisActivityState ?? [];
    return items.length > 48 ? items.slice(-48) : items;
  }, [mounted, redisActivityState]);
  const displayPinnedEvidence = mounted ? (pinnedEvidenceState ?? []) : [];
  const displayActivityPulse = displayRunning || activityPulseActive;
  const displayRealtime = useMemo(() => {
    const streamedRealtime = realtimeViewFromStream(realtimeStatusState);
    if (!mounted || realtime.status !== "idle" || !streamedRealtime) return realtime;
    return streamedRealtime;
  }, [mounted, realtime, realtimeStatusState]);
  const displayCommandState = useMemo<CommandState>(
    () => ({
      command_queue: mounted ? (commandQueueState ?? []) : [],
      active_command: mounted ? (activeCommandState ?? {}) : {},
      pinned_evidence: mounted ? (pinnedEvidenceState ?? []) : [],
      requested_scenario: mounted ? (requestedScenarioState ?? {}) : {},
      agent_focus: mounted ? (agentFocusState ?? {}) : {},
      phase_controls: mounted ? (phaseControlsState ?? { paused: false }) : { paused: false },
      export_status: mounted ? (exportStatusState ?? { ready: false }) : { ready: false },
      command_audit_log: mounted ? (commandAuditState ?? []) : [],
    }),
    [
      activeCommandState,
      agentFocusState,
      commandAuditState,
      commandQueueState,
      exportStatusState,
      mounted,
      phaseControlsState,
      pinnedEvidenceState,
      requestedScenarioState,
    ],
  );

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

  // Resolve the inspected seat: explicit selection -> active roster seat -> last speaker -> CFO.
  const activeAgentId = NODE_TO_AGENT[nodeName ?? ""];
  const activeRosterId = activeAgentId && ROSTER_BY_ID[activeAgentId] ? activeAgentId : undefined;
  const candidateId = selectedAgentId ?? (running ? activeRosterId : undefined) ?? latestSpeakerId(transcript) ?? "cfo";
  const selectedMember = ROSTER_BY_ID[candidateId] ?? ROSTER_BY_ID.cfo;

  // ----------------------------------------------------------------------- //
  // Health polling (every 15s): locks submissions until strict-live green.
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

  const loadScenarios = useCallback(async () => {
    try {
      const payload = await api.demoScenarios();
      setDemoScenarios(payload.scenarios ?? []);
      setSelectedScenarioId((current) => current || payload.scenarios?.[0]?.id || "");
    } catch {
      setDemoScenarios([]);
    }
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadScenarios(), 0);
    return () => window.clearTimeout(timeout);
  }, [loadScenarios]);

  useEffect(() => {
    if (!mounted) return;
    const count = displayRedisActivity.length;
    const previous = redisActivityCountRef.current;
    redisActivityCountRef.current = count;
    if (count <= previous || count === 0) return;

    setActivityPulseActive(true);
    const timeout = window.setTimeout(() => setActivityPulseActive(false), 2600);
    return () => window.clearTimeout(timeout);
  }, [displayRedisActivity.length, mounted]);

  // ----------------------------------------------------------------------- //
  // OpenAI Realtime 2 voice (WebRTC): gated behind strict-live preflight.
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

  useDemoResetListener(() => {
    stopRealtime();
    setInput("");
    setSelectedAgentId(null);
    setVoiceTranscript([]);
    setStateMirror({});
    setActivityPulseActive(false);
    redisActivityCountRef.current = 0;
    setRealtime({ status: "idle", detail: "Realtime 2 voice idle" });
    void loadScenarios();
  });

  const startRealtime = useCallback(async () => {
    if (!healthReady) {
      setRealtime({ status: "blocked", detail: "Strict live preflight must pass before voice starts." });
      return;
    }
    const audioEl = realtimeAudioRef.current;
    if (!audioEl) {
      setRealtime({ status: "blocked", detail: "Voice audio element not ready - refresh and try again." });
      return;
    }

    stopRealtime();

    try {
      realtimeVoiceRef.current = await connectRealtimeVoice({
        agentBase: agentBase(),
        audioEl,
        callbacks: {
          onStatus: setRealtime,
          onTranscript: mergeVoiceTranscript,
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
  }, [appendMessage, healthReady, mergeVoiceTranscript, running, stopRealtime]);

  const onVoiceButton = useCallback(() => {
    if (realtime.status === "connected") {
      const handle = realtimeVoiceRef.current;
      if (!handle) return;
      const nextMuted = !handle.isMicMuted();
      handle.setMicMuted(nextMuted);
      return;
    }
    if (realtime.status !== "connecting") {
      void startRealtime();
    }
  }, [realtime.status, startRealtime]);

  // ----------------------------------------------------------------------- //
  // Submission: strict-gated, streams via CopilotKit / AG-UI.
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
  // AG-UI command-and-control: operator steering of the live council.
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
          // state (never fabricated: this is the dispatcher's own response).
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
        setStateMirror(snapshot.state as Partial<DebateState>);
        setState((prev) => ({ ...(prev ?? {}), ...snapshot.state }) as DebateState);
      } catch {
        // best-effort; the panel still works from streamed state.
      }
    };
    void sync();
    const interval = window.setInterval(sync, 6000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [running, setState]);

  // Frontend actions: let the CopilotKit agent (chat / Realtime voice) drive the
  // same server-side command dispatcher. Handlers only transport: no business
  // logic runs in the browser.
  useCopilotAction({
    name: "askCouncilToClarify",
    description:
      "Ask a finance council role to clarify through its unique mandate: CFO chair synthesis, Treasury liquidity mechanics, FP&A forecastability, Risk controls, Procurement vendor terms, or Reliability evaluator scorecard.",
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
    description:
      "Challenge a specific finance council role to defend or revise a claim using its own lane: cash timing, forecast assumptions, controls/policy, vendor terms, chair synthesis, or evaluator scorecard.",
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
    name: "defendCouncilPosition",
    description:
      "Ask a targeted council role to defend its position through its mandate, not generic finance language.",
    parameters: [
      { name: "agent", type: "string", description: "council role id", required: true },
      { name: "point", type: "string", description: "optional focus for the defense", required: false },
    ],
    handler: async ({ agent, point }) => {
      const result = await dispatchCommand({
        type: "defend_position",
        agent: String(agent),
        payload: { point: point ? String(point) : undefined, context: { decision: decision ?? "" } },
        source: "copilot",
      });
      return result?.message ?? "Defend command dispatched.";
    },
  });

  useCopilotAction({
    name: "rerunCouncilRole",
    description:
      "Rerun one council role's analysis from scratch using that role's mandate and evidence lens.",
    parameters: [
      { name: "agent", type: "string", description: "council role id", required: true },
      { name: "reason", type: "string", description: "why to rerun or what to focus on", required: false },
    ],
    handler: async ({ agent, reason }) => {
      const result = await dispatchCommand({
        type: "rerun_role",
        agent: String(agent),
        payload: { reason: reason ? String(reason) : undefined, context: { decision: decision ?? "" } },
        source: "copilot",
      });
      return result?.message ?? "Rerun command dispatched.";
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
          <Stagger className="flex min-w-0 flex-col gap-2">
            <StaggerItem>
            <CouncilWeb
              agentStatuses={displayAgentStatuses}
              councilInfluence={mounted ? councilInfluence : undefined}
              healthReady={displayHealthReady}
              nodeName={displayNodeName}
              onSelectAgent={setSelectedAgentId}
              recommendation={displayRecommendation}
              running={displayRunning}
              selectedAgentId={selectedMember.id}
              started={displayStarted}
              transcript={displayTranscript}
            />
            </StaggerItem>

            <StaggerItem>
            <TranscriptStream
              transcript={displayTranscript}
              recommendation={displayRecommendation}
              running={displayRunning}
              nodeName={displayNodeName}
              healthReady={displayHealthReady}
              started={displayStarted}
              agentStatuses={displayAgentStatuses}
            />
            </StaggerItem>

            <StaggerItem>
            <InfluencePanel
              councilInfluence={mounted ? councilInfluence : undefined}
              reliabilityScores={mounted ? reliabilityScores : []}
              running={displayRunning}
              started={displayStarted}
              phase={displayPhase}
            />
            </StaggerItem>

            <StaggerItem>
            <SelfImprovementPanel
              agentImprovements={mounted ? agentImprovements : undefined}
              reliabilityScores={mounted ? reliabilityScores : []}
              running={displayRunning}
              started={displayStarted}
            />
            </StaggerItem>

            <StaggerItem>
            <BoardMemo
              boardMemo={mounted ? state?.board_memo : undefined}
              recommendation={displayRecommendation}
              decision={displayDecision}
              companyName={companyName}
              reliabilityScores={reliabilityScores}
              operatorActions={mounted ? state?.operator_actions : undefined}
              running={displayRunning}
              healthReady={displayHealthReady}
              started={displayStarted}
            />
            </StaggerItem>

            <StaggerItem>
            <ScenarioImpactCard impact={displayRecommendation?.impact} />
            </StaggerItem>
          </Stagger>

          <aside className="room-scroll flex min-w-0 flex-col gap-2 xl:sticky xl:top-2 xl:max-h-[calc(100dvh-1rem)] xl:self-start xl:overflow-y-auto">
            <DemoScenarioSelector
              scenarios={demoScenarios}
              selected={selectedDemoScenario}
              selectedId={selectedScenarioId}
              onSelect={setSelectedScenarioId}
              onUse={(scenario) => setInput(scenario.decision_prompt)}
              onRun={(scenario) => void submit(scenario.decision_prompt)}
              running={displayRunning}
              healthReady={displayHealthReady}
            />
            <CommandConsole
              input={input}
              onInput={setInput}
              onSubmit={submit}
              running={displayRunning}
              healthReady={displayHealthReady}
              started={displayStarted}
              realtime={displayRealtime}
              onVoiceButton={onVoiceButton}
              voiceTranscript={voiceTranscript}
              commands={commands}
              audioRef={realtimeAudioRef}
            />
            <CouncilCommandPanel
              healthReady={displayHealthReady}
              running={displayRunning}
              decision={displayDecision}
              recommendation={displayRecommendation}
              transcript={displayTranscript}
              commandState={displayCommandState}
              dispatch={dispatchCommand}
            />
            <EvidenceDrawer
              context={displayContext}
              started={displayStarted}
              active={displayActivityPulse}
              pinnedEvidence={displayPinnedEvidence}
            />
            <RedisActivityRail activity={displayRedisActivity} active={displayActivityPulse} />
          </aside>
        </div>
      </div>
    </main>
  );
}

function sourceLabel(value: string) {
  return value.replace(/_/g, " ");
}

function DemoScenarioSelector({
  scenarios,
  selected,
  selectedId,
  onSelect,
  onUse,
  onRun,
  running,
  healthReady,
}: {
  scenarios: DemoScenarioPack[];
  selected?: DemoScenarioPack;
  selectedId: string;
  onSelect: (id: string) => void;
  onUse: (scenario: DemoScenarioPack) => void;
  onRun: (scenario: DemoScenarioPack) => void;
  running: boolean;
  healthReady: boolean;
}) {
  const disabled = !selected || running;
  const runDisabled = disabled || !healthReady;
  const sourcePreview = selected?.sources?.slice(0, 4) ?? [];

  return (
    <Panel
      title="Messy scenarios"
      icon={Sparkles}
      count={scenarios.length || undefined}
      className="shrink-0"
      bodyClassName="min-w-0 space-y-3"
    >
      {scenarios.length === 0 ? (
        <div className="rounded-md border border-border bg-surface-muted p-3 text-[12px] text-muted-foreground">
          Scenario examples load from Redis when the agent service is available.
        </div>
      ) : (
        <>
          <label className="block text-[11px] font-semibold uppercase tracking-[0.08em] text-subtle-foreground" htmlFor="demo-scenario">
            Council case
          </label>
          <select
            id="demo-scenario"
            value={selected?.id ?? selectedId}
            onChange={(event) => onSelect(event.target.value)}
            className="h-9 w-full rounded-md border border-border bg-background px-2.5 text-[12px] font-medium text-foreground outline-none transition-colors focus:border-accent"
          >
            {scenarios.map((scenario) => (
              <option key={scenario.id} value={scenario.id}>
                {scenario.title}
              </option>
            ))}
          </select>

          {selected && (
            <div className="space-y-3">
              <p className="text-[12px] leading-5 text-muted-foreground">{selected.description}</p>

              <div className="flex flex-wrap gap-1.5">
                <StatusBadge tone="info" icon={Database}>
                  {selected.source_count} sources
                </StatusBadge>
                <StatusBadge tone="warning" icon={FileSpreadsheet}>
                  {selected.messy_input_count} messy fields
                </StatusBadge>
              </div>

              <div className="grid min-w-0 gap-1.5">
                {sourcePreview.map((source) => (
                  <div key={`${selected.id}-${source.source_type}`} className="min-w-0 rounded-md border border-border bg-surface-muted px-2.5 py-2">
                    <div className="flex min-w-0 items-center justify-between gap-2">
                      <span className="min-w-0 truncate text-[12px] font-semibold text-foreground">{sourceLabel(source.source_type)}</span>
                      <span className="shrink-0 font-mono text-[10px] text-subtle-foreground">{source.record_count} rows</span>
                    </div>
                    <div className="mt-1 min-w-0 truncate text-[11px] text-muted-foreground">
                      {source.source_system} · {(source.messy_fields ?? []).slice(0, 2).join("; ")}
                    </div>
                  </div>
                ))}
              </div>

              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => selected && onUse(selected)}
                  className={cx(
                    "inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-md border border-border bg-surface px-2 text-[12px] font-semibold text-foreground transition-colors hover:bg-surface-muted",
                    disabled && "cursor-not-allowed opacity-50 hover:bg-surface",
                  )}
                >
                  <FileSpreadsheet className="h-3.5 w-3.5" strokeWidth={2.25} />
                  Fill prompt
                </button>
                <button
                  type="button"
                  disabled={runDisabled}
                  onClick={() => selected && onRun(selected)}
                  className={cx(
                    "inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-md bg-accent px-2 text-[12px] font-semibold text-accent-foreground transition-colors hover:brightness-95",
                    runDisabled && "cursor-not-allowed opacity-50 hover:brightness-100",
                  )}
                >
                  <Play className="h-3.5 w-3.5" strokeWidth={2.25} />
                  Run
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
