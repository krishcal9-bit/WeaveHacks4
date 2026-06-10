"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useCoAgent, useCopilotAction, useCopilotChat } from "@copilotkit/react-core";
import { MessageRole, TextMessage } from "@copilotkit/runtime-client-gql";
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
  DebateState,
  OperatorCommand,
  VoiceTranscriptEntry,
} from "@/lib/types";
import { CouncilWeb } from "@/components/decision-room/council-web";
import { CouncilQuestionBanner } from "@/components/decision-room/council-question-banner";
import { BoardMemo } from "@/components/decision-room/board-memo";
import { CommandConsole } from "@/components/decision-room/command-console";
import { CouncilHeader, PreflightPanel } from "@/components/decision-room/council-chrome";
import { InfluencePanel } from "@/components/decision-room/influence-panel";
import { SelfImprovementPanel } from "@/components/decision-room/self-improvement-panel";
import { TranscriptStream } from "@/components/decision-room/transcript-stream";
import { EvidenceDrawer } from "@/components/decision-room/evidence-drawer";
import { AgentInspector } from "@/components/decision-room/agent-inspector";
import { RedisActivityRail } from "@/components/decision-room/activity-rails";
import { Stagger, StaggerItem } from "@/components/motion/stagger";

import { agentBase } from "@/lib/agent-base";
import { connectRealtimeVoice, type RealtimeVoiceHandle, type VoiceTranscriptUpdate } from "@/lib/realtime-voice";
import { useDemoResetListener } from "@/hooks/use-demo-reset";
import { useDeferredHealthReady, useMounted } from "@/lib/use-mounted";

const REQUIRED_CONNECTOR_IDS = [
  "ledger",
  "invoices",
  "vendor_export",
  "crm_opportunities",
  "headcount_plan",
  "security_evidence",
  "board_policy",
] as const;
const LOADED_CONNECTOR_STATUSES = ["imported", "partial", "skipped_unchanged"];

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

// Throttle a fast-changing value so consumers re-render at most once per `ms`.
// Always lands the final value (trailing edge) so the UI ends fully up to date.
// This is what keeps the Decision Room responsive: the AG-UI stream pushes many
// state deltas per second, but the heavy visual tree only needs to repaint a few
// times per second.
function useThrottledValue<T>(value: T, ms: number): T {
  const [throttled, setThrottled] = useState<T>(value);
  const lastRef = useRef<number>(0);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    const now = Date.now();
    const elapsed = now - lastRef.current;
    if (elapsed >= ms) {
      lastRef.current = now;
      setThrottled(value);
      return;
    }
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      lastRef.current = Date.now();
      setThrottled(value);
      timerRef.current = null;
    }, ms - elapsed);
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [value, ms]);

  return throttled;
}

export default function DecisionsPage() {
  const [input, setInput] = useState("");
  const [councilQuestion, setCouncilQuestion] = useState("");
  const [health, setHealth] = useState<HealthView>({ status: "loading", refreshing: true });
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [realtime, setRealtime] = useState<RealtimeView>({ status: "idle", detail: "Realtime 2 voice idle" });
  const [voiceTranscript, setVoiceTranscript] = useState<VoiceTranscriptEntry[]>([]);
  const [activityPulseActive, setActivityPulseActive] = useState(false);

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

  const router = useRouter();
  const { state, setState, running, nodeName } = useCoAgent<DebateState>({ name: "finance_department" });
  const { appendMessage } = useCopilotChat();
  const [stateMirror, setStateMirror] = useState<Partial<DebateState>>({});

  // Throttle the streamed coagent state for DISPLAY so the heavy Decision Room
  // tree repaints a few times per second instead of on every AG-UI delta (the
  // unthrottled stream froze the page). Logic below (submit guards, command sync,
  // copilot actions, dispatchCommand) still uses the LIVE state/running/nodeName.
  const vState = useThrottledValue(state, 250);
  const vRunning = useThrottledValue(running, 250);
  const vNode = useThrottledValue(nodeName, 250);

  // Defensive reads: every field is optional and may arrive incrementally.
  const transcript = useMemo(() => vState?.transcript ?? [], [vState?.transcript]);
  const agentStatuses = useMemo(() => vState?.agent_statuses ?? [], [vState?.agent_statuses]);
  const recommendation = vState?.recommendation;
  const reliabilityScores = useMemo(() => vState?.reliability_scores ?? [], [vState?.reliability_scores]);
  const councilInfluence = vState?.council_influence;
  const agentImprovements = vState?.agent_improvements;
  const commands = vState?.commands;
  const decision = state?.decision;
  const contextState = vState?.context && Object.keys(vState.context).length > 0 ? vState.context : stateMirror.context;
  const redisActivityState = vState?.redis_activity?.length ? vState.redis_activity : stateMirror.redis_activity;
  const pinnedEvidenceState = vState?.pinned_evidence?.length ? vState.pinned_evidence : stateMirror.pinned_evidence;
  const realtimeStatusState =
    vState?.realtime_status && Object.keys(vState.realtime_status).length > 0 ? vState.realtime_status : stateMirror.realtime_status;
  const companyName = contextState?.financials?.name ?? "the company";

  const mounted = useMounted();
  const started = transcript.length > 0 || vRunning;
  const healthReady = health.status === "ready" && health.data?.ready === true;
  const displayHealthReady = useDeferredHealthReady(healthReady);
  const displayTranscript = useMemo(() => (mounted ? transcript : []), [mounted, transcript]);
  const displayRunning = mounted && vRunning;
  const displayNodeName = mounted ? vNode : undefined;
  const displayAgentStatuses = useMemo(() => (mounted ? agentStatuses : []), [mounted, agentStatuses]);
  const displayRecommendation = mounted ? recommendation : undefined;
  const displayDecision = mounted ? decision : undefined;
  const displayStarted = mounted && started;
  const displayPhase = mounted ? vState?.phase : undefined;
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
  // Status + reliability for the inspected seat (rendered in the right column).
  const inspectorStatus = useMemo(
    () => displayAgentStatuses.find((status) => status.id === selectedMember.id),
    [displayAgentStatuses, selectedMember.id],
  );
  const inspectorReliability = useMemo(
    () => (mounted ? reliabilityScores : []).find((score) => score.agent_id === selectedMember.id),
    [mounted, reliabilityScores, selectedMember.id],
  );

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
  // Gate: the council only runs once the operator has uploaded company data.
  // On shortfall, schedule a redirect to the Data tab and return a message.
  // ----------------------------------------------------------------------- //
  const requireCompanyDataOrRedirect = useCallback(async (): Promise<string | null> => {
    let loaded = 0;
    try {
      const inventory = await api.connectors();
      loaded = (inventory.connectors ?? []).filter(
        (connector) =>
          LOADED_CONNECTOR_STATUSES.includes(connector.status ?? "") &&
          (connector.record_count ?? 0) > 0,
      ).length;
    } catch {
      loaded = 0;
    }
    if (loaded >= REQUIRED_CONNECTOR_IDS.length) return null;
    const reason = loaded === 0 ? "empty" : "incomplete";
    window.setTimeout(() => router.push(`/dashboard?need=${reason}`), 900);
    return loaded === 0
      ? "No data uploaded. Add your company files on the Data tab to run the council."
      : "Incomplete data. Finish uploading the required company files on the Data tab.";
  }, [router]);

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
    setCouncilQuestion("");
    setSelectedAgentId(null);
    setVoiceTranscript([]);
    setStateMirror({});
    setActivityPulseActive(false);
    redisActivityCountRef.current = 0;
    setRealtime({ status: "idle", detail: "Realtime 2 voice idle" });
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
            const blockedMessage = await requireCompanyDataOrRedirect();
            if (blockedMessage) return;
            setCouncilQuestion(decision);
            await appendMessage(new TextMessage({ role: MessageRole.User, content: decision }));
          },
        },
      });
    } catch (err) {
      stopRealtime();
      setRealtime({ status: "blocked", detail: err instanceof Error ? err.message : String(err) });
    }
  }, [appendMessage, healthReady, mergeVoiceTranscript, requireCompanyDataOrRedirect, running, stopRealtime]);

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
      const blockedMessage = await requireCompanyDataOrRedirect();
      if (blockedMessage) throw new Error(blockedMessage);
      setInput("");
      // Show the question banner above the council web for the typed/Run path
      // (the voice and copilot-action paths already set this).
      setCouncilQuestion(content);
      await appendMessage(new TextMessage({ role: MessageRole.User, content }));
    },
    [appendMessage, healthReady, running, requireCompanyDataOrRedirect],
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
  }, [dispatchCommand, decision]);

  // Send a full question/decision prompt to the council. Instead of dumping the
  // text into the prompt input, surface it as the animated banner above the
  // council web and stream it to the committee over AG-UI.
  useCopilotAction({
    name: "sendQuestionToCouncil",
    description:
      "Send a question or decision prompt to the full finance council to deliberate on. Use this whenever the operator asks you to put a question to the council, pose a decision, or kick off a debate. The question is shown as a prominent banner above the council web and streamed to the committee — never place it into a text input field.",
    parameters: [
      { name: "question", type: "string", description: "the question or decision prompt to send to the council", required: true },
    ],
    handler: async ({ question }) => {
      const content = String(question ?? "").trim();
      if (!content) return "No question provided.";
      setCouncilQuestion(content);
      if (running) return "The council is already deliberating; the question is shown above the council web.";
      if (!healthReady) return "Strict-live preflight must pass before the council can deliberate.";
      const blockedMessage = await requireCompanyDataOrRedirect();
      if (blockedMessage) return blockedMessage;
      await appendMessage(new TextMessage({ role: MessageRole.User, content }));
      return "Question sent to the council.";
    },
  }, [running, healthReady, requireCompanyDataOrRedirect, appendMessage]);

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
  }, [dispatchCommand, decision]);

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
  }, [dispatchCommand, decision]);

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
  }, [dispatchCommand, decision]);

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
  }, [dispatchCommand]);

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
  }, [dispatchCommand]);

  useCopilotAction({
    name: "exportBoardMemo",
    description: "Assemble and export the board-ready memo once the council has issued a ruling.",
    parameters: [],
    handler: async () => {
      const result = await dispatchCommand({ type: "export_memo", payload: {}, source: "copilot" });
      return result?.message ?? "Export command dispatched.";
    },
  }, [dispatchCommand]);

  return (
    <main className="flex min-h-full flex-col bg-background">
      <CouncilHeader
        currentPhase={currentPhase}
        decision={displayDecision}
        healthReady={displayHealthReady}
        running={displayRunning}
        steps={timeline}
      />

      <div className="flex min-w-0 flex-1 flex-col gap-2 overflow-x-hidden p-2 lg:p-3">
        {!displayHealthReady && <PreflightPanel health={health} onRefresh={loadHealth} />}

        <div className="grid min-h-0 min-w-0 flex-1 gap-2 xl:grid-cols-[minmax(0,1fr)_minmax(280px,340px)]">
          <Stagger className="flex min-w-0 flex-col gap-2">
            {mounted && councilQuestion && (
              <StaggerItem>
                <CouncilQuestionBanner question={councilQuestion} />
              </StaggerItem>
            )}

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
              boardMemo={mounted ? vState?.board_memo : undefined}
              recommendation={displayRecommendation}
              decision={displayDecision}
              companyName={companyName}
              reliabilityScores={reliabilityScores}
              operatorActions={mounted ? vState?.operator_actions : undefined}
              running={displayRunning}
              healthReady={displayHealthReady}
              started={displayStarted}
            />
            </StaggerItem>

            {/* Evidence drawer lives at the bottom of the left column. */}
            <StaggerItem>
            <EvidenceDrawer
              context={displayContext}
              started={displayStarted}
              active={displayActivityPulse}
              pinnedEvidence={displayPinnedEvidence}
            />
            </StaggerItem>
          </Stagger>

          {/* Right column: operator console, the agent inspector, then Redis
              activity which fills the remaining height. min-h-full lets the aside
              be at least the grid row height (so the fill box reaches the bottom)
              but also grow past it — so a long Redis activity list extends the
              page instead of getting an internal scrollbar. */}
          <aside className="flex min-w-0 flex-col gap-2 xl:min-h-full">
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
            <AgentInspector
              member={selectedMember}
              agentStatus={inspectorStatus}
              reliabilityScore={inspectorReliability}
              transcript={displayTranscript}
              recommendation={displayRecommendation}
              redisActivity={displayRedisActivity}
              learningReport={mounted ? vState?.learning_report : undefined}
              nodeName={displayNodeName}
              running={displayRunning}
              healthReady={displayHealthReady}
              started={displayStarted}
            />
            <RedisActivityRail
              activity={displayRedisActivity}
              active={displayActivityPulse}
              className="flex-1"
              bodyClassName="flex-1"
            />
          </aside>
        </div>
      </div>
    </main>
  );
}
